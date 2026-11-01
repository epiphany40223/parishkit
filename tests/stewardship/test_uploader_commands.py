"""The off-site uploader's ``upload`` command, without a database (#235).

Operations specification, "Dedicated off-site uploader": "Scheduling" (the
idle pattern), "Locking" (the expand transition's double lock) and
"Scripted upgrades" (``--wait-lock`` and ``--require-digest``). The
database reads are stood in for here; the handoff rules and the real login
are proved in ``database/test_uploader_upload_postgresql.py``.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from django.db import DatabaseError, OperationalError

from parishkit.config import ConfigError
from parishkit.stewardship import (
    backup_drive,
    backup_offsite,
    cli,
    runtime_paths,
    startup_interlock,
    uploader_boundaries,
    uploader_commands,
)
from parishkit.stewardship.backup_offsite import _copy_lock
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.uploader_commands import binds, run_upload

DIGEST = "d" * 64
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"


class Lease:
    """A stand-in startup interlock that records each use."""

    def __init__(self, calls, busy=False):
        self.calls = calls
        self.busy = busy

    def __enter__(self):
        if self.busy:
            raise startup_interlock.StartupBusy("busy")
        self.calls.append("lease")
        return self

    def __exit__(self, *exc):
        self.calls.append("release")
        return False


@pytest.fixture
def uploader(tmp_path, monkeypatch):
    """An uploader configuration over a private backups directory.

    Every database read and the copy itself are stand-ins that record
    their calls in order, so a test can see what ran and what never did.
    """
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    calls = []
    state = SimpleNamespace(
        calls=calls,
        pending=[],
        target=(FOLDER, "mail@example.org"),
        newest=None,
        copied="uploaded",
        lease_busy=False,
        backups=backups,
    )

    def read(name, value):
        """A stand-in read that records its name and answers ``value()``."""

        def answer(*args, **kwargs):
            calls.append(name)
            return value()

        return answer

    monkeypatch.setattr(
        uploader_commands, "_schema_current", read("schema", lambda: None)
    )
    monkeypatch.setattr(
        uploader_commands, "destination", read("destination", lambda: state.target)
    )
    monkeypatch.setattr(
        uploader_commands, "uploader_pending", read("pending", lambda: state.pending)
    )
    monkeypatch.setattr(
        uploader_commands, "_newest_state", read("newest", lambda: state.newest)
    )
    monkeypatch.setattr(
        uploader_commands, "_required", lambda digest, folder: state.copied
    )
    monkeypatch.setattr(
        uploader_commands, "_record", lambda value, **f: calls.append("record:" + value)
    )

    def copy(*args, **options):
        calls.append("copy")
        assert options["key"] == "/run/workspace/key"
        return {"state": "uploaded", "sets": len(state.pending)}

    monkeypatch.setattr(uploader_commands, "_copy_sets", copy)
    monkeypatch.setattr(
        uploader_boundaries, "admit_uploader_identity", lambda: calls.append("admit")
    )
    monkeypatch.setattr(
        startup_interlock,
        "StartupLease",
        lambda path, offline: Lease(calls, busy=state.lease_busy),
    )
    monkeypatch.setattr(
        runtime_paths,
        "RuntimeLayout",
        lambda configuration: SimpleNamespace(interlock=tmp_path / "startup.lock"),
    )
    monkeypatch.setattr(
        "django.db.connection.close", lambda: calls.append("close"), raising=False
    )
    state.configuration = SimpleNamespace(
        profile=DeploymentProfile.PRODUCTION,
        mail_redirect=None,
        paths={"backups": backups},
        secrets={"google_workspace": "/run/workspace/key"},
    )
    return state


def test_an_idle_cron_pass_is_silent_and_never_takes_the_interlock(uploader):
    """Nothing pending: no output, no interlock, no admission, no key."""
    assert run_upload(uploader.configuration) is None
    assert uploader.calls == ["schema", "destination", "pending"]


def test_an_idle_explicit_pass_reports_and_looks_up_the_required_copy(uploader):
    """With --require-digest the answer is the row, whoever wrote it."""
    result = run_upload(uploader.configuration, require_digest=DIGEST)
    assert result == {"state": "uploaded", "sets": 0, "required_copy": "uploaded"}
    uploader.copied = "not_uploaded"
    result = run_upload(uploader.configuration, wait_seconds=5, require_digest=DIGEST)
    assert result["required_copy"] == "not_uploaded"
    assert "lease" not in uploader.calls


def test_pending_sets_take_the_interlock_only_around_admission(uploader):
    """The interlock is held shared for admission and released before uploads."""
    uploader.pending = [("set", DIGEST, None)]
    assert run_upload(uploader.configuration) == {"state": "uploaded", "sets": 1}
    calls = uploader.calls
    assert calls[:3] == ["schema", "destination", "pending"]
    assert calls[3:7] == ["lease", "admit", "schema", "release"]
    assert calls.index("release") < calls.index("copy")


def test_a_held_interlock_is_a_quiet_stop(uploader):
    """Offline work holds the interlock: cron is silent; explicit says busy."""
    uploader.pending = [("set", DIGEST, None)]
    uploader.lease_busy = True
    assert run_upload(uploader.configuration) is None
    assert run_upload(uploader.configuration, wait_seconds=1) == {"state": "busy"}
    assert "copy" not in uploader.calls and "admit" not in uploader.calls


def test_a_removed_destination_records_disabled_once_under_the_interlock(uploader):
    """As the backup's copy does, so an old failure stops alerting."""
    uploader.target = None
    uploader.newest = "failed"
    assert run_upload(uploader.configuration) == {"state": "not_configured"}
    assert uploader.calls == [
        "schema",
        "destination",
        "newest",
        "lease",
        "admit",
        "schema",
        "record:disabled",
        "release",
    ]
    assert "copy" not in uploader.calls
    # Already disabled (or never copied): nothing to record, silent.
    for newest in ("disabled", None):
        uploader.calls.clear()
        uploader.newest = newest
        assert run_upload(uploader.configuration) is None
        assert "lease" not in uploader.calls
    result = run_upload(uploader.configuration, require_digest=DIGEST)
    assert result == {"state": "not_configured", "required_copy": "not_configured"}


def test_where_a_real_key_may_not_be_used_nothing_is_read(uploader):
    """LOCAL without a mail redirect (#1399): not_configured, before the lock."""
    uploader.configuration.profile = DeploymentProfile.LOCAL
    assert run_upload(uploader.configuration) is None
    result = run_upload(uploader.configuration, require_digest=DIGEST)
    assert result == {"state": "not_configured", "required_copy": "not_configured"}
    assert uploader.calls == []


@contextmanager
def held(path, flags=os.O_RDONLY):
    """Hold an exclusive flock on ``path`` from another descriptor."""
    descriptor = os.open(path, flags)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def test_a_held_copy_lock_is_silent_from_cron_and_busy_when_asked(uploader):
    """A copy running elsewhere: no database read at all."""
    with held(uploader.backups):
        assert run_upload(uploader.configuration) is None
        assert run_upload(uploader.configuration, require_digest=DIGEST) == {
            "state": "busy"
        }
    assert uploader.calls == []


def test_a_leftover_legacy_lock_file_is_ignored(uploader):
    """The contract drops the expand release's file lock: only the directory counts."""
    lock = uploader.backups / ".offsite-copy.lock"
    lock.touch(mode=0o600)
    with held(lock):
        assert run_upload(uploader.configuration) is None
    assert uploader.calls == ["schema", "destination", "pending"]


def test_the_copy_lock_writes_nothing_on_the_host(uploader):
    """Its backups mount is read-only: the directory lock creates no file."""
    run_upload(uploader.configuration)
    with _copy_lock(uploader.backups) as taken:
        assert taken
    assert list(uploader.backups.iterdir()) == []


def test_an_expired_lock_wait_is_logged_and_reported_busy(uploader, monkeypatch):
    """--wait-lock: the limit and the time waited go to the timeout log."""
    logged = []
    monkeypatch.setattr(
        backup_drive,
        "log_timeout",
        lambda what, **values: logged.append((what, values)),
    )
    now = [100.0]

    def sleep(seconds):
        now[0] += seconds

    with held(uploader.backups):
        result = run_upload(
            uploader.configuration,
            wait_seconds=3,
            require_digest=DIGEST,
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert result == {"state": "busy"}
    assert logged == [("lock_timeout", {"limit_seconds": 3, "elapsed_seconds": 3.0})]
    assert uploader.calls == []


def test_a_lock_released_within_the_wait_is_taken(uploader):
    """The wait polls; a copy that finishes in time lets this pass run."""
    descriptor = os.open(uploader.backups, os.O_RDONLY)
    fcntl.flock(descriptor, fcntl.LOCK_EX)

    def sleep(seconds):
        os.close(descriptor)

    result = run_upload(uploader.configuration, wait_seconds=10, sleep=sleep)
    assert result == {"state": "uploaded", "sets": 0}


@pytest.mark.parametrize(
    "error, warned",
    [
        (OperationalError("connection refused"), False),
        (DatabaseError("permission denied for table"), True),
    ],
)
def test_an_unready_database_is_silent_and_a_refusal_warns_once(
    uploader, monkeypatch, error, warned
):
    """Request mode's rule: offline is quiet, a refused login says so once."""
    logged = []

    def failing():
        raise error

    monkeypatch.setattr(uploader_commands, "destination", failing)
    monkeypatch.setattr(
        uploader_commands,
        "database_not_ready",
        lambda e: isinstance(e, OperationalError),
        raising=False,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_database.database_not_ready",
        lambda e: isinstance(e, OperationalError),
    )
    monkeypatch.setattr(
        uploader_commands,
        "emit_failure",
        lambda e, **options: logged.append(options["level"]),
    )
    assert run_upload(uploader.configuration) is None
    assert bool(logged) is warned
    assert run_upload(uploader.configuration, wait_seconds=1) == {
        "state": "unavailable"
    }
    assert "lease" not in uploader.calls


def test_a_refused_login_is_told_apart_from_an_offline_database():
    """libpq's own words decide, as runtime_database.database_not_ready reads them."""
    from parishkit.stewardship.runtime_database import database_not_ready

    offline = psycopg.OperationalError("Connection refused\n\tIs the server running")
    refused = psycopg.OperationalError(
        'FATAL:  password authentication failed for user "x"'
    )
    assert database_not_ready(offline)
    assert not database_not_ready(refused)


def test_a_schema_mismatch_is_silent(uploader, monkeypatch):
    """Not on this image's schema (mid-upgrade): a quiet exit."""

    def mismatch():
        raise uploader_commands.Quiet("schema")

    monkeypatch.setattr(uploader_commands, "_schema_current", mismatch)
    assert run_upload(uploader.configuration) is None
    assert run_upload(uploader.configuration, require_digest=DIGEST) == {
        "state": "schema"
    }


def test_a_future_named_copy_warns_on_a_pass_with_work_only(uploader, monkeypatch):
    """A clock that jumped: WARNING and the names in the output, never idle."""
    warned = []
    monkeypatch.setattr(
        uploader_commands,
        "emit",
        lambda event, **options: warned.append(options["failure_kind"]),
    )

    def pending(backups, folder, *, future=None):
        uploader.calls.append("pending")
        future.append("20991231T000000Z")
        return uploader.pending

    monkeypatch.setattr(uploader_commands, "uploader_pending", pending)
    assert run_upload(uploader.configuration) is None
    assert warned == []
    uploader.pending = [("set", DIGEST, None)]
    result = run_upload(uploader.configuration)
    assert result["future_copies"] == ["20991231T000000Z"]
    assert warned == ["backup_offsite_future_copy"]


def test_an_unexpected_copy_failure_is_recorded(uploader, monkeypatch):
    """Anything outside the Drive categories still records a failed row."""
    uploader.pending = [("set", DIGEST, None)]
    failed = []

    def broken(*args, **options):
        raise RuntimeError("boom")

    monkeypatch.setattr(uploader_commands, "_copy_sets", broken)
    monkeypatch.setattr(
        uploader_commands,
        "_failed",
        lambda folder, name, digest, kind: (
            failed.append(kind) or {"state": "failed", "failure_kind": kind}
        ),
    )
    monkeypatch.setattr(uploader_commands, "emit_failure", lambda e, **o: None)
    assert run_upload(uploader.configuration)["failure_kind"] == "unexpected"
    assert failed == ["unexpected"]


START = datetime(2026, 10, 28, 2, 0, 5, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "name, started, completed, bound",
    [
        ("20261028T020005Z", START, START + timedelta(minutes=3), True),
        ("20261028T020005Z", START, START + timedelta(hours=24), True),
        ("20261028T020005Z", START, START + timedelta(hours=24, seconds=1), False),
        ("20261028T020006Z", START, START + timedelta(minutes=3), False),
        ("20261027T020005Z", START, START + timedelta(minutes=3), False),
        (
            "20261028T020005Z",
            START.astimezone(datetime.now().astimezone().tzinfo),
            START + timedelta(minutes=3),
            True,
        ),
    ],
)
def test_a_set_name_binds_only_to_its_run_rows_start(name, started, completed, bound):
    """The name is started_at to the second in UTC; completion within 24 h."""
    assert binds(name, started, completed) is bound


def run_cli(*argv):
    """Run pk-stewardship with ``argv``; return the exit status."""
    try:
        return cli.main(list(argv))
    except SystemExit as exit_:
        return exit_.code


@pytest.mark.parametrize(
    "extra",
    [
        ["--wait-lock", "0"],
        ["--wait-lock", "-5"],
        ["--wait-lock", "1.5"],
        ["--wait-lock", "abc"],
        ["--wait-lock", str(backup_offsite.COPY_SECONDS + 1)],
        ["--require-digest", "D" * 64],
        ["--require-digest", "d" * 63],
        ["--require-digest", "d" * 64 + "\n"],
    ],
)
def test_bad_options_refuse_before_anything_is_read(
    tmp_path, monkeypatch, capsys, extra
):
    """Each malformed --wait-lock or --require-digest is refused with exit 2."""
    monkeypatch.setattr(
        uploader_commands,
        "load_deployment",
        lambda path: pytest.fail("nothing is loaded"),
    )
    monkeypatch.setattr(uploader_commands, "configure_logging", lambda: None)
    assert run_cli("upload", "--config", str(tmp_path / "x.yaml"), *extra) == 2
    assert "off-site upload refused" in capsys.readouterr().err


def test_upload_takes_no_other_option(capsys):
    """An option of another command is a usage error, not ignored."""
    assert run_cli("upload", "--config", "x.yaml", "--send") == 2
    assert run_cli("upload", "--config", "x.yaml", "--request") == 2


def test_upload_refuses_another_profile_and_a_missing_config(monkeypatch, capsys):
    """Only the uploader profile's document runs the copy."""
    monkeypatch.setattr(uploader_commands, "configure_logging", lambda: None)
    assert run_cli("upload") == 2
    monkeypatch.setattr(
        uploader_commands,
        "load_deployment",
        lambda path: SimpleNamespace(service_role="backup-worker"),
    )
    monkeypatch.setattr(
        uploader_boundaries,
        "admit_uploader_service",
        lambda c: pytest.fail("never admitted"),
    )
    assert run_cli("upload", "--config", "backup-worker.yaml") == 2
    assert "off-site upload refused" in capsys.readouterr().err


def test_the_cli_prints_one_line_or_nothing(monkeypatch, capsys):
    """Silence for an idle cron pass; one sorted JSON line otherwise."""
    monkeypatch.setattr(uploader_commands, "configure_logging", lambda: None)
    answers = [None, {"state": "uploaded", "sets": 1}]
    monkeypatch.setattr(uploader_commands, "_upload", lambda args: answers.pop(0))
    assert run_cli("upload", "--config", "x.yaml") == 0
    assert capsys.readouterr().out == ""
    assert run_cli("upload", "--config", "x.yaml") == 0
    assert json.loads(capsys.readouterr().out) == {"sets": 1, "state": "uploaded"}


def test_the_upload_command_is_listed_for_the_upgrade_probe(capsys):
    """The upgrade's step 1 keys on `upload` in the command list."""
    assert run_cli("--help") == 0
    out = capsys.readouterr().out
    assert ",upload," in out or ",upload}" in out


def test_a_refused_mount_or_login_is_the_generic_refusal(monkeypatch, capsys):
    """Admission failures exit 2 with one fixed sentence and nothing else."""
    monkeypatch.setattr(uploader_commands, "configure_logging", lambda: None)

    def refuse(args):
        raise ConfigError("Uploader mount inventory is incomplete or writable.")

    monkeypatch.setattr(uploader_commands, "_upload", refuse)
    assert run_cli("upload", "--config", "x.yaml") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "inventory" not in captured.err
