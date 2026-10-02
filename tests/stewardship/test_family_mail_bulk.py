"""Bulk Family send switch (#430): configuration, Compose and scheduler scans."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import (
    BULK_FAMILY_SEND_VARIABLE,
    BULK_SEND_BATCH_VARIABLE,
    load_deployment,
)
from parishkit.stewardship.jobs.family_mail_bulk import BulkSettings
from parishkit.stewardship.jobs.scanning import RecentHints
from parishkit.stewardship.runtime_topology import render_runtime

from .test_deployment import config_file
from .test_runtime_topology import IMAGE, configuration_at


def test_bulk_send_is_off_by_default_and_turned_on_by_one_setting(tmp_path):
    """Off unless set; YAML or the environment turns it on; the document keeps it."""
    from parishkit.stewardship.deployment_documents import deployment_document

    default = load_deployment(environ={})
    assert default.bulk_family_send is False and default.bulk_send_batch == 20
    name = BULK_FAMILY_SEND_VARIABLE
    assert load_deployment(environ={name: "1"}).bulk_family_send is True
    assert load_deployment(environ={name: "0"}).bulk_family_send is False
    # Compose passes the variable empty unless the operator exports it.
    assert load_deployment(environ={name: ""}).bulk_family_send is False
    path = config_file(tmp_path, {"bulk_family_send": True, "bulk_send_batch": 10})
    configuration = load_deployment(path, environ={})
    assert configuration.bulk_family_send and configuration.bulk_send_batch == 10
    (tmp_path / "rendered").mkdir()
    rendered = config_file(
        tmp_path / "rendered", deployment_document(configuration)["deployment"]
    )
    again = load_deployment(rendered, environ={})
    assert again.bulk_family_send and again.bulk_send_batch == 10
    # The environment overrides the YAML: "0" turns it back off.
    assert load_deployment(path, environ={name: "0"}).bulk_family_send is False
    batch = BULK_SEND_BATCH_VARIABLE
    assert load_deployment(environ={batch: "100"}).bulk_send_batch == 100


@pytest.mark.parametrize("value", ["yes", "true", "2", 1])
def test_an_unclear_bulk_switch_is_rejected(tmp_path, value):
    """Only 1/0 in the environment and true/false in YAML."""
    if isinstance(value, str):
        with pytest.raises(ConfigError, match="bulk_family_send"):
            load_deployment(environ={BULK_FAMILY_SEND_VARIABLE: value})
    else:
        path = config_file(tmp_path, {"bulk_family_send": value})
        with pytest.raises(ConfigError, match="bulk_family_send"):
            load_deployment(path, environ={})


@pytest.mark.parametrize("value", ["0", "101", "x"])
def test_a_send_batch_outside_one_to_a_hundred_is_rejected(value):
    """B is bounded: every message in a crashed batch needs manual settling."""
    with pytest.raises(ConfigError, match="bulk_send_batch"):
        load_deployment(environ={BULK_SEND_BATCH_VARIABLE: value})


def test_the_switch_reaches_the_scheduler_worker_and_mail_dispatch(tmp_path):
    """The one-command switch reaches exactly the three services that use it."""
    compose, _ = render_runtime(
        configuration_at(tmp_path, production=True), image=IMAGE
    )
    services = compose["services"]
    for name, expected in (
        (BULK_FAMILY_SEND_VARIABLE, ["mail-dispatch", "scheduler", "worker"]),
        (BULK_SEND_BATCH_VARIABLE, ["mail-dispatch"]),
    ):
        assert (
            sorted(
                service
                for service, values in services.items()
                if name in values.get("environment", {})
            )
            == expected
        )
        assert services["mail-dispatch"]["environment"][name] == "${" + name + ":-}"


def test_bulk_settings_are_validated():
    """A malformed switch or batch size fails before any task runs."""
    assert BulkSettings() == BulkSettings(False, 20)
    for enabled, batch in ((1, 20), (True, 0), (True, 101), (True, "20")):
        with pytest.raises(ValueError):
            BulkSettings(enabled, batch)


def row(task_type="outbox_delivery"):
    """A due TaskRun stand-in for the scan's in-memory hint bookkeeping."""
    return SimpleNamespace(pk=uuid4(), version=1, task_type=task_type)


def test_a_scan_hints_only_a_few_bulk_rows_per_type_and_page():
    """Bulk consumers find their own rows: two hints per type wake them."""
    recent = RecentHints(drained=frozenset({"outbox_delivery"}))
    recent.start_page()
    rows = [row() for _ in range(4)]
    assert not recent.fresh(rows[0], bulk=True)
    recent.admitted(rows[0], bulk=True)
    recent.admitted(rows[1], bulk=True)
    # Further bulk rows of the type count as fresh; others are untouched.
    assert recent.fresh(rows[2], bulk=True)
    assert not recent.fresh(rows[3], bulk=False)
    assert not recent.fresh(row("family_mail_prepare"), bulk=True)
    # The next page starts counting again.
    recent.start_page()
    assert not recent.fresh(rows[2], bulk=True)


def test_without_bulk_rows_the_scan_is_unchanged():
    """Off (nothing drained), only the 45 s republication window applies."""
    recent = RecentHints()
    recent.start_page()
    first = row()
    for _ in range(5):
        recent.admitted(first)
    assert not recent.fresh(row())
    recent.published(first.pk)
    assert recent.fresh(first)
