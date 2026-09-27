"""Standard-library container health probes."""

import json
import os
import subprocess
import sys
from time import monotonic

import pytest

from parishkit.stewardship import probe


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    """A private heartbeat directory whose publisher is reported alive."""
    directory = tmp_path / "heartbeat"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    monkeypatch.setattr(probe, "DIRECTORY", directory)
    monkeypatch.setattr(probe, "HEARTBEAT", directory / "heartbeat.json")
    monkeypatch.setattr(probe, "process_started", lambda pid: 1234)

    def write(*, started=1234, age=0.0, mode=0o600, value=None):
        """Publish a heartbeat the way the consumer loop does."""
        path = directory / "heartbeat.json"
        path.write_text(
            json.dumps(
                value
                if value is not None
                else {"pid": os.getpid(), "started": started, "time": monotonic() - age}
            )
        )
        path.chmod(mode)
        return path

    return write


def test_recent_heartbeat_from_live_process_is_healthy(heartbeat):
    heartbeat()
    assert probe.installer() == 0


@pytest.mark.parametrize(
    "options",
    [
        {"age": probe.MAX_AGE_SECONDS + 1},
        {"started": 999},  # the PID now names another process
        {"mode": 0o644},
        {"value": {"pid": 1, "started": 1234}},
        {"value": ["not", "an", "object"]},
    ],
)
def test_stale_foreign_or_malformed_heartbeat_is_unhealthy(heartbeat, options):
    heartbeat(**options)
    assert probe.installer() == 1


def test_missing_or_linked_heartbeat_is_unhealthy(heartbeat, tmp_path):
    assert probe.installer() == 1
    target = heartbeat()
    moved = tmp_path / "elsewhere.json"
    target.rename(moved)
    target.symlink_to(moved)
    assert probe.installer() == 1


def test_open_heartbeat_directory_is_unhealthy(heartbeat):
    heartbeat()
    probe.DIRECTORY.chmod(0o755)
    assert probe.installer() == 1


@pytest.mark.parametrize("arguments", [[], ["unknown"], ["installer", "web"]])
def test_unknown_probe_names_exit_two(arguments):
    assert probe.main(arguments) == 2


def test_probe_module_imports_only_the_standard_library():
    """The point of the probe: no Django or application import per check."""
    code = (
        "import sys; import parishkit.stewardship.probe; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'django', 'cryptography', 'celery', 'requests'} or "
        "(m.startswith('parishkit.') and m not in "
        "{'parishkit.stewardship', 'parishkit.stewardship.probe'})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
