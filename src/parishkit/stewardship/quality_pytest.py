"""Opt-in CI progress and exact execution evidence; never a runtime plugin."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from .quality_sharding import BROWSER_ENGINES, browser_partition, partition

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
    parser.addoption("--ci-evidence", help="New outside-repository execution receipt")
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


def pytest_configure(config):
    """Register state per pytest invocation, not across nested test sessions."""
    if config.getoption("--ci-evidence") and not config.getoption("--ci-shard"):
        raise pytest.UsageError("CI evidence requires a PostgreSQL shard")
    if config.getoption("--ci-browser-evidence") and not config.getoption(
        "--ci-browser-engine"
    ):
        raise pytest.UsageError("Browser evidence requires an engine partition")
    if config.getoption("--ci-shard") or config.getoption("--ci-progress"):
        config.pluginmanager.register(Progress(config), "stewardship-ci-progress")
    if config.getoption("--ci-browser-engine"):
        config.pluginmanager.register(
            BrowserSelection(config), "stewardship-browser-selection"
        )


class BrowserSelection:
    """A CI-only complete-suite partition; normal developer selection is unchanged."""

    def __init__(self, config):
        """Reject partial selectors and mixed profiles before collecting the suite."""
        self.config = config
        self.engine = config.getoption("--ci-browser-engine")
        self.directory = config.rootpath.resolve() / "tests/stewardship/browser"
        self.selected = []
        self.executed = []
        self.evidence = None
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
            selected = set(browser_partition(cases, self.engine))
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        self.selected = sorted(selected)
        removed = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        self.config.hook.pytest_deselected(items=removed)
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line(
            f"CI_BROWSER_PARTITION {self.engine}: {len(selected):,} out of "
            f"{len(cases):,} ({len(selected) / len(cases):.1%}) cases"
        )

    def pytest_runtest_logreport(self, report):
        """Count actual assertion-body execution, not collection or fixture setup."""
        if report.when == "call" and report.passed:
            self.executed.append(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        """A successful early exit must not turn incomplete execution green."""
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
                        "selected": self.selected,
                        "executed": sorted(self.executed),
                    },
                    stream,
                )


class Progress:
    """Track collection, completed teardown and elapsed time without test locals."""

    def __init__(self, config):
        """Validate explicit partition inputs before collecting any tests."""
        self.config = config
        self.started = {}
        self.completed = []
        self.universe = []
        self.selected = []
        self.shard = None
        self.evidence = None
        if value := config.getoption("--ci-shard"):
            try:
                index, count = [int(part) for part in value.split("/")]
                partition([], index, count)
                self.shard = (index, count)
                if not config.getoption("--require-postgresql-tests"):
                    raise ValueError("Required database gate missing")
                self.evidence = Path(config.getoption("--ci-evidence")).resolve()
                if (
                    self.evidence.is_relative_to(config.rootpath.resolve())
                    or self.evidence.exists()
                ):
                    raise ValueError("Evidence must be a new external path")
            except (TypeError, ValueError, OSError) as error:
                raise pytest.UsageError("Invalid CI shard/evidence options") from error

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        """Partition the entire database suite; partial selections fail closed."""
        if self.shard is None:
            return
        directory = self.config.rootpath / "tests/stewardship/database"
        if any(not item.path.is_relative_to(directory) for item in items):
            raise pytest.UsageError("CI shards must contain only database tests")
        self.universe = sorted(item.nodeid for item in items)
        self.selected = partition(self.universe, *self.shard)
        if not self.selected:
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
        """Identify the active test before its fixtures can block."""
        self.started[nodeid] = time.monotonic()
        self.emit(f"START {nodeid}")

    def pytest_runtest_logreport(self, report):
        """A test is complete only after teardown, not just its assertion body."""
        if report.when == "teardown":
            self.completed.append(report.nodeid)
            elapsed = time.monotonic() - self.started.pop(report.nodeid)
            self.emit(f"END {report.nodeid} elapsed={elapsed:.3f}s")

    def pytest_sessionfinish(self, session, exitstatus):
        """Emit a fresh success receipt only for a fully executed partition."""
        if self.shard is None or exitstatus != 0:
            return
        if sorted(self.completed) != self.selected:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return
        with self.evidence.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "universe": self.universe,
                    "selected": self.selected,
                    "completed": sorted(self.completed),
                },
                stream,
            )
