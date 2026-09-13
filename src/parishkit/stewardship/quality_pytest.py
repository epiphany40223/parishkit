"""Opt-in CI progress and exact execution evidence; never a runtime plugin."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from .quality_sharding import partition


def pytest_addoption(parser):
    """Keep ordinary developer tests unchanged unless CI explicitly opts in."""
    parser.addoption("--ci-shard", help="Required PostgreSQL partition INDEX/COUNT")
    parser.addoption("--ci-evidence", help="New outside-repository execution receipt")
    parser.addoption(
        "--ci-progress", action="store_true", help="Timestamp test progress"
    )


def pytest_configure(config):
    """Register state per pytest invocation, not across nested test sessions."""
    if config.getoption("--ci-evidence") and not config.getoption("--ci-shard"):
        raise pytest.UsageError("CI evidence requires a PostgreSQL shard")
    if config.getoption("--ci-shard") or config.getoption("--ci-progress"):
        config.pluginmanager.register(Progress(config), "stewardship-ci-progress")


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
