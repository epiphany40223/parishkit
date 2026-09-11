"""Real kernel lifecycle leases, including concurrent processes and inheritance."""

import os
import select
import subprocess
import sys

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.startup_interlock import MARKER, StartupBusy, StartupLease

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Compose runs Linux locks.")


@pytest.fixture
def lock_path(tmp_path):
    """Provision just this test's stable, owner-only interlock inode."""
    path = tmp_path / "startup.lock"
    path.write_bytes(MARKER)
    path.chmod(0o600)
    return path


def test_shared_online_and_exclusive_offline_exclusion(lock_path):
    with (
        StartupLease(lock_path, offline=False),
        StartupLease(lock_path, offline=False),
        pytest.raises(StartupBusy),
        StartupLease(lock_path, offline=True),
    ):
        pytest.fail("offline entered while online")
    with StartupLease(lock_path, offline=True):
        for offline in (False, True):
            with pytest.raises(StartupBusy), StartupLease(lock_path, offline=offline):
                pytest.fail("competing process entered")
    with StartupLease(lock_path, offline=True) as lease:
        lease.check()


@pytest.mark.parametrize("offline", [False, True])
def test_child_process_observes_the_same_interlock(lock_path, offline):
    program = (
        "from parishkit.stewardship.startup_interlock import StartupLease, StartupBusy;"
        "import sys\n"
        "try:\n"
        "    with StartupLease(sys.argv[1], offline=True): pass\n"
        "except StartupBusy: sys.exit(23)\n"
    )
    with StartupLease(lock_path, offline=offline):
        result = subprocess.run(
            [sys.executable, "-c", program, str(lock_path)],
            capture_output=True,
            timeout=10,
        )
    assert result.returncode == 23, result.stderr


def test_inherited_lease_survives_parent_close(lock_path):
    child = None
    try:
        with StartupLease(lock_path, offline=False) as lease:
            descriptor = lease.inherit()
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('ready', flush=True); sys.stdin.read()",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                pass_fds=(descriptor,),
            )
            assert select.select([child.stdout], [], [], 10)[0]
            assert child.stdout.readline().strip() == "ready"
        with pytest.raises(StartupBusy), StartupLease(lock_path, offline=True):
            pytest.fail("parent close released live child lease")
    finally:
        if child is not None:
            child.communicate(timeout=10)
    with StartupLease(lock_path, offline=True):
        pass


def test_replaced_inode_and_nested_or_closed_lease_fail(lock_path):
    lease = StartupLease(lock_path, offline=True)
    with pytest.raises(ConfigError):
        lease.check()
    with lease:
        with pytest.raises(ConfigError):
            lease.__enter__()
        replacement = lock_path.with_suffix(".replacement")
        replacement.write_bytes(MARKER)
        replacement.chmod(0o600)
        replacement.replace(lock_path)
        with pytest.raises(ConfigError, match="changed"):
            lease.check()
    with pytest.raises(ConfigError):
        lease.inherit()


@pytest.mark.parametrize("damage", ["mode", "contents", "hardlink", "directory"])
def test_invalid_interlock_is_never_repaired(lock_path, damage):
    if damage == "mode":
        lock_path.chmod(0o644)
    elif damage == "contents":
        lock_path.write_bytes(b"not an interlock")
    elif damage == "hardlink":
        lock_path.with_suffix(".alias").hardlink_to(lock_path)
    else:
        lock_path.unlink()
        lock_path.mkdir(mode=0o700)
    with pytest.raises(ConfigError), StartupLease(lock_path, offline=True):
        pytest.fail("invalid interlock admitted")
