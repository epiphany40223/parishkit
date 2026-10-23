"""Parallel CI coverage with complete, same-source execution evidence.

Each shard runs against its own PostgreSQL/Valkey cluster, on separate CI
runners or isolated local disposable services. Never run shards concurrently
against a shared cluster: SQL roles and schemas are cluster-wide test fixtures.
A CI job packs several shards onto one runner (`run_job`), each with its own
service pair on its own ports. The ordinary quality command remains the
serial, all-in-one developer gate.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

from .arguments import StewardshipArgumentParser
from .quality import FLOOR, coverage_percentages, load_scope
from .quality_pytest import baseline_receipt
from .quality_select import build_map
from .quality_sharding import baseline_partition, partition, tree_digest

DATABASE_TESTS = "tests/stewardship/database"
# The credential-free baseline: the whole suite under the test settings,
# which skip the database tests.
BASELINE_TESTS = "tests"
# Parts the baseline is split into, run at once beside partition one's
# database tests, each with its own SHARD_TIMEOUT (#1205). In October 2026
# the whole baseline took 765 to 1,310 s depending on the runner, and gained
# about 900 tests a day. By 2026-10-23 each of two parts took 1,284 to
# 1,549 s, so there are three (#2065). Job one holds only partition one, with
# half a database share (quality_sharding.FIRST_SHARE), so three parts make
# four pytest processes on its 4 vCPUs but only one PostgreSQL cluster. When a
# part nears its deadline, raise this; combine requires every part's evidence.
BASELINE_PARTS = 3
# Collecting the whole baseline took about 35 s locally for 27,677 tests.
BASELINE_COLLECTION_SECONDS = 5 * 60
# Partitions one CI job runs concurrently, each against its own PostgreSQL/
# Valkey pair (issue #625). This is the one tuning knob: the contract tests
# derive the workflow's job matrix and its service pairs from it, and fail
# naming what to change. Three per job keeps a full run's PostgreSQL group to
# ten of the account's twenty concurrent job slots for twenty-eight
# partitions (#625, #1200, #1529, #2065). It also fits a 4 vCPU / 16 GB hosted
# runner: the partitions spend much of their time waiting on PostgreSQL and
# on real lease and drain deadlines, and three 2 GiB tmpfs clusters plus
# three pytest processes stay well inside 16 GB. Job one holds partition one
# and the BASELINE_PARTS baseline parts, which need no cluster.
PARTITIONS_PER_JOB = 3
# Each pytest child's deadline: a partition's database run, and each
# baseline part beside partition one's. Packed fourteen ways, partitions took
# 1,120 to 1,690 s and the baseline 1,310 to 1,330 s in October 2026 (#1200);
# sixteen partitions brought the heaviest to about 24 minutes. By
# 2026-10-17 sixteen took 825 to 1,757 s, mostly by runner, and two were
# killed here, so the workflow ran twenty-two, projected at most about
# 1,330 s (#1529). By 2026-10-23 twenty-two took 910 to 1,720 s, again by
# runner, so it runs twenty-eight, projected at 1,330 to 1,430 s on that
# day's slow runners (#2065). The margin absorbs slow runners, not a slower
# suite: when a partition nears it, add partitions; when a baseline part
# does, add parts (BASELINE_PARTS).
SHARD_TIMEOUT = 30 * 60
# CI's service pairs publish host ports below Linux's ephemeral range
# (32768-60999). Inside it, any outbound connection open on the host (for
# example during the service image pulls) can hold the port at the moment
# Docker binds it, and the job fails before any step (#2085). The local defaults (55432 and
# 56379 in the test settings) stay as they are: `job` is CI-only and always
# sets both ports. Valkey port 56380 stays unused: the unavailable-service
# tests rely on nothing listening there.
POSTGRES_PORT = 25432
VALKEY_PORT = 26379
RESERVED_VALKEY_PORT = 56380
# The per-test stack dump only diagnoses a hang; SHARD_TIMEOUT is what fails
# one. Keep it well above the longest legitimate case and well below the shard
# deadline, or it can never fire first. The contract test derives that lower
# bound from the measured hints in quality_sharding, so a slower hint forces
# this value to be reconsidered. The 5,000-Family reference-load test has
# completed in 77 to 120.1 seconds depending on the hosted runner. Twice the
# former 120-second dump fired during it: the traceback was cut off mid-line
# and the child died from SIGSEGV with every test passing. Both were killed at
# that mark, so "over 120 seconds" is only a lower bound on the slowest run.
# Raising the threshold avoids that trigger; it does not make the dump safe, so
# a slower runner or a genuinely hung threaded test may still end that way.
# `child_status` names the signal when it does.
STACK_DUMP_SECONDS = 5 * 60


def environment():
    """Exclude ambient pytest/coverage selection while flushing child output."""
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COVERAGE_", "COV_CORE_"))
    }
    result["PYTHONUNBUFFERED"] = "1"
    return result


def outside(root, path):
    """Require external artifacts so generated measurements cannot be committed."""
    result = path.resolve()
    if result.is_relative_to(root):
        raise ValueError("CI artifacts must be outside the repository")
    return result


def collection(root, target, *options, timeout):
    """Independently collect ``target``'s node IDs without starting services.

    ``options`` are extra pytest options, such as ``--require-no-skips``.
    The IDs come back sorted, and a duplicate or empty collection fails.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--ds=parishkit.stewardship.settings.test",
            "--collect-only",
            *options,
            "--collection-manifest",
            "-q",
            target,
        ],
        cwd=root,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        # This is the credential-free test collector, not runtime/provider I/O.
        # Keep its import/collection failure visible without dumping full logs.
        print((result.stdout + result.stderr)[-12000:], file=sys.stderr)
        result.check_returncode()
    prefix = "PARISHKIT_TEST_NODEIDS="
    manifests = [
        line[len(prefix) :]
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]
    if len(manifests) != 1:
        raise ValueError("Missing unique collection manifest")
    nodes = json.loads(manifests[0])
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(not isinstance(node, str) for node in nodes)
    ):
        raise ValueError("Invalid test collection")
    partition(nodes, 1, 1)
    return sorted(nodes)


def database_collection(root):
    """Independently collect the full database universe without starting services."""
    nodes = collection(root, DATABASE_TESTS, "--require-no-skips", timeout=120)
    if any(not node.startswith(DATABASE_TESTS + "/") for node in nodes):
        raise ValueError("Invalid database collection")
    return nodes


def baseline_collection(root):
    """Independently collect the whole unsplit non-database baseline (#1205)."""
    return collection(root, BASELINE_TESTS, timeout=BASELINE_COLLECTION_SECONDS)


def baseline_evidence(directory, part):
    """Where baseline part ``part`` writes its execution receipt."""
    return directory / f"baseline-{part}.json"


def baseline_label(part):
    """Name a baseline part in log prefixes and timeout messages."""
    return f"baseline {part}/{BASELINE_PARTS}"


def child_status(result):
    """Return a shell-safe status, naming a fatal signal instead of hiding it.

    A negative return code passed to SystemExit surfaces as an unexplained
    value such as 245. Say what killed the child, and that a traceback cut off
    just above is the known stack-dump hazard rather than a test failure.
    """
    if result.returncode < 0:
        try:
            name = signal.Signals(-result.returncode).name
        except ValueError:
            name = f"signal {-result.returncode}"
        print(
            f"CI shard child was killed by {name}. A stack dump cut off above "
            "this line means the hang diagnostic itself crashed the interpreter.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return result.returncode


# After a timed-out child is sent SIGABRT, how long its faulthandler may take
# to write every thread's stack before the child is killed outright.
ABORT_GRACE_SECONDS = 10


def run_child(
    arguments, *, phase, timeout, prefix=None, lock=None, on_start=None, **options
):
    """Run one shard pytest child; at its deadline, dump its stacks and say so.

    ``subprocess.run(timeout=...)`` SIGKILLs a late child, so no stack dump
    runs and the job log showed only "incomplete or invalid" (#961). This
    sends SIGABRT first: pytest has faulthandler enabled for fatal signals, so
    the child writes every thread's stack, naming the running test, to the
    job log. A child still alive after ABORT_GRACE_SECONDS is killed. Before
    re-raising ``TimeoutExpired``, it prints which ``phase`` was stopped, its
    limit and the elapsed time. Any other interruption kills the child, as
    ``subprocess.run`` does.

    With a ``prefix`` (and the ``lock`` its sibling children share), the
    child's output, stack dump included, is relayed whole line by line with
    that prefix, so children running at once never splice their partial
    progress lines together (#1200). ``on_start``, if given, is called with
    the started ``Popen``, so a sibling can stop this child early.
    """
    started = time.monotonic()
    if prefix is not None:
        options |= {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    process = subprocess.Popen(arguments, **options)
    if on_start is not None:
        on_start(process)
    thread = None
    if prefix is not None:
        thread = threading.Thread(
            target=relay, args=(process.stdout, prefix, lock), daemon=True
        )
        thread.start()

    def drained():
        """Let the relay finish the child's last lines, such as its stack dump."""
        if thread is not None:
            thread.join(timeout=RELAY_DRAIN_SECONDS)

    try:
        code = process.wait(timeout=timeout)
        drained()
        return subprocess.CompletedProcess(arguments, code)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGABRT)
        try:
            process.wait(timeout=ABORT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        drained()
        print(
            f"ERROR: CI shard timeout: stopped the {phase} pytest run after "
            f"{time.monotonic() - started:.0f}s (limit {timeout:.0f}s). Its "
            "thread stacks are dumped above; in the database run, the last CI_PROGRESS "
            "START without an END also names the test that was running.",
            file=sys.stderr,
            flush=True,
        )
        raise
    except BaseException:
        process.kill()
        process.wait()
        raise


def coverage_arguments(scope):
    """Branch coverage of the scope, with per-test contexts for the test map."""
    return [
        "--cov-branch",
        *(f"--cov={module}" for module in scope.modules),
        "--cov-report=",
        "--cov-context=test",
    ]


def run_shard(root, output, index, count, basetemp=None, *, coverage=True, select=None):
    """Run one partition and, on shard one only, the baseline parts; require success.

    ``basetemp``, when given, is pytest's private temporary root, so packed
    partitions on one runner never share or prune each other's directories.
    ``coverage=False`` (an "affected" run) skips measurement, and ``select``
    (a ``quality_select`` file, which implies no coverage) partitions only
    the selected tests. Either way the receipt says so, and ``combine``,
    which full runs and release evidence need, refuses it.
    """
    partition([], index, count)
    coverage = coverage and select is None
    scope = load_scope(root)
    digest = tree_digest(root)
    output = outside(root, output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    data = output / "coverage.data"
    env = environment()
    if coverage:
        env["COVERAGE_FILE"] = str(data)
        # Per-test contexts feed the test map; coverage's sys.monitoring core
        # (the default from Python 3.14) records them incompletely.
        env["COVERAGE_CORE"] = "ctrace"
    arguments = [
        sys.executable,
        "-m",
        "pytest",
        *(coverage_arguments(scope) if coverage else []),
        "-p",
        "no:cacheprovider",
        "--durations=20",
        "-o",
        f"faulthandler_timeout={STACK_DUMP_SECONDS}",
    ]
    basetemp = outside(root, basetemp) if basetemp else None
    database = [
        *arguments,
        *([f"--basetemp={basetemp}"] if basetemp else []),
        "--ds=parishkit.stewardship.settings.database_test",
        "--require-postgresql-tests",
        "--require-no-skips",
        f"--ci-shard={index}/{count}",
        f"--ci-evidence={output / 'tests.json'}",
        *([f"--ci-select={Path(select).resolve()}"] if select else []),
        DATABASE_TESTS,
    ]
    if index == 1:
        # pytest empties its temporary root when it starts, so each baseline
        # part, running beside the others and the database tests, needs one
        # of its own.
        baselines = [
            [
                *arguments,
                *([f"--basetemp={basetemp}-baseline-{part}"] if basetemp else []),
                "--ds=parishkit.stewardship.settings.test",
                f"--ci-baseline={part}/{BASELINE_PARTS}",
                f"--ci-evidence={baseline_evidence(output, part)}",
                BASELINE_TESTS,
            ]
            for part in range(1, BASELINE_PARTS + 1)
        ]
        status = run_with_baseline(root, env, database, baselines, data, coverage)
    else:
        status = child_status(
            run_child(
                database, phase="database", cwd=root, env=env, timeout=SHARD_TIMEOUT
            )
        )
    if status:
        return status
    if tree_digest(root) != digest:
        raise ValueError("Repository changed during CI measurement")
    evidence = json.loads((output / "tests.json").read_text())
    universe = evidence["universe"]
    pool = evidence["subset"] if select else universe
    expected = partition(pool, index, count)
    if (
        not (expected or select)
        or not set(pool) <= set(universe)
        or evidence["selected"] != expected
        or evidence["completed"] != expected
    ):
        raise ValueError("Incomplete CI partition")
    baseline_tests = check_baseline(output) if index == 1 else 0
    parts = range(1, BASELINE_PARTS + 1) if index == 1 else ()
    receipt = {
        "schema": 2,
        "index": index,
        "count": count,
        "baseline": index == 1,
        # Each baseline part's receipt, by digest; combine checks them all.
        "baseline_sha256": [
            hashlib.sha256(baseline_evidence(output, part).read_bytes()).hexdigest()
            for part in parts
        ],
        "coverage": coverage,
        "root": str(root),
        "tree": digest,
        "data_sha256": (
            hashlib.sha256(data.read_bytes()).hexdigest() if coverage else None
        ),
        "tests_sha256": hashlib.sha256(
            (output / "tests.json").read_bytes()
        ).hexdigest(),
    }
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream)
    message = f"CI shard {index}/{count}: {len(expected):,} database tests passed"
    if baseline_tests:
        message += (
            f"; the baseline ran {baseline_tests:,} tests in {BASELINE_PARTS} parts"
        )
    if select:
        message += (
            f"; the selection runs {len(pool):,} of {len(universe):,} "
            f"and skips {len(universe) - len(pool):,}"
        )
        summarize(message)
    print(message, flush=True)
    return 0


def run_with_baseline(root, env, database, baselines, data, coverage):
    """Run partition one's database tests and the baseline parts at once.

    Return 0 or a failing status. The non-database baseline once ran first,
    sharing one SHARD_TIMEOUT with partition one's database tests. As both
    grew, that partition reached the deadline (#961, #1200), so each child
    now has its own SHARD_TIMEOUT and its own timeout message; then the
    baseline itself neared its deadline, so it is split into the commands in
    ``baselines``, one per part (#1205). Each part measures into a separate
    coverage file, merged into ``data`` only when every child passes, so the
    receipt still hashes one complete coverage file. Each child's output is
    relayed whole, line by line, a part's tagged ``[baseline N/K]``. A
    timeout in any child is raised once all have stopped. Any other failure
    of the database side (an interrupt, a child that cannot start) stops the
    baseline parts at once rather than leaving them to run out their
    deadlines.
    """
    extras = [
        data.with_name(f"baseline-{part}.coverage")
        for part in range(1, len(baselines) + 1)
    ]
    lock = threading.Lock()
    # The baseline parts' Popens, once started. If the database side fails
    # first, ``cancel`` makes a part stop as soon as it starts.
    processes = []
    cancel = threading.Event()
    # The parts' threads append and the database side's failure path reads.
    guard = threading.Lock()

    def started(process):
        """Record a baseline child; stop it at once if already cancelled."""
        with guard:
            processes.append(process)
            if cancel.is_set():
                process.kill()

    with ThreadPoolExecutor(max_workers=len(baselines)) as pool:
        later = []
        for part, extra in enumerate(extras, start=1):
            part_env = dict(env)
            if coverage:
                part_env["COVERAGE_FILE"] = str(extra)
            later.append(
                pool.submit(
                    run_child,
                    baselines[part - 1],
                    phase=baseline_label(part),
                    cwd=root,
                    env=part_env,
                    timeout=SHARD_TIMEOUT,
                    prefix=f"[{baseline_label(part)}] ",
                    lock=lock,
                    on_start=started,
                )
            )
        try:
            result = run_child(
                database,
                phase="database",
                cwd=root,
                env=env,
                timeout=SHARD_TIMEOUT,
                prefix="",
                lock=lock,
            )
        except subprocess.TimeoutExpired:
            # Already logged; the baseline parts finish or time out on their own.
            raise
        except BaseException:
            with guard:
                cancel.set()
                for process in processes:
                    process.kill()
            print(
                "Stopping the baseline: the database run did not finish.",
                file=sys.stderr,
                flush=True,
            )
            raise
        # Name every failure (a signal, say) before returning any status.
        status = [child_status(future.result()) for future in later]
        status.append(child_status(result))
    if any(status):
        return next(code for code in status if code)
    if coverage:
        for extra in extras:
            merge_coverage(data, extra)
    return 0


def check_baseline(output):
    """Require every baseline part's receipt in ``output``; return the test count.

    Each part's pytest run wrote its receipt only after completing exactly
    its share of the suite it collected (``quality_pytest``). Here, before
    the shard's own receipt, the parts must agree on that suite, and their
    shares must add up to it. ``combine`` later proves the shares are the
    exact, disjoint split of an independent collection.
    """
    receipts = [
        json.loads(baseline_evidence(output, part).read_text())
        for part in range(1, BASELINE_PARTS + 1)
    ]
    universe = receipts[0]["universe"]
    if (
        any(
            receipt["part"] != part
            or receipt["parts"] != BASELINE_PARTS
            or receipt["universe"] != universe
            or receipt["completed"] != receipt["selected"]
            or not receipt["selected"]["count"]
            for part, receipt in enumerate(receipts, start=1)
        )
        or sum(receipt["selected"]["count"] for receipt in receipts)
        != universe["count"]
    ):
        raise ValueError("Incomplete CI baseline")
    return universe["count"]


def merge_coverage(data, extra):
    """Merge the coverage file ``extra`` (with its test contexts) into ``data``."""
    merged = CoverageData(basename=str(data))
    merged.read()
    other = CoverageData(basename=str(extra))
    other.read()
    if not other.measured_files():
        raise ValueError("The baseline recorded no coverage")
    merged.update(other)
    merged.write()
    extra.unlink()


def summarize(line):
    """Append one line to the GitHub Actions job summary, when there is one."""
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"- {line}\n")


def job_count(count, per_job=PARTITIONS_PER_JOB):
    """Return how many packed CI jobs run ``count`` partitions."""
    return -(-count // per_job)


def job_partitions(job, count, per_job=PARTITIONS_PER_JOB):
    """Return the partition indexes one packed CI job runs, in order.

    Jobs are numbered from one. When the partitions do not divide evenly, the
    first job takes the short remainder: partition one runs the CPU-heavy
    non-database baseline beside its database tests, so its runner should
    hold the fewest other partitions. Every partition belongs to exactly one
    job.
    """
    partition([], 1, count)
    jobs = job_count(count, per_job)
    if type(job) is not int or not 1 <= job <= jobs:
        raise ValueError("Invalid CI job for this partition count")
    first = max(1, count - (jobs - job + 1) * per_job + 1)
    return list(range(first, count - (jobs - job) * per_job + 1))


def slot_ports(slot):
    """Map a packed job's zero-based slot to its own service pair's ports."""
    valkey = VALKEY_PORT + slot
    if valkey >= RESERVED_VALKEY_PORT:
        valkey += 1
    return {
        "PARISHKIT_TEST_POSTGRES_PORT": str(POSTGRES_PORT + slot),
        "PARISHKIT_TEST_VALKEY_PORT": str(valkey),
    }


# After a child exits, how long its relay may keep reading. A grandchild that
# outlives the child and still holds the pipe must not stall the job until
# its time limit; the relay is a daemon thread, so it never blocks exit.
RELAY_DRAIN_SECONDS = 30


def relay(stream, prefix, lock):
    """Copy a child's output lines live, each tagged with its partition.

    Lines are relayed as they arrive rather than replayed at the end, so a
    job the runner kills at its time limit still shows each partition's last
    CI_PROGRESS record. The lock keeps concurrent lines whole.
    """
    for line in iter(stream.readline, b""):
        text = line.decode("utf-8", "replace").rstrip("\n")
        with lock:
            print(prefix + text, flush=True)


def stop_children(children):
    """Terminate, then if needed kill, children still running; say so."""
    for index, process, _ in children:
        if process.poll() is None:
            print(f"Stopping CI partition {index}", file=sys.stderr, flush=True)
            process.terminate()
    for _, process, _ in children:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def terminated(signum, frame):
    """Turn SIGTERM into an exception so run_job can stop its children."""
    raise SystemExit(128 + signum)


def run_job(root, output, job, count, *, coverage=True, select=None):
    """Run one CI job's partitions concurrently and require every one to pass.

    Each partition is an ordinary `shard` child with its own deadline,
    receipt and output directory (``partition-N`` under ``output``), pointed
    at its own PostgreSQL/Valkey pair by port and given its own pytest
    temporary root beside ``output``. The combiner later reads those
    directories exactly as it reads single-partition artifacts. ``coverage``
    and ``select`` pass through to every partition (see ``run_shard``). If
    starting a child fails, or the job is interrupted or terminated,
    children already started are stopped rather than orphaned.
    """
    indexes = job_partitions(job, count)
    output = outside(root, output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Beside, not inside, output: the artifact uploads output in full.
    temporary = outside(root, output.with_name(output.name + "-tmp"))
    temporary.mkdir(mode=0o700, exist_ok=False)
    lock = threading.Lock()
    children = []
    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        for slot, index in enumerate(indexes):
            env = environment()
            env.update(slot_ports(slot))
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "parishkit.stewardship.quality_ci",
                    "shard",
                    "--root",
                    str(root),
                    "--index",
                    str(index),
                    "--count",
                    str(count),
                    "--output",
                    str(output / f"partition-{index}"),
                    "--basetemp",
                    str(temporary / f"partition-{index}"),
                    *([] if coverage else ["--no-coverage"]),
                    *(["--select", str(select)] if select else []),
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            thread = threading.Thread(
                target=relay,
                args=(process.stdout, f"[partition {index}] ", lock),
                daemon=True,
            )
            thread.start()
            children.append((index, process, thread))
        status = 0
        for index, process, thread in children:
            # Each child enforces its own partition deadline; the job's
            # timeout-minutes is the backstop for a child that never returns.
            code = process.wait()
            thread.join(timeout=RELAY_DRAIN_SECONDS)
            if code:
                status = 1
                reason = f"signal {-code}" if code < 0 else f"status {code}"
                print(f"CI partition {index}/{count} failed ({reason})", flush=True)
    except BaseException:
        stop_children(children)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    if not status:
        print(f"CI job {job}: partitions {indexes} passed", flush=True)
    return status


def remap(root, recorded_root, filename):
    """Rebase measured checkout files without admitting outside-source aliases."""
    original = Path(filename)
    origin = Path(recorded_root)
    relative = original.relative_to(origin) if original.is_absolute() else original
    if ".." in relative.parts:
        raise ValueError("Coverage path escapes its checkout")
    result = root / relative
    if not result.is_file() or result.resolve() != result:
        raise ValueError("Coverage references an absent or aliased source")
    return str(result)


def verify_baseline(directory, digests, universe):
    """Prove the baseline parts in ``directory`` ran every test exactly once.

    ``universe`` is combine's own collection of the unsplit baseline, and
    ``digests`` the shard receipt's digests of the parts' receipts, in part
    order. Each part's receipt must be exactly the one that completing its
    share of ``universe`` produces: the same suite, and the same tests
    selected and completed. The shares must then be disjoint and together be
    the whole of ``universe``.
    """
    parts = len(digests)
    shares = []
    for part, digest in enumerate(digests, start=1):
        path = baseline_evidence(directory, part)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("CI baseline evidence digest mismatch")
        expected = baseline_partition(universe, part, parts)
        if json.loads(path.read_text()) != baseline_receipt(
            part, parts, universe, expected, expected
        ):
            raise ValueError("CI baseline part did not execute its complete share")
        shares.extend(expected)
    if len(shares) != len(set(shares)) or sorted(shares) != universe:
        raise ValueError("CI baseline parts do not cover the suite exactly once")


def combine(root, directory, report, count, test_map=None):
    """Require every exact partition before combining raw line/branch evidence.

    Only complete, measured, unselected partitions combine, so an "affected"
    run's evidence can never pass. ``test_map``, when given, is a new file
    that receives the per-test source map (``quality_select.build_map``)
    that later "affected" runs select database tests from.
    """
    partition([], 1, count)
    scope = load_scope(root)
    digest = tree_digest(root)
    directory = outside(root, directory)
    report = outside(root, report)
    if report.exists() or Path(str(report) + ".data").exists():
        raise ValueError("Coverage report must be new")
    receipts = sorted(directory.glob("*/receipt.json"))
    if len(receipts) != count:
        raise ValueError("Missing or extra CI shard artifacts")
    universe = database_collection(root)
    baseline = baseline_collection(root)
    combined = Coverage(branch=True, source=[str(root / "src")], config_file=False)
    combined.set_option("run:data_file", str(report) + ".data")
    seen = set()
    for path in receipts:
        receipt = json.loads(path.read_text())
        index = receipt["index"]
        if (
            type(index) is not int
            or index in seen
            or receipt["schema"] != 2
            or receipt["count"] != count
            or receipt["baseline"] is not (index == 1)
            or type(receipt["baseline_sha256"]) is not list
            or len(receipt["baseline_sha256"]) != (BASELINE_PARTS if index == 1 else 0)
            or receipt.get("coverage", True) is not True
            or receipt["tree"] != digest
        ):
            raise ValueError("Duplicate, stale or incompatible CI shard")
        expected = partition(universe, index, count)
        data_path = path.parent / "coverage.data"
        tests_path = path.parent / "tests.json"
        if (
            hashlib.sha256(data_path.read_bytes()).hexdigest() != receipt["data_sha256"]
            or hashlib.sha256(tests_path.read_bytes()).hexdigest()
            != receipt["tests_sha256"]
        ):
            raise ValueError("CI shard artifact digest mismatch")
        tests = json.loads(tests_path.read_text())
        if tests != {"universe": universe, "selected": expected, "completed": expected}:
            raise ValueError("CI shard did not execute its complete test partition")
        if index == 1:
            verify_baseline(path.parent, receipt["baseline_sha256"], baseline)
        data = CoverageData(basename=str(data_path))
        data.read()
        if not data.has_arcs() or not data.measured_files():
            raise ValueError("CI shard lacks branch coverage")
        # Validate before passing a callback into SQLite: callback exceptions
        # otherwise lose their useful type behind a generic database error.
        for filename in data.measured_files():
            remap(root, receipt["root"], filename)
        combined.get_data().update(data, map_path=partial(remap, root, receipt["root"]))
        seen.add(index)
    if seen != set(range(1, count + 1)) or tree_digest(root) != digest:
        raise ValueError("CI evidence is incomplete or source changed")
    combined.save()
    # Reserve the output exclusively, just like the serial quality runner.
    with report.open("x", encoding="utf-8"):
        pass
    combined.json_report(outfile=str(report))
    lines, branches = coverage_percentages(root, scope, report)
    if test_map is not None:
        write_map(root, test_map, combined.get_data(), universe, count)
    print(f"All {len(universe):,} database tests accounted for across {count} shards")
    print(
        f"All {len(baseline):,} baseline tests accounted for across "
        f"{BASELINE_PARTS} parts"
    )
    print(f"Stewardship scope: lines {lines:.2f}%; branches {branches:.2f}%")
    return 0 if lines >= FLOOR and branches >= FLOOR else 1


def write_map(root, path, data, universe, count):
    """Write the database test map; a failure is logged, never fatal.

    The map only lets later "affected" runs skip tests, so it must never
    change a full run's (or release evidence's) outcome. Without a map,
    affected runs fall back to the whole database group.
    """
    try:
        document = build_map(data, root, universe, count)
        with outside(root, path).open("x", encoding="utf-8") as stream:
            json.dump(document, stream)
    except Exception as error:  # noqa: BLE001 - optional output only
        print(
            f"WARNING: database test map not written ({type(error).__name__}: "
            f"{error}); affected runs will run the whole database group",
            file=sys.stderr,
        )


def main(argv=None):
    """Run isolated CI shards, a packed job of them, or the coverage gate."""
    parser = StewardshipArgumentParser(
        "python -m parishkit.stewardship.quality_ci",
        description="Run isolated test shards and verify their combined coverage.",
        error_hints={},
    )
    parser.add_argument("operation", choices=("shard", "job", "combine"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--basetemp", type=Path)
    parser.add_argument("--no-coverage", action="store_true")
    parser.add_argument("--select", type=Path)
    parser.add_argument("--map", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if args.operation == "shard":
            if (
                args.index is None
                or args.output is None
                or args.input
                or args.report
                or args.map
            ):
                parser.usage_error("shard requires --index/--output only")
            return run_shard(
                root,
                args.output,
                args.index,
                args.count,
                args.basetemp,
                coverage=not args.no_coverage,
                select=args.select,
            )
        if args.operation == "job":
            if (
                args.index is None
                or args.output is None
                or args.input
                or args.report
                or args.basetemp
                or args.map
            ):
                parser.usage_error("job requires --index/--output only")
            return run_job(
                root,
                args.output,
                args.index,
                args.count,
                coverage=not args.no_coverage,
                select=args.select,
            )
        if (
            args.input is None
            or args.report is None
            or args.index
            or args.output
            or args.basetemp
            or args.no_coverage
            or args.select
        ):
            parser.usage_error("combine requires --input/--report (and --map) only")
        return combine(root, args.input, args.report, args.count, args.map)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        CoverageException,
        subprocess.SubprocessError,
    ):
        print(
            "ERROR: CI measurement incomplete or invalid; inspect test progress",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
