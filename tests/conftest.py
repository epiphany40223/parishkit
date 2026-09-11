"""Opt-in machine-readable collection evidence for host/container parity."""

import json
from pathlib import Path

import pytest


def pytest_addoption(parser):
    """Expose collection evidence without changing normal test selection."""
    parser.addoption(
        "--collection-manifest",
        action="store_true",
        help="Print a JSON node-ID manifest for host/container parity checks",
    )
    parser.addoption(
        "--require-postgresql-tests",
        action="store_true",
        help="Fail unless disposable PostgreSQL tests are selected and none skip",
    )
    parser.addoption(
        "--require-no-skips",
        action="store_true",
        help="Fail if any selected runtime verification is skipped",
    )


def pytest_collection_modifyitems(config, items):
    """A database CI gate must not pass on a pure-profile or empty selection."""
    if config.getoption("--require-no-skips"):
        for argument in config.args:
            selected = Path(str(argument).split("::", 1)[0]).resolve()
            if not any(
                item.path.resolve() == selected
                or item.path.resolve().is_relative_to(selected)
                for item in items
            ):
                raise pytest.UsageError("Required verification path collected no tests")
    if not config.getoption("--require-postgresql-tests"):
        return
    from django.conf import settings

    directory = Path(__file__).parent / "stewardship" / "database"
    if (
        settings.SETTINGS_MODULE != "parishkit.stewardship.settings.database_test"
        or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql"
        or not any(item.path.is_relative_to(directory) for item in items)
    ):
        raise pytest.UsageError(
            "Required disposable PostgreSQL tests are not configured"
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Do not turn skipped database verification into a successful CI run."""
    report = yield
    if (
        item.config.getoption("--require-postgresql-tests")
        or item.config.getoption("--require-no-skips")
    ) and report.skipped:
        report.outcome = "failed"
        report.longrepr = (
            "A required PostgreSQL verification was skipped."
            if item.config.getoption("--require-postgresql-tests")
            else "A required verification was skipped."
        )
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """An opt-in gate cannot silently drop a module during collection."""
    report = yield
    if collector.config.getoption("--require-no-skips") and report.skipped:
        report.outcome = "failed"
        report.longrepr = "A required verification was skipped during collection."
    return report


def pytest_collection_finish(session):
    """Emit actual selected node IDs, including skips and duplicate occurrences."""
    if session.config.getoption("--collection-manifest"):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line(
            "PARISHKIT_TEST_NODEIDS="
            + json.dumps([item.nodeid for item in session.items], ensure_ascii=True)
        )
