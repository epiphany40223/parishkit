"""Run a browser CI partition only when pytest proves actual completion."""

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .quality_ci import environment
from .quality_pytest import failures_path, progress_path, running_path
from .quality_sharding import BROWSER_ENGINES, browser_label, parse_browser_runs

# 50 minutes for all of one job's partitions together; a job's tests should
# take about 24 to 28 on an ordinary runner (a Firefox quarter, or a
# Chromium and WebKit seventh, #2051); when they near 30, split the engines
# further (quality_sharding.BROWSER_JOBS) rather than raising this. Setup
# usually takes 1 to 2 minutes, so this leaves 10 minutes of the 60-minute
# job limit for setup and post-job steps, and this bounded timeout, not the job cancel,
# reports a hang. (One 2026-09-30 mirror outage spent 17 minutes installing;
# only a long hang in that same run would now reach the job cancel.) Raised
# from 38 minutes when slow runners ran a partition 60% over its estimate
# (#1112).
JOB_SECONDS = 3000
# The browser job's timeout-minutes in ci.yml, counted from the job's start.
# A slow package mirror can spend 25 to 42 minutes on setup (#2050), after
# which a full JOB_SECONDS budget would outlast the job and GitHub would cancel
# it mid-test with no CI_BROWSER_* line. So run_job also stops RESERVE_SECONDS
# before this limit, leaving time to kill the partition, log the kill and
# write the job summary.
JOB_LIMIT_SECONDS = 3600
RESERVE_SECONDS = 180
# The workflow's first step stores the job's start (Unix seconds) here.
JOB_STARTED_ENV = "BROWSER_JOB_STARTED"
# A directory the workflow uploads as an artifact on failure; each partition
# copies its progress log there (#2355). Unset outside CI, so nothing is kept.
PROGRESS_DIR_ENV = "BROWSER_PROGRESS_DIR"
# How many of the latest completed tests a kill report lists.
RECENT_TESTS = 10


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


def partition_limit(remaining, later, count, budget=JOB_SECONDS):
    """Seconds a partition may run: REMAINING job time less later reserves.

    Each of a job's COUNT partitions reserves an equal share of BUDGET,
    and LATER partitions still to run keep theirs, so a slow partition cannot
    starve the ones after it; time an earlier partition left unused rolls
    forward to the next. Shares are equal, not proportional to need: a
    Chromium partition needs well under its share and its WebKit partner
    about 1.6 times as long (#1151). So BROWSER_JOBS runs WebKit last, where
    it gets its own share plus whatever Chromium left unused. A
    one-partition job gets the whole budget.
    """
    return max(1, remaining - later * budget / count)


def job_budget(started, now):
    """Seconds the job's partitions may share, and the seconds setup used.

    STARTED is the job's start in Unix seconds (None outside CI) and NOW the
    current Unix time. The budget is JOB_SECONDS, cut to what is left before
    the job limit less RESERVE_SECONDS when setup was slow (#2050); never
    below zero, so partitions then get partition_limit's 1 s floor and are
    reported as budget kills instead of being cancelled by GitHub.
    """
    if started is None:
        return JOB_SECONDS, 0
    setup = max(0, now - started)
    left = JOB_LIMIT_SECONDS - RESERVE_SECONDS - setup
    return max(0, min(JOB_SECONDS, left)), setup


def job_started():
    """The job's start time from the workflow, or None when unset or invalid."""
    try:
        return int(os.environ[JOB_STARTED_ENV])
    except (KeyError, ValueError):
        return None


def progress_state(lines):
    """Split progress sidecar LINES into the running tests and the completed ones.

    Return (running, done): RUNNING lists the node ids started but not ended,
    oldest first, and DONE the (node id, seconds text) pairs ended, in order.
    """
    running, done = {}, []
    for line in lines:
        kind, _, rest = line.partition(" ")
        if kind == "START":
            running[rest] = None
        elif kind == "END":
            node, _, seconds = rest.rpartition(" elapsed=")
            running.pop(node, None)
            done.append((node, seconds))
    return list(running), done


def kill_report(label, what, limit, elapsed, lines):
    """Text naming what was killed, the limit and elapsed time, and the tests.

    WHAT says why the partition was stopped; LINES are its progress lines.
    The report lists the tests running at that moment and the last
    RECENT_TESTS completed with their seconds (#2355).
    """
    running, done = progress_state(lines)
    report = [
        f"CI_BROWSER_KILL_REPORT {label}: {what}; killed pytest and its process "
        f"group after {elapsed:,.0f} s (limit {limit:,.0f} s); "
        f"{len(done):,} test(s) had completed"
    ]
    report += [f"  running: {node}" for node in running] or ["  running: none"]
    report.append(f"  last {min(len(done), RECENT_TESTS)} completed:")
    report += [f"    {node} {seconds}" for node, seconds in done[-RECENT_TESTS:]]
    return "\n".join(report)


def keep_progress_log(label, lines):
    """Copy a partition's progress LINES into the artifact directory, if set."""
    if directory := os.environ.get(PROGRESS_DIR_ENV):
        Path(directory).mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", label)
        Path(directory, f"{name}.progress.log").write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8"
        )


def timeout_message(limit, elapsed, failures):
    """Say which budget ran out, the elapsed time and whether any test failed."""
    budget = (
        f"killed after {elapsed:,.0f} s when its {limit:,.0f} s partition budget "
        "(its share of the job budget) ran out"
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
        label = browser_label(engine, index, count)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            kill_group(process)
            elapsed = time.monotonic() - started
            progress = sidecar_lines(progress_path(receipt))
            print(
                kill_report(
                    label, "partition budget ran out", timeout, elapsed, progress
                ),
                flush=True,
            )
            keep_progress_log(label, progress)
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
            progress = sidecar_lines(progress_path(receipt))
            print(
                kill_report(
                    label,
                    "interrupted or cancelled",
                    timeout,
                    time.monotonic() - started,
                    progress,
                ),
                flush=True,
            )
            keep_progress_log(label, progress)
            raise
        # Kept on any exit, so a failed partition's log is uploaded too.
        keep_progress_log(label, sidecar_lines(progress_path(receipt)))
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
    budget, setup = job_budget(job_started(), time.time())
    # One line per job, so a kill after a slow setup is attributable (#2050).
    print(
        f"CI_BROWSER_JOB_BUDGET {budget:,.0f} s for {len(runs):,} partition(s); "
        f"setup used {setup:,.0f} s of the {JOB_LIMIT_SECONDS:,} s job limit",
        flush=True,
    )
    deadline = time.monotonic() + budget
    failed = []
    tests, kills, others = [], [], []
    # try/finally: a job timeout or cancel (SIGINT -> KeyboardInterrupt) during
    # a later partition must not drop the earlier failures from the summary.
    try:
        for position, (engine, index, count) in enumerate(runs):
            limit = partition_limit(
                deadline - time.monotonic(),
                len(runs) - position - 1,
                len(runs),
                budget,
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
