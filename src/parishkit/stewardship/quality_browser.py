"""Run a browser CI partition only when pytest proves actual completion."""

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .quality_ci import environment
from .quality_pytest import failures_path, running_path
from .quality_sharding import BROWSER_ENGINES, browser_label, parse_browser_runs

# 50 minutes for all of one job's partitions together; a job's tests should
# take at most about 30 on an ordinary runner (a Firefox half, #1380, or a
# Chromium and WebKit fifth, #1529); when they near 38, split the engines
# further (quality_sharding.BROWSER_JOBS) rather than raising this. Setup
# usually takes 1 to 2 minutes, so this leaves 10 minutes of the 60-minute
# job limit for setup and post-job steps, and this bounded timeout, not the job cancel,
# reports a hang. (One 2026-09-30 mirror outage spent 17 minutes installing;
# only a long hang in that same run would now reach the job cancel.) Raised
# from 38 minutes when slow runners ran a partition 60% over its estimate
# (#1112).
JOB_SECONDS = 3000


class BudgetExhausted(ValueError):
    """A partition was killed because its time budget ran out.

    FAILURES lists the test phases that had failed before the kill, RUNNING
    names the test that was running (None if none had started), and ELAPSED
    and LIMIT are the seconds it ran and its budget (#2001).
    """

    def __init__(self, message, failures=(), running=None, elapsed=0, limit=0):
        """Keep the kill's details for the job's log marker and summary."""
        super().__init__(message)
        self.failures = list(failures)
        self.running = running
        self.elapsed = elapsed
        self.limit = limit


def sidecar_lines(path):
    """The lines of a receipt sidecar the child pytest wrote, if it wrote one."""
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def partition_limit(remaining, later, count):
    """Seconds a partition may run: REMAINING job time less later reserves.

    Each of a job's COUNT partitions reserves an equal share of JOB_SECONDS,
    and LATER partitions still to run keep theirs, so a slow partition cannot
    starve the ones after it; time an earlier partition left unused rolls
    forward to the next. Shares are equal, not proportional to need: a
    Chromium partition needs well under its share and its WebKit partner
    about 1.6 times as long (#1151). So BROWSER_JOBS runs WebKit last, where
    it gets its own share plus whatever Chromium left unused. A
    one-partition job gets the whole budget.
    """
    return max(1, remaining - later * JOB_SECONDS / count)


def timeout_message(limit, elapsed, failures):
    """Say which budget ran out, the elapsed time and whether any test failed."""
    budget = (
        f"killed after {elapsed:,.0f} s when its {limit:,.0f} s partition budget "
        f"(its share of the {JOB_SECONDS:,} s job budget) ran out"
    )
    if not failures:
        return f"{budget}; no test had failed, so rerun the job"
    return f"{budget}; {len(failures):,} test phase(s) had already failed: " + (
        ", ".join(failures)
    )


def validate_receipt(path, engine, index=1, count=1):
    """A fresh receipt must bind the requested job to all executed cases."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict or set(value) != {
            "engine",
            "partition",
            "selected",
            "executed",
        }:
            raise ValueError("Invalid receipt fields")
        selected = value["selected"]
        if (
            value["engine"] != engine
            or value["partition"] != [index, count]
            or type(selected) is not list
            or not selected
            or not all(type(node) is str and node for node in selected)
            or sorted(set(selected)) != selected
            or selected != value["executed"]
        ):
            raise ValueError("Incomplete receipt")
    except (OSError, ValueError) as error:
        raise ValueError(
            "Browser partition did not produce valid completion evidence"
        ) from error
    return len(selected)


def kill_group(process):
    """Kill PROCESS's whole process group, then reap PROCESS itself."""
    # The group may have exited between the timeout and the kill.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def run_engine(root, engine, index=1, count=1, timeout=JOB_SECONDS):
    """Keep stdout live; reject early zero exits that never load pytest hooks.

    INDEX/COUNT names one partition of the engine's cases (see BROWSER_JOBS);
    the default 1/1 runs the engine's whole share of the suite.
    """
    if engine not in BROWSER_ENGINES:
        raise ValueError("Unsupported browser engine")
    # Each invocation owns a new private directory outside the checkout; no
    # prior run's receipt can satisfy a --version/--help or other early exit.
    with tempfile.TemporaryDirectory(prefix="parishkit-browser-ci-") as directory:
        receipt = Path(directory) / "completion.json"
        started = time.monotonic()
        # A new session makes pytest the leader of its own process group, so
        # a budget kill also reaches the Playwright driver and browsers it
        # started; orphans would otherwise slow the next partition.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/stewardship/browser",
                f"--ci-browser-engine={engine}",
                f"--ci-browser-evidence={receipt}",
                f"--ci-browser-partition={index}/{count}",
                "--require-no-skips",
                "--ci-progress",
                "--collection-manifest",
                "-o",
                "faulthandler_timeout=120",
                "--durations=10",
                "-q",
            ],
            cwd=root,
            env=environment() | {"PARISHKIT_RUN_BROWSER_TESTS": "1"},
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            kill_group(process)
            elapsed = time.monotonic() - started
            failures = sidecar_lines(failures_path(receipt))
            running = next(iter(sidecar_lines(running_path(receipt))), None)
            raise BudgetExhausted(
                timeout_message(timeout, elapsed, failures),
                failures,
                running,
                elapsed,
                timeout,
            ) from error
        except BaseException:
            # The new session no longer gets the job's interrupt signals, so
            # a cancel or interrupt here must not leave pytest running.
            kill_group(process)
            raise
        if returncode:
            error = subprocess.CalledProcessError(returncode, process.args)
            # The failed node ids, for the job summary (#2001).
            error.failures = sidecar_lines(failures_path(receipt))
            raise error
        cases = validate_receipt(receipt, engine, index, count)
        print(
            f"CI_BROWSER_COMPLETE {browser_label(engine, index, count)}: "
            f"{cases:,} executed cases",
            flush=True,
        )


def budget_kill(label, error):
    """Describe a budget kill with no failed test, naming the running test."""
    running = error.running or "no test yet"
    return (
        f"{label}: running {running} after {error.elapsed:,.0f} s "
        f"(budget {error.limit:,.0f} s)"
    )


def job_summary(tests, kills, others):
    """Markdown for the GitHub job summary, most actionable section first.

    TESTS holds (label, node id) pairs for failed test phases, KILLS the
    budget_kill lines of partitions killed with no failed test, and OTHERS
    the remaining failed partitions' "label: error" lines. A reader sees
    real test failures first and can tell a slow runner apart (#2001).
    """
    sections = [
        ("Failed browser tests", [f"`{node}` ({label})" for label, node in tests]),
        (
            "Browser partitions killed by their time budget with no failed "
            "test (likely a slow runner)",
            kills,
        ),
        ("Other failed browser partitions", others),
    ]
    return "".join(
        f"### {title}\n\n" + "".join(f"- {line}\n" for line in lines) + "\n"
        for title, lines in sections
        if lines
    )


def run_job(root, runs):
    """Run each partition in turn, each within its budget; fail if any failed.

    A failed partition does not stop the later ones, so one job's log shows
    every engine's result. A budget kill with no failed test logs
    CI_BROWSER_BUDGET_KILLED and anything else CI_BROWSER_FAILED; both fail
    the job. Failures also go to the GitHub job summary when one is
    available. Return the labels of the partitions that failed.
    """
    deadline = time.monotonic() + JOB_SECONDS
    failed = []
    tests, kills, others = [], [], []
    # try/finally: a job timeout or cancel (SIGINT -> KeyboardInterrupt) during
    # a later partition must not drop the earlier failures from the summary.
    try:
        for position, (engine, index, count) in enumerate(runs):
            limit = partition_limit(
                deadline - time.monotonic(), len(runs) - position - 1, len(runs)
            )
            try:
                run_engine(root, engine, index, count, limit)
            # OSError: a missing interpreter or checkout still gets a clear line
            # and lets the job's later partitions run.
            except (ValueError, subprocess.SubprocessError, OSError) as error:
                label = browser_label(engine, index, count)
                failed.append(label)
                failures = getattr(error, "failures", [])
                # A phase can fail twice (call and teardown); list each test once.
                tests += [(label, node) for node in dict.fromkeys(failures)]
                if isinstance(error, BudgetExhausted) and not failures:
                    kills.append(budget_kill(label, error))
                    print(f"CI_BROWSER_BUDGET_KILLED {kills[-1]}", flush=True)
                else:
                    others.append(f"{label}: {error}")
                    print(f"CI_BROWSER_FAILED {others[-1]}", flush=True)
    finally:
        if failed and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write(job_summary(tests, kills, others))
    return failed


def main():
    """Expose only complete partition runners, with no arbitrary pytest options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        required=True,
        help='Partitions to run, such as "chromium:1/5 webkit:1/5"',
    )
    args = parser.parse_args()
    try:
        runs = parse_browser_runs(args.runs)
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    if failed := run_job(Path.cwd(), runs):
        parser.exit(1, f"Browser partitions failed: {', '.join(failed)}\n")


if __name__ == "__main__":
    main()
