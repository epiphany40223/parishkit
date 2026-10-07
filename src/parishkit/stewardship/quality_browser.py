"""Run a browser CI partition only when pytest proves actual completion."""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .quality_ci import environment
from .quality_sharding import BROWSER_ENGINES, browser_label, parse_browser_runs

# About 38 minutes for all of one job's partitions together: a job's tests
# take about 15. Stay under the 60-minute job limit, even after a slow
# 17-minute install, so this bounded timeout, not the job cancel, reports a
# hang.
JOB_SECONDS = 2280


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
        subprocess.run(
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
            timeout=timeout,
            check=True,
        )
        cases = validate_receipt(receipt, engine, index, count)
        print(
            f"CI_BROWSER_COMPLETE {browser_label(engine, index, count)}: "
            f"{cases:,} executed cases",
            flush=True,
        )


def run_job(root, runs):
    """Run each partition in turn under one shared deadline; fail if any failed.

    A failed partition does not stop the later ones, so one job's log shows
    every engine's result. Return the labels of the partitions that failed.
    """
    deadline = time.monotonic() + JOB_SECONDS
    failed = []
    for engine, index, count in runs:
        try:
            run_engine(root, engine, index, count, max(1, deadline - time.monotonic()))
        # OSError: a missing interpreter or checkout still gets a clear line
        # and lets the job's later partitions run.
        except (ValueError, subprocess.SubprocessError, OSError) as error:
            label = browser_label(engine, index, count)
            print(f"CI_BROWSER_FAILED {label}: {error}", flush=True)
            failed.append(label)
    return failed


def main():
    """Expose only complete partition runners, with no arbitrary pytest options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        required=True,
        help='Partitions to run, such as "webkit:1/2 chromium:1/2"',
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
