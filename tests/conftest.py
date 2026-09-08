"""Opt-in machine-readable collection evidence for host/container parity."""

import json


def pytest_addoption(parser):
    """Expose collection evidence without changing normal test selection."""
    parser.addoption(
        "--collection-manifest",
        action="store_true",
        help="Print a JSON node-ID manifest for host/container parity checks",
    )


def pytest_collection_finish(session):
    """Emit actual selected node IDs, including skips and duplicate occurrences."""
    if session.config.getoption("--collection-manifest"):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line(
            "PARISHKIT_TEST_NODEIDS="
            + json.dumps([item.nodeid for item in session.items], ensure_ascii=True)
        )
