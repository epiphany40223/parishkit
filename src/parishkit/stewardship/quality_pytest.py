"""Opt-in CI progress and exact execution evidence; never a runtime plugin."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from .quality_select import chosen, load_selection
from .quality_sharding import (
    BROWSER_ENGINES,
    baseline_partition,
    browser_estimate,
    browser_label,
    browser_partition,
    nodes_digest,
    parse_browser_partition,
    partition,
)

# Pin discovery semantics through pytest's public getini API. Changing these
# defaults is an explicit suite-ownership change, not an implicit CI override.
BROWSER_DISCOVERY = {
    "python_files": ["test_*.py", "*_test.py"],
    "python_classes": ["Test"],
    "python_functions": ["test"],
    "norecursedirs": [
        "*.egg",
        ".*",
        "_darcs",
        "build",
        "CVS",
        "dist",
        "node_modules",
        "venv",
        "{arch}",
    ],
}


def pytest_addoption(parser):
    """Keep ordinary developer tests unchanged unless CI explicitly opts in."""
    parser.addoption("--ci-shard", help="Required PostgreSQL partition INDEX/COUNT")
    parser.addoption(
        "--ci-baseline",
        help="Required non-database baseline part PART/COUNT (#1205)",
    )
    parser.addoption("--ci-evidence", help="New outside-repository execution receipt")
    parser.addoption(
        "--ci-select",
        help="Partition only the database tests this selection file chooses",
    )
    parser.addoption(
        "--ci-progress", action="store_true", help="Timestamp test progress"
    )
    parser.addoption(
        "--ci-browser-engine",
        choices=BROWSER_ENGINES,
        help="Run one engine from the complete required browser suite",
    )
    parser.addoption(
        "--ci-browser-evidence", help="New external browser completion receipt"
    )
    parser.addoption(
        "--ci-browser-partition",
        help="Run job INDEX/COUNT of the selected engine's cases (default 1/1)",
    )


def pytest_configure(config):
    """Register state per pytest invocation, not across nested test sessions."""
    config.addinivalue_line(
        "markers",
        "sql_rules: asserts only that the database refuses something (a trigger, "
        "guard or grant); affected CI runs skip it unless the grants or its "
        "own file change",
    )
    if config.getoption("--ci-select") and not config.getoption("--ci-shard"):
        raise pytest.UsageError("A database test selection requires a shard")
    if config.getoption("--ci-shard") and config.getoption("--ci-baseline"):
        raise pytest.UsageError("A CI run is a database shard or a baseline part")
    if config.getoption("--ci-evidence") and not (
        config.getoption("--ci-shard") or config.getoption("--ci-baseline")
    ):
        raise pytest.UsageError("CI evidence requires a shard or a baseline part")
    if config.getoption("--ci-browser-evidence") and not config.getoption(
        "--ci-browser-engine"
    ):
        raise pytest.UsageError("Browser evidence requires an engine partition")
    if config.getoption("--ci-browser-partition") and not config.getoption(
        "--ci-browser-engine"
    ):
        raise pytest.UsageError("A browser partition requires an engine")
    if any(
        config.getoption(option)
        for option in ("--ci-shard", "--ci-baseline", "--ci-progress")
    ):
        config.pluginmanager.register(Progress(config), "stewardship-ci-progress")
    if config.getoption("--ci-browser-engine"):
        config.pluginmanager.register(
            BrowserSelection(config), "stewardship-browser-selection"
        )


def failures_path(receipt):
    """The sidecar beside a browser receipt that lists failed test phases."""
    return receipt.with_name(f"{receipt.name}.failed")


def running_path(receipt):
    """The sidecar beside a browser receipt naming the test that last started."""
    return receipt.with_name(f"{receipt.name}.running")


def progress_path(receipt):
    """The sidecar beside a browser receipt logging each test's start and end."""
    return receipt.with_name(f"{receipt.name}.progress")


class BrowserSelection:
    """A CI-only complete-suite partition; normal developer selection is unchanged."""

    def __init__(self, config):
        """Reject partial selectors and mixed profiles before collecting the suite."""
        self.config = config
        self.engine = config.getoption("--ci-browser-engine")
        try:
            self.partition = parse_browser_partition(
                config.getoption("--ci-browser-partition") or "1/1"
            )
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        self.directory = config.rootpath.resolve() / "tests/stewardship/browser"
        self.selected = []
        self.executed = []
        self.seconds = {}
        self.evidence = None
        self.started = None
        if value := config.getoption("--ci-browser-evidence"):
            self.evidence = Path(value).resolve()
            if self.evidence.exists() or self.evidence.is_relative_to(
                config.rootpath.resolve()
            ):
                raise pytest.UsageError("Browser evidence must be a new external path")
        if (
            not config.getoption("--require-no-skips")
            or os.environ.get("PARISHKIT_RUN_BROWSER_TESTS") != "1"
            or config.getoption("--ci-shard")
            or config.getoption("--ci-evidence")
            or config.getoption("--require-postgresql-tests")
            or len(config.args) != 1
            or Path(config.args[0]).resolve() != self.directory
            or any(
                config.getoption(option, default=False)
                for option in (
                    "keyword",
                    "markexpr",
                    "deselect",
                    "lf",
                    "stepwise",
                    "stepwise_skip",
                    "stepwise_reset",
                    "ignore",
                    "ignore_glob",
                    "collectonly",
                    "setuponly",
                    "setupplan",
                    "inifilename",
                    "markers",
                )
            )
            or any(
                override != "faulthandler_timeout=120"
                for override in config.getoption("override_ini", default=[]) or []
            )
            or any(
                config.getini(name) != expected
                for name, expected in BROWSER_DISCOVERY.items()
            )
        ):
            raise pytest.UsageError(
                "CI browser selection requires the complete opted-in browser suite "
                "with --require-no-skips and no other selection/profile options"
            )

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        """Fail on unknown ownership, then retain exactly this engine's cases."""
        if any(
            not item.path.resolve().is_relative_to(self.directory) for item in items
        ):
            raise pytest.UsageError("CI browser selection admits only browser tests")
        cases = [
            (
                item.nodeid,
                getattr(getattr(item, "callspec", None), "params", {}).get(
                    "browser_engine"
                ),
            )
            for item in items
        ]
        try:
            selected = set(browser_partition(cases, self.engine, *self.partition))
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        self.selected = sorted(selected)
        removed = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        self.config.hook.pytest_deselected(items=removed)
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line(
            f"CI_BROWSER_PARTITION {browser_label(self.engine, *self.partition)}: "
            f"{len(selected):,} out of "
            f"{len(cases):,} ({len(selected) / len(cases):.1%}) cases, "
            f"estimated {browser_estimate(self.selected, self.engine):,.0f} s"
        )
        # Timed from here, so the actual figure is execution, like the estimate.
        self.started = time.monotonic()

    def pytest_runtest_logstart(self, nodeid, location):
        """Name the test now starting in the receipt's running sidecar.

        Overwritten for each test, so a parent that kills this run for its
        time budget can say which test was running (#2001).
        """
        if self.evidence:
            self.log_progress(f"START {nodeid}")
            # Temp file plus os.replace, so a kill never leaves it empty.
            path = running_path(self.evidence)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(f"{nodeid}\n", encoding="utf-8")
            os.replace(temp, path)

    def log_progress(self, line):
        """Append LINE to the progress sidecar, closing it so a kill loses nothing.

        The parent prints the running and last completed tests from this
        file when it kills a partition at its budget (#2355).
        """
        with progress_path(self.evidence).open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")

    def pytest_runtest_logreport(self, report):
        """Count actual assertion-body execution, not collection or fixture setup.

        Also append each failed phase's node ID to the receipt's failure
        sidecar as it happens, so a parent that kills this run for exceeding
        its time budget can still say whether any test had failed (#1112).
        """
        if report.when == "call" and report.passed:
            self.executed.append(report.nodeid)
        if self.evidence:
            # A test's seconds are its setup, call and teardown together.
            seconds = self.seconds[report.nodeid] = (
                self.seconds.get(report.nodeid, 0) + report.duration
            )
            if report.when == "teardown":
                self.log_progress(f"END {report.nodeid} elapsed={seconds:.3f}s")
        if report.failed and self.evidence:
            with failures_path(self.evidence).open("a", encoding="utf-8") as stream:
                stream.write(f"{report.nodeid}\n")

    def pytest_sessionfinish(self, session, exitstatus):
        """A successful early exit must not turn incomplete execution green.

        Also log estimated against actual seconds, pass or fail, so drifting
        browser cost hints (quality_sharding) show up in ordinary job logs.
        """
        if self.started is not None:
            self.config.pluginmanager.get_plugin("terminalreporter").write_line(
                f"CI_BROWSER_TIMING {browser_label(self.engine, *self.partition)}: "
                f"estimated {browser_estimate(self.selected, self.engine):,.0f} s, "
                f"actual {time.monotonic() - self.started:,.0f} s"
            )
        if exitstatus != 0:
            return
        if not self.selected or sorted(self.executed) != self.selected:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            self.config.pluginmanager.get_plugin("terminalreporter").write_line(
                "CI browser partition did not execute every selected assertion body"
            )
            return
        if self.evidence:
            with self.evidence.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "engine": self.engine,
                        "partition": list(self.partition),
                        "selected": self.selected,
                        "executed": sorted(self.executed),
                    },
                    stream,
                )


class Progress:
    """Track collection, completed teardown and elapsed time without test locals.

    A database shard (``--ci-shard``) partitions the database tests and
    logs every test's start and end. A baseline part (``--ci-baseline``,
    #1205) partitions the whole non-database run instead, quietly, and its
    receipt records digests rather than node IDs (see ``baseline_receipt``).
    """

    def __init__(self, config):
        """Validate explicit partition inputs before collecting any tests."""
        self.config = config
        self.baseline = bool(config.getoption("--ci-baseline"))
        self.started = {}
        self.durations = {}
        self.completed = []
        self.universe = []
        self.selected = []
        self.subset = None
        self.shard = None
        self.evidence = None
        self.selection = None
        if value := config.getoption("--ci-shard") or config.getoption("--ci-baseline"):
            try:
                index, count = [int(part) for part in value.split("/")]
                partition([], index, count)
                self.shard = (index, count)
                if self.baseline is bool(
                    config.getoption("--require-postgresql-tests")
                ):
                    # Database shards need the database gate; the baseline,
                    # which runs without a database, must not have it.
                    raise ValueError("Database gate does not match the run")
                self.evidence = Path(config.getoption("--ci-evidence")).resolve()
                if (
                    self.evidence.is_relative_to(config.rootpath.resolve())
                    or self.evidence.exists()
                ):
                    raise ValueError("Evidence must be a new external path")
                if path := config.getoption("--ci-select"):
                    self.selection = load_selection(path)
            except (TypeError, ValueError, KeyError, OSError) as error:
                raise pytest.UsageError("Invalid CI shard/evidence options") from error

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        """Partition the entire database suite, or an explicit selection of it.

        Without ``--ci-select`` an empty partition fails closed. A selection
        (an "affected" run only) may leave a partition empty: few tests can
        spread thinner than the fixed partition count.
        """
        if self.shard is None:
            return
        if self.baseline:
            self.universe = sorted(item.nodeid for item in items)
            self.selected = baseline_partition(self.universe, *self.shard)
            if not self.selected:
                raise pytest.UsageError("CI baseline part selected no tests")
            selected = set(self.selected)
            removed = [item for item in items if item.nodeid not in selected]
            items[:] = [item for item in items if item.nodeid in selected]
            self.config.hook.pytest_deselected(items=removed)
            return
        directory = self.config.rootpath / "tests/stewardship/database"
        if any(not item.path.is_relative_to(directory) for item in items):
            raise pytest.UsageError("CI shards must contain only database tests")
        self.universe = sorted(item.nodeid for item in items)
        pool = self.universe
        if self.selection is not None:
            self.subset = pool = sorted(
                item.nodeid
                for item in items
                if chosen(
                    item.nodeid,
                    item.get_closest_marker("sql_rules") is not None,
                    self.selection,
                )
            )
        self.selected = partition(pool, *self.shard)
        if not self.selected and self.selection is None:
            raise pytest.UsageError("CI partition selected no tests")
        selected = set(self.selected)
        removed = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        self.config.hook.pytest_deselected(items=removed)

    def emit(self, message):
        """Flush safe test identifiers/timing so a live log shows actual progress."""
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line(f"CI_PROGRESS {stamp} {message}")
        reporter.flush()

    def pytest_runtest_logstart(self, nodeid, location):
        """Identify the active test before its fixtures can block.

        A baseline part stays quiet: a line per test would add tens of
        thousands of log lines, some of them megabytes long.
        """
        if self.baseline:
            return
        self.started[nodeid] = time.monotonic()
        self.emit(f"START {nodeid}")

    def pytest_runtest_logreport(self, report):
        """A test is complete only after teardown, not just its assertion body."""
        if report.when == "teardown":
            self.completed.append(report.nodeid)
        if self.baseline:
            return
        self.durations.setdefault(report.nodeid, {})[report.when] = report.duration
        if report.when == "teardown":
            elapsed = time.monotonic() - self.started.pop(report.nodeid)
            self.emit(f"END {report.nodeid} elapsed={elapsed:.3f}s")

    def pytest_sessionfinish(self, session, exitstatus):
        """Emit a fresh success receipt only for a fully executed partition."""
        if (
            self.selection is not None
            and not self.selected
            and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
        ):
            # A selection's empty partition ran nothing, which is complete.
            session.exitstatus = exitstatus = pytest.ExitCode.OK
        if self.shard is None or exitstatus != 0:
            return
        if sorted(self.completed) != self.selected:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return
        if self.baseline:
            with self.evidence.open("x", encoding="utf-8") as stream:
                json.dump(
                    baseline_receipt(
                        *self.shard, self.universe, self.selected, self.completed
                    ),
                    stream,
                )
            return
        with self.evidence.open("x", encoding="utf-8") as stream:
            evidence = {
                "universe": self.universe,
                "selected": self.selected,
                "completed": sorted(self.completed),
            }
            # Only a selection records its subset, so quality_ci.combine (full
            # runs and release evidence) rejects selected evidence outright.
            if self.subset is not None:
                evidence["subset"] = self.subset
            json.dump(evidence, stream)
        # Keep optional scheduling diagnostics outside the strictly validated
        # execution receipt consumed by quality_ci.combine.
        with self.evidence.with_suffix(".timings.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(self.durations, stream)


def baseline_receipt(part, parts, universe, selected, completed):
    """The execution receipt one non-database baseline part writes (#1205).

    It holds each set's count and ``nodes_digest`` rather than node IDs,
    which can be megabytes long. ``quality_ci.combine`` builds the same
    receipt for every part from its own collection of the unsplit suite and
    requires each to match, so the parts together ran every test once.
    """
    return {
        "part": part,
        "parts": parts,
        **{
            name: {"count": len(nodes), "sha256": nodes_digest(nodes)}
            for name, nodes in (
                ("universe", universe),
                ("selected", selected),
                ("completed", completed),
            )
        },
    }
