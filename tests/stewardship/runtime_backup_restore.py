"""A real backup restored into a fresh PostgreSQL, with the image's own tools.

The automated restore test (#305, #392): after the operational scenario has
completed the setup wizard (a promoted ParishSoft snapshot, a Family campaign
with a sealed Family code, keyrings and schedules), this module takes a
backup exactly as the host's cron does, then follows the backup runbook's
restore with the same image: open the set with the kept key, check it with
``restore-check``, compare its schema with ``restore-compare`` on a scratch
server, and restore it into a new, empty PostgreSQL as a replacement host
does (``database-roles``, then step 6's commands). It passes only when every
table holds the rows it held at the backup and ``web`` starts and reports
ready on the restored database.

Everything runs against the scenario's disposable Compose project and named
volume, with a throwaway key pair and synthetic data only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from uuid import uuid4

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.runtime_paths import PROVISIONING_RECORD, RuntimeLayout
from parishkit.stewardship.runtime_topology import POSTGRES_IMAGE

# Every public table's row count, one "table=count" line each, read by the
# superuser operator login (which row-level security does not filter).
ROW_COUNTS = (
    "SELECT c.relname || '=' || (xpath('/row/n/text()', query_to_xml("
    "format('SELECT count(*) AS n FROM public.%I', c.relname), false, true, '')"
    "))[1]::text FROM pg_class c JOIN pg_namespace s ON s.oid=c.relnamespace "
    "WHERE s.nspname='public' AND c.relkind IN ('r','p') ORDER BY 1"
)
# Whole-row digests of a few tables whose values matter most to a restore:
# the applied migrations, the campaign, each Family's sealed code and the
# promoted source snapshot. Equal digests mean equal rows, not just counts.
CONTENT = "SELECT " + ",".join(
    f"(SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
    f"FROM public.{table} t)"
    for table in (
        "django_migrations",
        "stewardship_campaign",
        "stewardship_family_campaign",
        "stewardship_source_snapshot",
    )
)
# The runbook's flags for the release image run on the operator's machine.
OFFLINE = (
    "--rm",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
)


def docker(*arguments, timeout=120, check=True):
    """One Docker CLI call; a failure shows its output (synthetic data only)."""
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=timeout
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def as_me():
    """The runbook's ``--user "$(id -u):$(id -g)"``: files stay the tester's."""
    return ("--user", f"{os.getuid()}:{os.getgid()}")


def bind(source, target, *, readonly=True):
    """One ``--mount`` bind argument pair."""
    suffix = ",readonly" if readonly else ""
    return ("--mount", f"type=bind,source={source},target={target}{suffix}")


def install_backup_key(image, keys, root, configuration, *, recorded_image):
    """Make the kept key pair with ``backup-keygen`` and install its public half.

    As at install time (the runbook's "The key"): the private key stays in
    ``keys``, outside the runtime, and only the bare public key is written
    as the ``backup_data`` credential in the seed tree ``root``, before the
    scenario copies that tree into its volume. The seed has no
    ``provision-runtime`` run, so the provisioning record it would leave
    (which every set archives and whose image its manifest names) is written
    here in that command's shape. Returns the key's fingerprint.
    """
    keys.mkdir(mode=0o700)
    made = docker(
        "run",
        *OFFLINE,
        *as_me(),
        *bind(keys, "/keys", readonly=False),
        image,
        "backup-keygen",
        "--destination",
        "/keys/stewardship-backup.key",
    )
    printed = json.loads(made.stdout)
    credential = RuntimeLayout(configuration).credential("backup_data")
    staged = root / credential.relative_to(configuration.paths.root)
    staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(staged, (printed["public_key"] + "\n").encode())
    record = {
        "version": 1,
        "deployment": deployment_document(configuration, switches=False),
        "image": recorded_image,
        "checkout": None,
        "bind_source_root": None,
    }
    # The seed root itself is not owner-only; the scenario's volume copy
    # makes every file 0600 and owned by the service user.
    (root / PROVISIONING_RECORD).write_text(json.dumps(record))
    return printed["recipient_fingerprint"]


def operator_sql(file, project, configuration, query):
    """One query as the operator login inside the database container."""
    from .test_operational_compose import compose_run

    return compose_run(
        file,
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "pk_stewardship_operator",
        "-d",
        configuration.postgres.name,
        "-v",
        "ON_ERROR_STOP=1",
        "-Atc",
        query,
    ).stdout.strip()


def stop_online(file, project):
    """Restore step 1: stop every running service but PostgreSQL and Valkey."""
    from .test_operational_compose import compose_run

    running = compose_run(
        file, project, "ps", "--services", "--status", "running"
    ).stdout.split()
    online = sorted(set(running) - {"postgres", "valkey"})
    if online:
        compose_run(file, project, "stop", "--timeout", "10", *online, timeout=120)


def copy_set(volume, configuration, image, destination):
    """Copy the one backup set off the volume, as the off-host copy would.

    ``docker cp`` writes the files as the tester, so the runbook's
    ``backup-open`` can read them with ``--user`` and no extra capability.
    """
    backups = configuration.paths["backups"].relative_to(configuration.paths.root)
    holder = "parishkit-restore-copy-" + uuid4().hex
    docker(
        "create",
        "--name",
        holder,
        "--network",
        "none",
        "--mount",
        f"type=volume,src={volume},dst=/fixture,readonly",
        image,
    )
    try:
        destination.mkdir(mode=0o700)
        docker("cp", f"{holder}:/fixture/{backups}/.", str(destination))
    finally:
        docker("rm", "--force", holder, check=False)
    sets = [path for path in destination.iterdir() if path.is_dir()]
    assert len(sets) == 1, sorted(path.name for path in destination.iterdir())
    return sets[0]


def open_set(image, keys, backup_set, output, fingerprint):
    """Restore step 2: ``backup-open`` both sealed files with the kept key.

    Each opened file's size and digest must be the manifest's, and the key's
    fingerprint the one ``backup-keygen`` printed.
    """
    output.mkdir(mode=0o700)
    manifest = json.loads((backup_set / "manifest.json").read_text())
    for kind, sealed, plain in (
        ("database", "database.pgdump.sealed", "database.pgdump"),
        ("files", "files.tar.sealed", "files.tar"),
    ):
        opened = docker(
            "run",
            *OFFLINE,
            *as_me(),
            *bind(keys, "/keys"),
            *bind(backup_set, "/set"),
            *bind(output, "/out", readonly=False),
            image,
            "backup-open",
            "--key",
            "/keys/stewardship-backup.key",
            "--input",
            f"/set/{sealed}",
            "--destination",
            f"/out/{plain}",
        )
        facts = json.loads(opened.stdout)
        assert facts["recipient_fingerprint"] == fingerprint
        assert facts["plaintext_bytes"] == manifest[kind]["plaintext_bytes"]
        assert facts["plaintext_sha256"] == manifest[kind]["plaintext_sha256"]
    return output / "database.pgdump", output / "files.tar"


def check_files(archive, configuration):
    """The archive holds the runtime trees and the record, by tree name."""
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    credential = RuntimeLayout(configuration).credential("backup_data")
    assert {"config", "credentials", "media", ".stewardship-provisioned.json"} <= names
    assert str(credential.relative_to(configuration.paths.root)) in names


def restore_check(image, backup_set, dump):
    """Restore step 2's check: the set's migrations are exactly this image's,
    read from the manifest and from the dump alike."""
    checked = docker(
        "run",
        *OFFLINE,
        *as_me(),
        *bind(backup_set, "/set"),
        *bind(dump.parent, "/out"),
        image,
        "restore-check",
        "--manifest",
        "/set/manifest.json",
        "--dump",
        f"/out/{dump.name}",
    )
    report = json.loads(checked.stdout)
    assert report["result"] == "match", report
    assert report["backup"]["migrations_from"] == "manifest"


def restore_compare(image, dump, scratch):
    """The runbook's comparison on a scratch PostgreSQL 18.6 on an internal
    network: the set's schema must be exactly what this image migrates.

    The password file is mounted through its private directory rather than
    as one file (the runbook's form): Docker Desktop shows a single-file bind
    as root's, which the owner-only check refuses, while native Linux, where
    CI runs, shows either form as the tester's.
    """
    scratch.mkdir(mode=0o700)
    password = scratch / "pw"
    write_private(password, uuid4().hex.encode())
    name = "parishkit-restore-scratch-" + uuid4().hex
    docker("network", "create", "--internal", name)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            name,
            "--cap-drop",
            "ALL",
            *(
                argument
                for capability in (
                    "CHOWN",
                    "DAC_OVERRIDE",
                    "FOWNER",
                    "SETGID",
                    "SETUID",
                )
                for argument in ("--cap-add", capability)
            ),
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/var/lib/postgresql",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/scratch/pw",
            *bind(scratch, "/run/scratch"),
            POSTGRES_IMAGE,
        )
        ready = time.monotonic() + 60
        while (
            docker(
                "exec",
                name,
                "pg_isready",
                "-U",
                "postgres",
                "-h",
                "127.0.0.1",
                check=False,
            ).returncode
            != 0
        ):
            assert time.monotonic() < ready, docker("logs", name, check=False).stderr
            time.sleep(0.5)
        compared = docker(
            "run",
            "--rm",
            "--network",
            name,
            *as_me(),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            *bind(dump.parent, "/out"),
            *bind(scratch, "/run/scratch"),
            image,
            "restore-compare",
            "--dump",
            f"/out/{dump.name}",
            "--scratch-host",
            name,
            "--scratch-password-file",
            "/run/scratch/pw",
            timeout=600,
            check=False,
        )
        assert compared.returncode == 0, compared.stdout + compared.stderr
        assert json.loads(compared.stdout)["result"] == "same"
    finally:
        docker("rm", "--force", "--volumes", name, check=False)
        docker("network", "rm", name, check=False)


def empty_database_directory(volume, configuration, image):
    """A replacement host's PostgreSQL starts from nothing: empty this
    disposable scenario's data directory, inside its own volume only."""
    data = configuration.paths["postgresql"].relative_to(configuration.paths.root)
    docker(
        "run",
        *OFFLINE,
        "--user",
        "10001:10001",
        "--mount",
        f"type=volume,src={volume},dst=/fixture",
        "--entrypoint",
        "python",
        image,
        "-c",
        "import shutil, sys\nfrom pathlib import Path\n"
        "for path in Path(sys.argv[1]).iterdir():\n"
        "    shutil.rmtree(path) if path.is_dir() else path.unlink()\n",
        f"/fixture/{data}",
    )


def restore_database(file, project, configuration, deployment, dump):
    """Restore steps 5 and 6 on the new, empty cluster, as the runbook says."""
    from .test_operational_compose import compose_run

    layout = RuntimeLayout(configuration)
    compose_run(
        file, project, "up", "--detach", "--wait", "postgres", "valkey", timeout=120
    )
    # Step 5: the roles, with the restored password files.
    compose_run(
        file,
        project,
        "run",
        "--rm",
        "database-provision",
        "database-roles",
        "--config",
        str(layout.service_directory / "database-provision.yaml"),
        "--confirm-deployment",
        deployment,
    )
    # Step 6, command for command: copy in, convert, then empty and load the
    # schema in one transaction.
    prefix = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(file),
        "exec",
        "-T",
        "postgres",
    ]
    with dump.open("rb") as stream:
        copied = subprocess.run(
            [*prefix, "sh", "-c", "cat > /tmp/restore.pgdump"],
            stdin=stream,
            capture_output=True,
            timeout=120,
        )
    assert copied.returncode == 0, copied.stderr
    for command in (
        ["pg_restore", "--file=/tmp/restore.sql", "/tmp/restore.pgdump"],
        [
            "sh",
            "-c",
            'printf "%s\\n" "DROP SCHEMA public CASCADE;" "CREATE SCHEMA public;" '
            '"GRANT USAGE ON SCHEMA public TO PUBLIC;" > /tmp/prefix.sql',
        ],
        [
            "psql",
            "--username",
            "pk_stewardship_operator",
            "--dbname",
            configuration.postgres.name,
            "--single-transaction",
            "-v",
            "ON_ERROR_STOP=1",
            "--quiet",
            "-f",
            "/tmp/prefix.sql",
            "-f",
            "/tmp/restore.sql",
        ],
        ["rm", "/tmp/restore.pgdump", "/tmp/restore.sql", "/tmp/prefix.sql"],
    ):
        compose_run(file, project, "exec", "-T", "postgres", *command, timeout=300)


def wait_for_web(file, project, configuration):
    """Step 8 on a drill host: ``web`` alone, its health check, then ``health``."""
    from .test_operational_compose import compose_run

    web = str(RuntimeLayout(configuration).service_directory / "web.yaml")
    compose_run(file, project, "up", "--detach", "--no-deps", "web", timeout=120)
    deadline = time.monotonic() + 90
    while (
        compose_run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "healthcheck",
            check=False,
        ).returncode
        != 0
    ):
        if time.monotonic() >= deadline:
            logs = compose_run(file, project, "logs", "web", check=False)
            raise AssertionError("Restored web never became live: " + logs.stdout)
        time.sleep(0.5)
    health = compose_run(
        file, project, "exec", "-T", "web", "pk-stewardship", "health", "--config", web
    )
    assert json.loads(health.stdout)["ready"] is True


def check_backup_restore(
    file,
    project,
    configuration,
    volume,
    deployment,
    *,
    image,
    keys,
    fingerprint,
    work,
    invariants,
):
    """Back up the completed scenario, restore the set into a fresh cluster
    and prove it is the same database (see the module docstring).

    ``invariants`` is one operator query whose answer must survive the
    restore unchanged.
    """
    from .test_operational_compose import compose_run

    stop_online(file, project)
    before = operator_sql(file, project, configuration, ROW_COUNTS)
    counts = dict(line.split("=") for line in before.splitlines())
    # Not a vacuous comparison: the wizard's Family and campaign rows exist.
    assert int(counts["stewardship_family_campaign"]) >= 1, counts
    assert int(counts["django_migrations"]) > 0, counts
    proof = operator_sql(file, project, configuration, invariants)
    content = operator_sql(file, project, configuration, CONTENT)
    # The host's cron command, under the backup profile's own SQL identity.
    taken = json.loads(
        compose_run(file, project, "run", "--rm", "backup-worker", timeout=300).stdout
    )
    assert taken["backup_recorded"] is True
    assert taken["recipient_fingerprint"] == fingerprint
    assert taken["recipient_source"] == "file"
    assert taken["offsite"] == {"state": "not_configured"}
    work.mkdir(mode=0o700)
    backup_set = copy_set(volume, configuration, image, work / "set")
    # The set's origin: its manifest is the one the backup recorded.
    digest = hashlib.sha256((backup_set / "manifest.json").read_bytes()).hexdigest()
    assert digest == taken["manifest_digest"]
    # The opened plaintext (database dump and files archive) is removed
    # even when a check fails, so no decrypted copy outlives the test.
    opened = work / "out"
    try:
        dump, archive = open_set(image, keys, backup_set, opened, fingerprint)
        check_files(archive, configuration)
        restore_check(image, backup_set, dump)
        restore_compare(image, dump, work / "scratch")
        # A replacement host: same files, a new and empty PostgreSQL 18.6.
        compose_run(file, project, "stop", "--timeout", "10", timeout=120)
        empty_database_directory(volume, configuration, image)
        restore_database(file, project, configuration, deployment, dump)
        # The set's own row is written after its dump, so the restored database
        # holds exactly what the database held before the backup.
        assert operator_sql(file, project, configuration, ROW_COUNTS) == before
        assert operator_sql(file, project, configuration, invariants) == proof
        assert operator_sql(file, project, configuration, CONTENT) == content
        wait_for_web(file, project, configuration)
    finally:
        shutil.rmtree(opened, ignore_errors=True)
