"""Consumer acknowledgements require all currently supervised workers to agree."""

import io
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import consumer_runtime as consumers
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_paths import private_directory

from .test_runtime_topology import configuration_at


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    """Use real owner-only records and substitute only the Linux process inventory."""
    directory = tmp_path / "consumer"
    private_directory(directory, create=True)
    monkeypatch.setattr(consumers, "DIRECTORY", directory)
    monkeypatch.setattr(consumers, "PIDFILE", directory / "supervisor.pid")
    configuration = replace(
        configuration_at(tmp_path),
        service_role=ServiceRole.WEB,
        secrets={"metrics": tmp_path / "metrics"},
    )
    identities = {100: (1, 1000), 101: (100, 1001), 102: (100, 1002)}
    monkeypatch.setattr(consumers, "process_identity", lambda pid: identities[pid])
    monkeypatch.setattr(consumers, "_children", lambda pid: (101, 102))
    write_private(consumers.PIDFILE, b"100\n")
    records = {}
    for pid in (101, 102):
        records[pid] = {
            "version": 1,
            "pid": pid,
            "started": identities[pid][1],
            "parent": 100,
            "parent_started": 1000,
            "receipts": {"metrics": "a" * 64},
        }
        write_private(
            directory / f"{pid}-{identities[pid][1]}.json",
            json.dumps(records[pid]).encode(),
        )
    return configuration, records, identities


def test_complete_live_cohort_agrees(cohort):
    """Only matching evidence from both workers yields a service-level receipt."""
    configuration, _, _ = cohort
    assert consumers.loaded_service_receipts(configuration) == {"metrics": "a" * 64}


@pytest.mark.parametrize(
    "replacement",
    [
        {"version": True},
        {"pid": 999},
        {"started": 999},
        {"parent": 999},
        {"parent_started": 999},
        {"receipts": {"metrics": "b" * 64}},
        {"receipts": {"metrics": "private-token"}},
        {"receipts": {}},
        {"receipts": {"metrics": "a" * 64, "unexpected": "b" * 64}},
        {"extra": 1},
    ],
)
def test_incomplete_or_stale_worker_evidence_refused(cohort, replacement):
    """One successful worker cannot conceal disagreement or a replayed identity."""
    configuration, records, _ = cohort
    records[102].update(replacement)
    write_private(
        consumers.DIRECTORY / "102-1002.json", json.dumps(records[102]).encode()
    )
    with pytest.raises(ConfigError):
        consumers.loaded_service_receipts(configuration)


@pytest.mark.parametrize("children", [(), (101,), (101, 102, 103)])
def test_partial_startup_or_worker_overlap_refused(cohort, monkeypatch, children):
    """Startup and graceful replacement must settle before acknowledgement."""
    configuration, _, _ = cohort
    monkeypatch.setattr(consumers, "_children", lambda pid: children)
    with pytest.raises(ConfigError):
        consumers.loaded_service_receipts(configuration)


def test_process_set_change_during_check_refused(cohort, monkeypatch):
    """A stable initial snapshot alone cannot establish whole-service readiness."""
    configuration, _, _ = cohort
    snapshots = iter([(101, 102), (101, 103)])
    monkeypatch.setattr(consumers, "_children", lambda pid: next(snapshots))
    with pytest.raises(ConfigError):
        consumers.loaded_service_receipts(configuration)


def test_reused_or_reparented_worker_refused(cohort):
    """Changing a kernel identity invalidates otherwise intact prior records."""
    configuration, _, identities = cohort
    identities[102] = (999, 1002)
    with pytest.raises(ConfigError):
        consumers.loaded_service_receipts(configuration)
    identities[102] = (100, 9999)
    with pytest.raises(ConfigError):
        consumers.loaded_service_receipts(configuration)


def test_one_container_never_acknowledges_multiple_replicas(cohort):
    """Cross-container coordination needs its own evidence, not an assumed count."""
    configuration, _, _ = cohort
    configuration = replace(
        configuration,
        runtime_budget=replace(
            configuration.runtime_budget,
            replicas=2,
            database_connections=200,
            auxiliary_connections=16,
        ),
    )
    with pytest.raises(ConfigError, match="one supervised"):
        consumers.loaded_service_receipts(configuration)


def test_admitted_worker_publishes_only_safe_receipts(cohort, monkeypatch, settings):
    """Publication uses actual worker/parent kernel identities and private files."""
    configuration, _, identities = cohort
    pid = os.getpid()
    identities[pid] = (100, 1009)
    settings.STEWARDSHIP_HEALTH_RUNTIME = SimpleNamespace(configuration=configuration)
    settings.STEWARDSHIP_LOADED_CREDENTIAL_RECEIPTS = {"metrics": "c" * 64}
    consumers.publish_worker_receipts(SimpleNamespace(pid=pid))
    value = json.loads((consumers.DIRECTORY / f"{pid}-1009.json").read_bytes())
    assert value["receipts"] == {"metrics": "c" * 64}
    assert value["parent_started"] == 1000
    with pytest.raises(ConfigError):
        consumers.publish_worker_receipts(SimpleNamespace(pid=pid + 1))


@pytest.mark.parametrize("state", ["S", "Z", "X", "x"])
@pytest.mark.parametrize("recorded_pid", [123, 124])
def test_linux_stat_parser_handles_parentheses_and_zombies(
    monkeypatch, state, recorded_pid
):
    """Linux comm may contain closing parentheses; starttime is field 22."""
    value = f"{recorded_pid} (worker ) name) {state} 100 " + "0 " * 17 + "456 0\n"

    def open_stat(path, *args, **kwargs):
        assert str(path) == "/proc/123/stat"
        return io.StringIO(value)

    monkeypatch.setattr(consumers.Path, "open", open_stat)
    if state == "S" and recorded_pid == 123:
        assert consumers.process_identity(123) == (100, 456)
    else:
        with pytest.raises(ConfigError):
            consumers.process_identity(123)


@pytest.mark.parametrize("pid", [True, 1, -1, "100", 2**31])
def test_invalid_pid_refused_without_reading_proc(pid):
    """Only a valid non-init integer process ID can select a proc entry."""
    with pytest.raises(ConfigError):
        consumers.process_identity(pid)


@pytest.mark.parametrize("value", ["101 102", "101 101", "garbage", "1" * 4097])
def test_linux_child_inventory_is_bounded_and_unique(monkeypatch, value):
    """The exact supervisor's inventory must be bounded and unambiguous."""

    def open_children(path, *args, **kwargs):
        assert str(path) == "/proc/100/task/100/children"
        return io.StringIO(value)

    monkeypatch.setattr(consumers.Path, "open", open_children)
    if value == "101 102":
        assert consumers._children(100) == (101, 102)
    else:
        with pytest.raises(ConfigError):
            consumers._children(100)
