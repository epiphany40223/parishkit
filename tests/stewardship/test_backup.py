"""One backup set: sealed dump, sealed files, manifest, record and retention.

pg_dump is not installed on the test host, so the dump step is replaced by a
stand-in that streams known bytes; the sealing, the archive, the manifest, the
record, the retention and every refusal are the real code.
"""

import io
import json
import shutil
import tarfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import backup, backup_sealing
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.runtime_paths import RuntimeLayout, private_directory
from parishkit.stewardship.runtime_topology import _service_config

DUMP = b"PGDMP" + bytes(range(256)) * 40


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """A provisioned root with the operator's public key installed."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "rt")})
    provisioning.provision_runtime(
        configuration, image="parishkit-stewardship:development"
    )
    private, public = backup_sealing.generate_keypair()
    (tmp_path / "private").write_text(private)
    layout = RuntimeLayout(configuration)
    private_directory(layout.credential_directory("backup_data"), create=True)
    write_private(layout.credential("backup_data"), public.encode())
    monkeypatch.setattr(
        backup,
        "dump_database",
        lambda configuration, sink, *, recipient: backup_sealing.seal(
            io.BytesIO(DUMP), sink, recipient=recipient, kind="database"
        ),
    )
    return _service_config(configuration, ServiceRole.BACKUP_WORKER)


def opened(path, key):
    """Decrypt one sealed output with the human's key."""
    sink = io.BytesIO()
    kind, count, digest = backup_sealing.open_sealed(
        path.open("rb"), sink, private=backup_sealing.load_private(key)
    )
    return kind, count, digest, sink.getvalue()


def test_a_set_is_written_sealed_recorded_and_openable(deployment, tmp_path):
    """Both outputs open with the private key and the manifest names them."""
    # Uploaded branding is restored with the database that refers to it.
    branding = deployment.paths["media"] / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    (branding / "logo.png").write_bytes(b"\x89PNG synthetic")
    records = []
    manifest = backup.run_backup(
        deployment, record=lambda **facts: records.append(facts)
    )
    sets = list(deployment.paths["backups"].iterdir())
    assert len(sets) == 1 and backup.SET_NAME.match(sets[0].name)
    directory = sets[0]
    assert sorted(p.name for p in directory.iterdir()) == sorted(
        [backup.DUMP, backup.FILES, backup.MANIFEST]
    )
    assert all((directory / n).stat().st_mode & 0o777 == 0o600 for n in (backup.DUMP,))
    kind, count, digest, plain = opened(directory / backup.DUMP, tmp_path / "private")
    assert (kind, plain) == ("database", DUMP)
    assert manifest["database"]["plaintext_sha256"] == digest
    assert manifest["database"]["plaintext_bytes"] == count == len(DUMP)
    kind, count, digest, plain = opened(directory / backup.FILES, tmp_path / "private")
    assert kind == "files" and manifest["files"]["plaintext_sha256"] == digest
    with tarfile.open(fileobj=io.BytesIO(plain)) as archive:
        names = set(archive.getnames())
    layout = RuntimeLayout(deployment)
    web = layout.service_directory / "web.yaml"
    assert str(Path("config") / web.relative_to(deployment.paths["config"])) in names
    password = layout.database_password("web")
    assert (
        str(Path("credentials") / password.relative_to(deployment.paths["credentials"]))
        in names
    )
    assert "media/branding/logo.png" in names
    assert ".stewardship-provisioned.json" in names
    with tarfile.open(fileobj=io.BytesIO(plain)) as archive:
        # Tree roots are recorded owner-only, so extraction cannot widen them.
        roots = {m.name: m for m in archive.getmembers() if "/" not in m.name}
    assert {name: roots[name].mode for name in ("config", "credentials", "media")} == {
        "config": 0o700,
        "credentials": 0o700,
        "media": 0o700,
    }
    assert not any(name.startswith("backups") for name in names)
    written = json.loads((directory / backup.MANIFEST).read_text())
    assert written == manifest
    assert "password" not in json.dumps(manifest).lower()
    assert records == [
        {
            "started_at": datetime.fromisoformat(manifest["started_at"]),
            "database_bytes": len(DUMP),
            "files_bytes": count,
            "manifest_digest": records[0]["manifest_digest"],
            "recipient_fingerprint": manifest["recipient_fingerprint"],
            "application_version": manifest["application_version"],
        }
    ]
    assert len(records[0]["manifest_digest"]) == 64


def test_a_media_file_removed_during_the_backup_is_left_out(deployment, monkeypatch):
    """Branding cleanup may delete a bundle mid-backup; that must not fail it."""
    branding = deployment.paths["media"] / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    kept, removed = branding / "kept.png", branding / "removed.png"
    kept.write_bytes(b"kept")
    removed.write_bytes(b"removed")
    real = backup._file

    def vanishing(archive, name, path, **options):
        """Delete the second file just before it is opened, like a cleanup."""
        if path == removed:
            removed.unlink()
        return real(archive, name, path, **options)

    monkeypatch.setattr(backup, "_file", vanishing)
    sink = io.BytesIO()
    backup.archive_files(deployment, sink)
    with tarfile.open(fileobj=io.BytesIO(sink.getvalue())) as archive:
        names = set(archive.getnames())
    assert "media/branding/kept.png" in names
    assert "media/branding/removed.png" not in names
    # Outside media a vanishing file is a real fault, not a race to tolerate.
    web = RuntimeLayout(deployment).service_directory / "web.yaml"

    def missing(archive, name, path, **options):
        """Make one configuration file disappear."""
        if path == web:
            raise FileNotFoundError(path)
        return real(archive, name, path, **options)

    monkeypatch.setattr(backup, "_file", missing)
    with pytest.raises(FileNotFoundError):
        backup.archive_files(deployment, io.BytesIO())


def test_the_size_bound_refuses_before_reading_a_file(deployment, monkeypatch):
    """A file larger than what remains of the bound is refused unread."""
    branding = deployment.paths["media"] / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    (branding / "large.png").write_bytes(b"x" * 4096)
    monkeypatch.setattr(backup, "MAX_FILES_BYTES", 2048)
    with pytest.raises(ConfigError, match="exceed the backup bound"):
        backup.archive_files(deployment, io.BytesIO())


def test_an_authority_outside_the_archived_trees_refuses_the_run(deployment, tmp_path):
    """A moved authority store would silently be missing from every set."""
    moved = replace(
        deployment,
        paths=replace(
            deployment.paths,
            values={**deployment.paths.values, "authority": tmp_path / "elsewhere"},
        ),
    )
    with pytest.raises(ConfigError, match="authority store"):
        backup.run_backup(moved, record=lambda **facts: None)
    assert not any(deployment.paths["backups"].iterdir())


def test_retention_keeps_the_newest_sets_and_only_dated_directories(deployment):
    """Older dated sets go; anything else in the directory is left alone."""
    backups = deployment.paths["backups"]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(backup.RETAINED_SETS + 2):
        name = (start + timedelta(days=day)).strftime("%Y%m%dT%H%M%SZ")
        (backups / name).mkdir(mode=0o700)
        (backups / name / backup.MANIFEST).write_text("{}")
    (backups / "operator-notes").mkdir(mode=0o700)
    # A failed run's directory has no manifest: it neither counts nor goes.
    failed = backups / (start - timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    failed.mkdir(mode=0o700)
    (failed / backup.DUMP).write_bytes(b"partial")
    backup.run_backup(deployment, record=lambda **facts: None)
    remaining = sorted(p.name for p in backups.iterdir())
    assert "operator-notes" in remaining and failed.name in remaining
    complete = [
        name
        for name in remaining
        if backup.SET_NAME.match(name) and (backups / name / backup.MANIFEST).exists()
    ]
    assert len(complete) == backup.RETAINED_SETS
    assert complete[0] == (start + timedelta(days=3)).strftime("%Y%m%dT%H%M%SZ")


def test_refusals_leave_no_record(deployment, tmp_path):
    """A symlink in a tree, a missing key and a failed dump record nothing."""
    records = []
    layout = RuntimeLayout(deployment)
    link = deployment.paths["config"] / "stray"
    link.symlink_to(tmp_path / "private")
    with pytest.raises(ConfigError, match="non-regular"):
        backup.run_backup(deployment, record=lambda **facts: records.append(facts))
    link.unlink()
    # The failed set stays for inspection, without a manifest; a second run
    # in the same second would find its name taken, so clear it here.
    (failed,) = deployment.paths["backups"].iterdir()
    assert not (failed / backup.MANIFEST).exists()
    shutil.rmtree(failed)
    key = layout.credential("backup_data")
    kept = key.read_bytes()
    key.unlink()
    with pytest.raises(ConfigError):
        backup.run_backup(deployment, record=lambda **facts: records.append(facts))
    write_private(key, kept)

    def failing(configuration, sink, *, recipient):
        raise ConfigError("The database dump did not complete.")

    backup.dump_database, original = failing, backup.dump_database
    try:
        with pytest.raises(ConfigError, match="did not complete"):
            backup.run_backup(deployment, record=lambda **facts: records.append(facts))
    finally:
        backup.dump_database = original
    assert records == []
    for directory in deployment.paths["backups"].iterdir():
        assert not (directory / backup.MANIFEST).exists()


def test_dump_uses_the_password_environment_and_needs_pg_dump(deployment, monkeypatch):
    """The password reaches pg_dump only through its environment."""
    monkeypatch.undo()  # Drop the fixture's stand-in: exercise the real dump step.
    recipient = backup_sealing.Recipient.load(
        RuntimeLayout(deployment).credential("backup_data")
    )
    monkeypatch.setattr(backup.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="not installed"):
        backup.dump_database(deployment, io.BytesIO(), recipient=recipient)
    calls = []

    class Process:
        """A pg_dump that streams the known bytes and exits cleanly."""

        def __init__(self, command, **options):
            calls.append((command, options))
            self.stdout, self.returncode = io.BytesIO(DUMP), 0
            self.stderr = io.BytesIO(b"pg_dump: warning: synthetic\n")

        def wait(self, timeout=None):
            return self.returncode

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    sink = io.BytesIO()
    count, _ = backup.dump_database(deployment, sink, recipient=recipient)
    assert count == len(DUMP)
    command, options = calls[0]
    password = read_private(deployment.postgres.password_file).decode().strip()
    assert options["env"]["PGPASSWORD"] == password
    assert password not in " ".join(command)
    assert command[:2] == ["/usr/bin/pg_dump", "--format=custom"]
    # Owners and ACLs carry every REVOKE from PUBLIC and every runtime grant.
    assert "--no-owner" not in command and "--no-acl" not in command
    assert command[command.index("--username") + 1] == deployment.postgres.user
    assert deployment.postgres.user == "pk_stewardship_backup_worker"


@pytest.mark.parametrize("failure", ["exit", "empty"])
def test_a_failed_dump_is_refused_and_named_by_a_reviewed_event(
    deployment, monkeypatch, caplog, capsys, failure
):
    """A nonzero exit or an empty dump refuses and logs backup_dump_failed.

    The check formats the record exactly as production does: pg_dump's own
    text never reaches the log, and the event survives the formatter.
    """
    from parishkit.stewardship.observability import SafeJsonFormatter

    monkeypatch.undo()
    recipient = backup_sealing.Recipient.load(
        RuntimeLayout(deployment).credential("backup_data")
    )
    noise = b"pg_dump: error: \x01secret\x7f " + b"x" * 20000

    class Process:
        """A pg_dump that fails, or that says nothing at all."""

        def __init__(self, command, **options):
            self.stdout = io.BytesIO(DUMP if failure == "exit" else b"")
            self.stderr = io.BytesIO(noise + b"\n")
            self.returncode = 1 if failure == "exit" else 0

        def wait(self, timeout=None):
            return self.returncode

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(backup.subprocess, "Popen", Process)
    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(ConfigError, match="did not complete"),
    ):
        backup.dump_database(deployment, io.BytesIO(), recipient=recipient)
    formatted = [SafeJsonFormatter().format(record) for record in caplog.records]
    assert any('"backup_dump_failed"' in line for line in formatted)
    assert not any("secret" in line for line in formatted)
    assert capsys.readouterr().out == ""


BACKUP_LOGIN = "pk_stewardship_backup_worker"
BACKUP_ROW = (
    BACKUP_LOGIN,
    BACKUP_LOGIN,
    False,  # superuser
    True,  # bypasses row-level security, for pg_dump
    False,  # create database
    False,  # create role
    False,  # replication
    False,  # inherit
    "pg_read_all_data:true:false",
)


@pytest.mark.parametrize(
    "change, temporary, accepted",
    [
        ({}, False, True),
        ({0: "pk_stewardship_web", 1: "pk_stewardship_web"}, False, False),
        ({1: "pk_stewardship_operator"}, False, False),
        ({2: True}, False, False),
        ({3: False}, False, False),
        ({7: True}, False, False),
        ({8: "pg_read_all_data:true:false,pg_write_all_data:true:false"}, False, False),
        ({8: None}, False, False),
        ({}, True, False),
    ],
)
def test_the_backup_command_admits_only_its_own_login(
    monkeypatch, change, temporary, accepted
):
    """Exactly the backup login's attributes and membership, and no temp authority."""
    from unittest.mock import MagicMock

    import django.db

    from parishkit.stewardship import backup_commands

    row = tuple(change.get(index, value) for index, value in enumerate(BACKUP_ROW))
    database = MagicMock()
    cursor = database.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [row, (temporary,)]
    monkeypatch.setattr(django.db, "connection", database)
    if accepted:
        backup_commands._admit_backup_identity()
    else:
        with pytest.raises(ConfigError):
            backup_commands._admit_backup_identity()


def test_keygen_and_open_commands_roundtrip_and_refuse_generically(
    tmp_path, capsys, monkeypatch
):
    """The console makes a key, opens a set with it and never echoes inputs."""
    from parishkit.stewardship import backup_commands

    # CLI unit tests must not retain handlers bound to a finished capture
    # stream, or later tests' stderr carries a logging error.
    monkeypatch.setattr(backup_commands, "configure_logging", lambda: None)
    key = tmp_path / "operator.key"
    assert main(["backup-keygen", "--destination", str(key)]) == 0
    public = json.loads(capsys.readouterr().out)["public_key"]
    assert key.stat().st_mode & 0o777 == 0o600
    assert main(["backup-keygen", "--destination", str(key)]) == 2
    assert "refused" in capsys.readouterr().err
    (tmp_path / "public").write_text(public + "\n")
    recipient = backup_sealing.Recipient.load(tmp_path / "public")
    sealed = tmp_path / "set.sealed"
    with sealed.open("wb") as sink:
        backup_sealing.seal(
            io.BytesIO(DUMP), sink, recipient=recipient, kind="database"
        )
    out = tmp_path / "set.pgdump"
    assert (
        main(
            [
                "backup-open",
                "--key",
                str(key),
                "--input",
                str(sealed),
                "--destination",
                str(out),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["kind"] == "database"
    assert out.read_bytes() == DUMP
    other = tmp_path / "other.key"
    other.write_text(backup_sealing.generate_keypair()[0])
    bad = tmp_path / "bad.pgdump"
    assert (
        main(
            [
                "backup-open",
                "--key",
                str(other),
                "--input",
                str(sealed),
                "--destination",
                str(bad),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "could not be opened" in captured.err and str(tmp_path) not in captured.err
    assert not bad.exists()
    assert main(["backup", "--config", str(tmp_path / "private-config")]) == 2
    assert "private-config" not in capsys.readouterr().err
