"""Refit the browser partition hints from CI job logs (#2051).

Usage: python -m parishkit.stewardship.quality_browser_hints JOB.log ...

Each JOB.log is one stewardship-browser-engine job's log, as
``gh api repos/OWNER/REPO/actions/jobs/ID/logs`` prints it. The tool reads
every test's ``CI_PROGRESS ... END <node> elapsed=Ns`` seconds, fits
quality_sharding's BROWSER_ENGINE_SECONDS and BROWSER_FILE_EXTRA_SECONDS to
them, and prints both as Python to paste into quality_sharding. It then
replays the checked-in BROWSER_JOBS on the same tests: each partition's
estimate beside the seconds its tests actually took, and each job's total
as a share of quality_browser.JOB_SECONDS. Paste the fit first, then rerun
to replay it. Hints only place whole files; they never select tests.
"""

import argparse
import re
import statistics
from collections import defaultdict
from pathlib import Path

from .quality_browser import JOB_SECONDS
from .quality_sharding import (
    BROWSER_JOBS,
    browser_estimate,
    browser_label,
    browser_partition,
    parse_browser_runs,
)

# A partition's first line names its engine; an unsplit engine has no INDEX/COUNT.
PARTITION = re.compile(r"CI_BROWSER_PARTITION (\w+)(?: \d+/\d+)?:")
# Node IDs may contain spaces, so the node is everything up to " elapsed=".
END = re.compile(r"CI_PROGRESS \S+ END (\S.*) elapsed=([0-9.]+)s\s*$")


def measured_seconds(logs):
    """Each (engine, node ID)'s median seconds across the LOGS' text lines.

    LOGS is an iterable of line iterables, one per job log. A test measured
    by several attempts of one job takes its median, so one slow attempt
    does not set the hint alone.
    """
    seconds = defaultdict(list)
    for lines in logs:
        engine = None
        for line in lines:
            if match := PARTITION.search(line):
                engine = match.group(1)
            elif engine and (match := END.search(line)):
                seconds[(engine, match.group(1))].append(float(match.group(2)))
    return {key: statistics.median(values) for key, values in seconds.items()}


def file_totals(measured):
    """Per engine, each test file name's [cases, seconds] from MEASURED."""
    totals = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for (engine, node), seconds in measured.items():
        total = totals[engine][node.split("::", 1)[0].rsplit("/", 1)[-1]]
        total[0] += 1
        total[1] += seconds
    return totals


def fit_line(points):
    """Least-squares (seconds per file, seconds per case) for (cases, seconds).

    Neither may be negative: a negative fixed cost would make one-case files
    look free, so the intercept is then pinned at zero, and seconds falling
    with size get no per-case cost, only their mean.
    """
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in points) / spread if spread else 0
    )
    if mean_y - slope * mean_x < 0:
        return 0.0, sum(y for _, y in points) / sum(x for x, _ in points)
    if slope < 0:
        return mean_y, 0.0
    return mean_y - slope * mean_x, slope


def fit_hints(totals, threshold=10, rounds=10):
    """Fit per-engine (fixed, per-case) seconds and per-file extra seconds.

    A file whose seconds stay more than THRESHOLD above its engine's line,
    averaged over the engines that run it, gets that average as extra
    seconds and is left out of the next fit, so a few slow files cannot
    tilt every engine's line. Repeat until the set of such files settles.
    Return (engine seconds, extra seconds), rounded as quality_sharding
    records them.
    """
    extras = {}
    for _ in range(rounds):
        lines = {
            engine: fit_line(
                [tuple(total) for name, total in files.items() if name not in extras]
            )
            for engine, files in totals.items()
        }
        residuals = defaultdict(list)
        for engine, files in totals.items():
            fixed, per_case = lines[engine]
            for name, (cases, seconds) in files.items():
                residuals[name].append(seconds - fixed - per_case * cases)
        found = {
            name: round(statistics.mean(values))
            for name, values in residuals.items()
            if statistics.mean(values) > threshold
        }
        settled = found.keys() == extras.keys()
        extras = found
        if settled:
            break
    engines = {
        engine: (round(fixed), round(per_case, 2))
        for engine, (fixed, per_case) in sorted(lines.items())
    }
    return engines, dict(sorted(extras.items(), key=lambda item: -item[1]))


def replay(measured, jobs=BROWSER_JOBS):
    """Split MEASURED's tests as JOBS would, with the checked-in hints.

    Return (job, [(label, estimated, actual seconds)]) for each job. Tests a
    budget kill never finished are absent from MEASURED, so the affected
    partitions read somewhat low.
    """
    cases = [(node, engine) for engine, node in measured]
    result = []
    for job in jobs:
        rows = []
        for engine, index, count in parse_browser_runs(job):
            nodes = browser_partition(cases, engine, index, count)
            rows.append(
                (
                    browser_label(engine, index, count),
                    browser_estimate(nodes, engine),
                    sum(measured[(engine, node)] for node in nodes),
                )
            )
        result.append((job, rows))
    return result


def main():
    """Print the refitted hints and the replay of BROWSER_JOBS."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs", nargs="+", type=Path, help="browser job logs")
    parser.add_argument(
        "--threshold",
        type=float,
        default=10,
        help="seconds above the fit that earn a file extra seconds (default 10)",
    )
    args = parser.parse_args()
    measured = measured_seconds(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
        for path in args.logs
    )
    if not measured:
        parser.exit(1, "No CI_PROGRESS END lines found in the logs\n")
    engines, extras = fit_hints(file_totals(measured), args.threshold)
    print(f"BROWSER_ENGINE_SECONDS = {engines!r}")
    print(f"BROWSER_FILE_EXTRA_SECONDS = {extras!r}")
    for job, rows in replay(measured):
        total = sum(actual for _, _, actual in rows)
        parts = ", ".join(
            f"{label} estimated {estimate:,.0f} s, actual {actual:,.0f} s"
            for label, estimate, actual in rows
        )
        print(f"{job}: {total:,.0f} s ({total / JOB_SECONDS:.0%}); {parts}")


if __name__ == "__main__":
    main()
