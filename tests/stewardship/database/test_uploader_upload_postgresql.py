"""The off-site uploader's copy, as its own login (#235, "Handoff").

Operations specification, "Dedicated off-site uploader": a set is copied
only when a backup run row binds its name (the row's ``started_at`` to the
second, ``completed_at`` within 24 hours), and only when it is newer than
the newest copy already recorded for the folder; a copy named in the future
(a clock that jumped) is left out of that comparison. The login is the real
uploader login name with exactly its registry's grants; Drive, the key and
the interlock are stand-ins (the interlock's use is recorded).
"""

import json
import logging
from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction

from parishkit.config import ConfigError
from parishkit.stewardship import (
    backup_offsite,
    observability,
    startup_interlock,
    uploader_boundaries,
    uploader_commands,
)
from parishkit.stewardship.accounts import key_files
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
from parishkit.stewardship.jobs.backup_models import BackupRun, BackupUpload
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.uploader_commands import run_upload

from ..drive_fakes import FakeDrive, write_sealed_set
from . import test_backup_offsite_postgresql as offsite_tests
from .test_background_grants_postgresql import task_login
from .test_backup_offsite_postgresql import FOLDER

pytestmark = pytest.mark.django_db(transaction=True)
# The applied configuration with the off-site folder (the off-site copy
# tests' fixtures), for the one test that calls the real definer.
destination = offsite_tests.destination
workspace = offsite_tests.workspace


class Lease:
    """The startup interlock, stood in for; each entry is recorded."""

    def __init__(self, uses):
        self.uses = uses

    def __enter__(self):
        self.uses.append("lease")
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def uploader(tmp_path, monkeypatch):
    """An uploader profile over an empty backups directory and a fake Drive."""
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    drive = FakeDrive(FOLDER)
    uses = []
    monkeypatch.setattr(
        uploader_commands, "destination", lambda: (FOLDER, "mail@example.org")
    )
    monkeypatch.setattr(backup_offsite, "DriveClient", lambda session, **o: drive)
    # The test database has no runtime row; the definer test reads the tag.
    monkeypatch.setattr(backup_offsite, "set_tag", lambda: drive.tag)
    monkeypatch.setattr(key_files, "read_private", lambda path: b"key")
    monkeypatch.setattr(
        startup_interlock, "StartupLease", lambda path, offline: Lease(uses)
    )
    monkeypatch.setattr(
        uploader_boundaries, "admit_uploader_identity", lambda: uses.append("admit")
    )
    configuration = SimpleNamespace(
        profile=DeploymentProfile.PRODUCTION,
        mail_redirect=None,
        paths={"backups": backups},
        secrets={"google_workspace": str(tmp_path / "key")},
        runtime_root=tmp_path,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_paths.RuntimeLayout",
        lambda c: SimpleNamespace(interlock=tmp_path / "startup.lock"),
    )
    return SimpleNamespace(
        backups=backups, drive=drive, uses=uses, configuration=configuration
    )


def bound_set(case, age, *, name_shift=timedelta(0), content=None):
    """A complete local set and the run row that names it.

    The row starts ``age`` before the database's time and completes now
    (its default); the directory is named for the start plus
    ``name_shift``, so a non-zero shift is a name the row does not bind.
    """
    with transaction.atomic():
        started = database_now() - age
    name = (started + name_shift).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = case.backups / name
    directory.mkdir(mode=0o700)
    write_sealed_set(directory, content or name.encode())
    record(directory, started)
    return directory


def record(directory, started):
    """One backup run row for ``directory``'s manifest, started at ``started``."""
    run = BackupRun.objects.create(
        started_at=started,
        database_bytes=1,
        files_bytes=1,
        manifest_digest=backup_offsite._digest(directory),
        recipient_fingerprint="c" * 16,
        application_version="test",
    )
    run.refresh_from_db()
    return run


def upload(case, **options):
    """One pass as the uploader login, never sleeping between retries."""
    with task_login(ServiceRole.BACKUP_UPLOADER, exact=True, reconnect=True):
        return run_upload(
            case.configuration,
            session_factory=lambda value, subject: None,
            sleep=lambda seconds: None,
            **options,
        )


def test_a_bound_set_is_copied_and_the_next_pass_is_idle(uploader):
    """The uploader records its own copy; then nothing is pending."""
    directory = bound_set(uploader, timedelta(minutes=10))
    assert upload(uploader) == {"state": "uploaded", "sets": 1}
    assert uploader.drive.sets() == [directory.name]
    row = BackupUpload.objects.get()
    assert (row.state, row.set_name, row.folder_id) == (
        "uploaded",
        directory.name,
        FOLDER,
    )
    assert uploader.uses == ["lease", "admit"]
    uploader.uses.clear()
    assert upload(uploader) is None
    assert uploader.uses == []
    assert BackupUpload.objects.count() == 1


def test_an_unrecorded_set_directory_is_never_uploaded(uploader):
    """A stray directory with no run row is not even considered."""
    directory = uploader.backups / "20991231T000000Z"
    directory.mkdir(mode=0o700)
    write_sealed_set(directory)
    assert upload(uploader) is None
    assert upload(uploader, require_digest="d" * 64) == {
        "state": "uploaded",
        "sets": 0,
        "required_copy": "not_uploaded",
    }
    assert uploader.uses == [] and not BackupUpload.objects.exists()
    assert uploader.drive.calls.count("upload") == 0


@pytest.mark.parametrize(
    "age, shift",
    [
        (timedelta(minutes=10), timedelta(seconds=1)),
        (timedelta(minutes=10), -timedelta(days=1)),
        (timedelta(hours=25), timedelta(0)),
    ],
    ids=["name a second off", "name a day earlier", "completed 25 hours later"],
)
def test_a_set_its_rows_do_not_bind_is_refused_while_it_is_newest(
    uploader, caplog, age, shift
):
    """A ``verification`` row every pass, until a newer set copies."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    refused = bound_set(uploader, age, name_shift=shift)
    for runs in (1, 2):
        result = upload(uploader)
        assert result == {"state": "failed", "failure_kind": "verification", "sets": 0}
        rows = BackupUpload.objects.filter(state="failed", set_name=refused.name)
        assert rows.count() == runs
    assert uploader.drive.calls.count("upload") == 0
    assert any(
        json.loads(observability.SafeJsonFormatter().format(r))
        .get("extra", {})
        .get("drive_failure")
        == "verification"
        for r in caplog.records
    )
    newer = bound_set(uploader, timedelta(0), content=b"newer")
    # Both pending, oldest first: refused once more, then the newer copied.
    assert upload(uploader) == {
        "state": "failed",
        "failure_kind": "verification",
        "sets": 1,
    }
    assert uploader.drive.sets() == [newer.name]
    # Older than the newest copy now: silently not pending.
    uploader.uses.clear()
    failed = BackupUpload.objects.filter(state="failed").count()
    assert upload(uploader) is None
    assert uploader.uses == []
    assert BackupUpload.objects.filter(state="failed").count() == failed


def test_one_binding_row_among_several_admits_the_set(uploader):
    """Rows that do not bind neither admit a set nor hold a real one back."""
    directory = bound_set(uploader, timedelta(minutes=5))
    with transaction.atomic():
        record(directory, database_now() - timedelta(hours=3))
    assert upload(uploader) == {"state": "uploaded", "sets": 1}
    assert uploader.drive.sets() == [directory.name]


def test_a_set_not_newer_than_the_newest_copy_is_silently_skipped(uploader):
    """No row, no interlock: neither an older anchor nor a reused name."""
    newest = bound_set(uploader, timedelta(minutes=5))
    assert upload(uploader)["sets"] == 1
    copied_folder = uploader.drive.children(FOLDER, tagged=True, folders=True)[0]
    # An older set, bound to its own row, staged after the newest copied.
    bound_set(uploader, timedelta(hours=6), content=b"older")
    # And an impostor in the copied set's own name, with a row of its own.
    for name in backup_offsite.SEALED_FILES:
        (newest / name).unlink()
    write_sealed_set(newest, b"impostor")
    with transaction.atomic():
        record(newest, database_now() - timedelta(minutes=5))
    uploader.uses.clear()
    assert upload(uploader) is None
    assert uploader.uses == []
    assert BackupUpload.objects.count() == 1
    assert not uploader.drive.items[copied_folder["id"]]["trashed"]


def failure_kinds(caplog):
    """The failure_kind of every formatted process-log line so far."""
    return [
        json.loads(observability.SafeJsonFormatter().format(record))
        .get("extra", {})
        .get("failure_kind")
        for record in caplog.records
    ]


def test_a_copy_named_in_the_future_is_ignored_with_a_warning(uploader, caplog):
    """A clock that jumped left a future copy on record; newer sets still copy."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    BackupUpload.objects.create(
        state="uploaded",
        set_name="20991231T000000Z",
        manifest_digest="a" * 64,
        folder_id=FOLDER,
    )
    assert upload(uploader) is None
    # An idle pass stays silent, the warning included.
    assert "backup_offsite_future_copy" not in failure_kinds(caplog)
    directory = bound_set(uploader, timedelta(minutes=1))
    result = upload(uploader)
    assert result == {
        "state": "uploaded",
        "sets": 1,
        "future_copies": ["20991231T000000Z"],
    }
    assert directory.name in uploader.drive.sets()
    assert "backup_offsite_future_copy" in failure_kinds(caplog)


def test_require_digest_reads_the_row_whoever_wrote_it(uploader):
    """Another run's copy counts; a copy in another folder does not."""
    directory = bound_set(uploader, timedelta(minutes=5))
    digest = backup_offsite._digest(directory)
    BackupUpload.objects.create(
        state="uploaded",
        set_name=directory.name,
        manifest_digest=digest,
        folder_id="0" * 20,
    )
    result = upload(uploader, wait_seconds=60, require_digest=digest)
    assert result["required_copy"] == "uploaded" and result["sets"] == 1
    other = bound_set(uploader, timedelta(0), content=b"other")
    BackupUpload.objects.create(
        state="uploaded",
        set_name=other.name,
        manifest_digest=backup_offsite._digest(other),
        folder_id="0" * 20,
    )
    uploader.drive.fail["create_folder"] = ["permission"]
    result = upload(uploader, require_digest=backup_offsite._digest(other))
    assert result["required_copy"] == "not_uploaded"
    # Copied meanwhile by another run into this folder: found after the pass.
    BackupUpload.objects.create(
        state="uploaded",
        set_name=other.name,
        manifest_digest=backup_offsite._digest(other),
        folder_id=FOLDER,
    )
    result = upload(uploader, require_digest=backup_offsite._digest(other))
    assert result == {"state": "uploaded", "sets": 0, "required_copy": "uploaded"}


def test_the_uploader_reads_its_destination_through_the_definer(request):
    """As its own login, the two applied settings and nothing else."""
    from django.db import connection

    request.getfixturevalue("destination")
    with task_login(ServiceRole.BACKUP_UPLOADER, exact=True):
        assert uploader_commands.destination() == (FOLDER, "mail@example.org")
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM stewardship_system_configuration")
            assert cursor.fetchone() is not None
            assert backup_offsite.set_tag().startswith("deployment-")


def test_smoke_send_copies_only_a_set_its_row_binds(uploader):
    """The uploader profile's --send set: the newest, bound to its run row."""
    from parishkit.stewardship.smoke import _bound_newest

    with pytest.raises(ConfigError, match="no complete backup set"):
        _bound_newest(uploader.backups)
    directory = bound_set(uploader, timedelta(minutes=5))
    with task_login(ServiceRole.BACKUP_UPLOADER, exact=True):
        assert _bound_newest(uploader.backups) == directory
    unbound = bound_set(
        uploader, timedelta(minutes=1), name_shift=timedelta(seconds=30)
    )
    assert unbound.name > directory.name
    with (
        task_login(ServiceRole.BACKUP_UPLOADER, exact=True),
        pytest.raises(ConfigError, match="not bound"),
    ):
        _bound_newest(uploader.backups)
