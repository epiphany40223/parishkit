"""The same-host restore command, tools/stewardship-ops/pk-restore (#608).

The host has Docker but not the ParishKit package, so the command is a bash
script. These tests run it with stand-ins on PATH: a `docker` that answers
as a set-up deployment would and records every call, `crontab`,
`systemctl` and `df`, and a `timeout` that skips its options (and answers
124, as an expired limit does, for a call containing FAKE_TIMEOUT). What is
pinned: usage errors and `plan` run no Docker command; every guard (the
database marker, the typed UUID, cron jobs and timers, the files' and the
manifest's digests, the database state each --from implies, the archive's
members, the image's digest and commands, the deployment YAML in the
image, restore-check, free space, a failed container listing) refuses
before anything stops; a run collects static files and validates the YAML
before the stop, moves every tree aside rather than deleting it, restores
the set's files, converts the dump on disk and loads it in one named
transaction, deletes the plaintext copies, retargets, moves the collected
static tree in, ends automation sessions, begins the restore review and
starts only web and caddy; a failed or expired load ends its session,
deletes the copies and says, from the schema comment the load's own
transaction writes, whether it committed and where to resume; a signal
during the load stops it at once (with the real GNU timeout, skipped
without one); and --from resumes from the state it checks. No container
runs, so the image's own commands are out of scope.
"""

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stewardship-ops" / "pk-restore"
UUID = "00000000-0000-4000-8000-000000000000"
IMAGE = "ghcr.io/example/stewardship@sha256:" + "b" * 64
SET = "20261010T020000Z"
HELP = (
    "usage: pk-stewardship [{health,validate-deployment,collect-static,"
    "retarget-image,restore-check,revoke-automation-sessions,restore-begin}]"
)

# Records each call in FAKE_LOG. FAKE_MARKER is the database marker,
# FAKE_RECORDED the recorded manifest digest (gone once a load committed),
# FAKE_COMMENT the public schema's comment (default: what the last
# committed load set), FAKE_LATER_ROWS whether backup rows started at or
# after the set (default: until a load committed), FAKE_DBSIZE the
# database's size in bytes,
# FAKE_DIGESTS what a RepoDigests inspect answers (FAKE_DIGEST_PAD adds a
# long tail after it), FAKE_HELP the image's help text, FAKE_CHECK
# restore-check's exit status, FAKE_VALIDATE validate-deployment's,
# FAKE_BACKUP a running backup-worker's id, FAKE_PS_FAIL a failed listing,
# FAKE_LOAD_STATUS the load's exit status after it committed, FAKE_HANG a
# pattern whose call records its pid and sleeps, FAKE_FAIL a pattern whose
# call exits 1. An `exec` into the postgres container runs its command here
# (psql is emulated, pg_restore is a stand-in), with paths inside the container
# mapped to FAKE_STORE. retarget-image rewrites the record; collect-static
# writes a file into its destination.
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
if [ -n "${FAKE_FAIL:-}" ] && [[ "$*" == *"$FAKE_FAIL"* ]]; then exit 1; fi
if [ -n "${FAKE_HANG:-}" ] && [[ "$*" == *"$FAKE_HANG"* ]]; then
    echo $$ >"$FAKE_STATE/hang.pid"
    exec /bin/sleep 30
fi
last=${*: -1}
host() { echo "${1//\/var\/lib\/postgresql/$FAKE_STORE}"; }
if [[ " $* " == *" exec -T "* ]]; then
    args=("$@")
    i=0
    while [ "${args[$i]}" != exec ]; do i=$((i + 1)); done
    i=$((i + 2))
    while [ "${args[$i]}" = -e ]; do i=$((i + 2)); done
    i=$((i + 1))
    cmd=()
    for a in "${args[@]:$i}"; do cmd+=("$(host "$a")"); done
    [ "${cmd[0]}" != timeout ] || cmd=("${cmd[@]:2}")
    case ${cmd[0]} in
        psql)
            case "$*" in
                *"shobj_description"*)
                    echo "${FAKE_MARKER-parishkit-stewardship:$FAKE_UUID}" ;;
                *"obj_description"*)
                    if [ -n "${FAKE_COMMENT+x}" ]; then comment=$FAKE_COMMENT
                    else comment=$(cat "$FAKE_STATE/comment" 2>/dev/null); fi
                    expected=$(sed -n "s/.* = '\(pk-restore:[^']*\)' AND .*/\1/p" \
                        <<<"$*")
                    later=${FAKE_LATER_ROWS:-}
                    if [ -z "$later" ]; then
                        if [ -e "$FAKE_STATE/comment" ]; then later=0; else later=1; fi
                    fi
                    if [ "$comment" = "$expected" ] && [ "$later" = 0 ]; then echo t
                    else echo f; fi ;;
                *"manifest_digest"*)
                    [ -e "$FAKE_STATE/comment" ] || echo "${FAKE_RECORDED-}" ;;
                *"pg_database_size"*) echo "${FAKE_DBSIZE:-1024}" ;;
                *"pg_stat_activity"*) echo 0 ;;
                *"--single-transaction"*)
                    cp "$FAKE_STORE"/pk-restore-*/restore.sql "$FAKE_STATE/copied"
                    sed -n "s/^COMMENT ON SCHEMA public IS '\(.*\)';$/\1/p" \
                        "$FAKE_STORE"/pk-restore-*/suffix.sql >"$FAKE_STATE/comment"
                    exit "${FAKE_LOAD_STATUS:-0}" ;;
            esac ;;
        pk-stewardship) ;;
        *) exec "${cmd[@]}" ;;
    esac
    exit 0
fi
case "$*" in
    "compose ls"*) echo '[{"Name":"stewardship","ConfigFiles":"'"$FAKE_COMPOSE"'"}]' ;;
    *" ps --services --status running") printf '%s\n' postgres valkey web caddy ;;
    *" config --services") printf '%s\n' postgres valkey web worker scheduler caddy ;;
    "ps --quiet "*)
        [ -z "${FAKE_PS_FAIL:-}" ] || exit 1
        echo "${FAKE_BACKUP:-}" ;;
    *"RepoDigests"*)
        echo "${FAKE_DIGESTS:-$last}"
        [ -z "${FAKE_DIGEST_PAD:-}" ] || yes junk | head -n 200000 ;;
    *" --help") echo "$FAKE_HELP" ;;
    *" validate-deployment "*) exit "${FAKE_VALIDATE:-0}" ;;
    *" restore-check "*)
        echo '{"result": "match"}'
        exit "${FAKE_CHECK:-0}" ;;
    *" retarget-image "*) echo '{"image": "'"$last"'"}' >"$FAKE_RECORD" ;;
    *" collect-static --destination "*) echo collected >"$last/app.js" ;;
esac
exit 0
"""


def stub(bin_dir, name, body):
    """Write one executable stand-in."""
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


def sha256(path):
    """The hex SHA-256 of a file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_tar(path, members):
    """Write a files.tar with the given {name: bytes or None for a directory}."""
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in members.items():
            entry = tarfile.TarInfo(name)
            if data is None:
                entry.type, entry.mode = tarfile.DIRTYPE, 0o700
                archive.addfile(entry)
            else:
                entry.size, entry.mode = len(data), 0o600
                archive.addfile(entry, io.BytesIO(data))


SET_FILES = {
    ".stewardship-provisioned.json": b'{"image": "set"}\n',
    "config": None,
    "config/services": None,
    "config/services/web.yaml": b"set web\n",
    "config/services/compose.json": b"{}\n",
    "credentials": None,
    "credentials/key": b"set key\n",
    "media": None,
    "media/logo.png": b"set logo\n",
}


@pytest.fixture
def host(tmp_path):
    """A deployment root, a sealed set's decrypted files and the stand-ins."""
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "docker", FAKE_DOCKER)
    try:
        subprocess.run([str(bin_dir / "docker")], env={"FAKE_LOG": os.devnull})
    except PermissionError:
        pytest.skip("the temporary directory cannot run the stand-ins")
    stub(bin_dir, "crontab", '#!/usr/bin/env bash\necho "${FAKE_CRONTAB:-}"\n')
    stub(bin_dir, "systemctl", '#!/usr/bin/env bash\necho "${FAKE_TIMERS:-}"\n')
    stub(
        bin_dir,
        "df",
        "#!/usr/bin/env bash\n"
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        "path=${*: -1}\n"
        'if [ -n "${FAKE_OTHER:-}" ] && [[ $path == *"$FAKE_OTHER"* ]]; then\n'
        '    echo "/dev/y 9 1 99999999 1% /other"; exit 0; fi\n'
        "free=${FAKE_FREE:-99999999}\n"
        'if [ -n "${FAKE_FREE_AFTER:-}" ] && '
        'ls "$FAKE_STORE"/pk-restore-*/restore.sql >/dev/null 2>&1; then\n'
        "    free=$FAKE_FREE_AFTER; fi\n"
        'echo "/dev/x 99999999 1 $free 1% /"\n',
    )
    stub(bin_dir, "sleep", "#!/usr/bin/env bash\nexit 0\n")
    # pg_restore --file=SQL DUMP, as the container's would convert it.
    stub(bin_dir, "pg_restore", '#!/usr/bin/env bash\ncp "$2" "${1#--file=}"\n')
    stub(
        bin_dir,
        "timeout",
        "#!/usr/bin/env bash\n"
        'if [ -n "${FAKE_TIMEOUT:-}" ] && [[ "$*" == *"$FAKE_TIMEOUT"* ]]; then '
        "exit 124; fi\n"
        'while [[ $1 == -* ]]; do shift; done; shift; exec "$@"\n',
    )
    root = tmp_path / "root"
    services = root / "config" / "services"
    services.mkdir(parents=True)
    store = root / "run" / "persistent" / "postgresql"
    store.mkdir(parents=True)
    paths = {
        "root": str(root),
        "config": str(root / "config"),
        "credentials": str(root / "credentials"),
        "media": str(root / "run" / "persistent" / "media"),
        "postgresql": str(store),
        "cache": str(root / "cache"),
    }
    document = {"deployment": {"paths": paths, "postgres": {"name": "stw"}}}
    (services / "web.yaml").write_text(json.dumps(document))
    # The set's own rendering, marked so a test can tell it was restored.
    set_web = json.dumps({**document, "set": True}).encode()
    compose = services / "compose.json"
    compose.write_text("{}\n")
    (root / "credentials").mkdir()
    (root / "credentials" / "key").write_text("current key\n")
    (root / "run" / "persistent" / "media").mkdir(parents=True)
    (root / "cache" / "static").mkdir(parents=True)
    (root / "cache" / "static" / "old.js").write_text("old\n")
    (root / "logs").mkdir()
    record = root / ".stewardship-provisioned.json"
    record.write_text('{"image": "current"}\n')
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    dump = decrypted / "database.pgdump"
    dump.write_bytes(b"PGDMP fake dump\n")
    files = decrypted / "files.tar"
    write_tar(files, {**SET_FILES, "config/services/web.yaml": set_web})
    set_dir = tmp_path / "backups" / SET
    set_dir.mkdir(parents=True)
    manifest = set_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "image": IMAGE,
                "migrations": [["stewardship", "0001_initial"]],
                "database": {"plaintext_sha256": sha256(dump)},
                "files": {"plaintext_sha256": sha256(files)},
            }
        )
    )
    yaml = tmp_path / "deployment.yaml"
    yaml.write_text("deployment: {}\n")
    (tmp_path / "state").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "cron.d").mkdir()
    (tmp_path / "crontab").write_text("# m h dom mon dow user command\n")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_LOG": str(tmp_path / "docker.log"),
        "FAKE_STATE": str(tmp_path / "state"),
        "FAKE_COMPOSE": str(compose),
        "FAKE_RECORD": str(record),
        "FAKE_STORE": str(store),
        "FAKE_UUID": UUID,
        "FAKE_HELP": HELP,
        "FAKE_RECORDED": sha256(manifest),
        "PK_RESTORE_OWNER": f"{os.getuid()}:{os.getgid()}",
        "PK_RESTORE_CRON_DIR": str(tmp_path / "cron.d"),
        "PK_RESTORE_CRONTAB": str(tmp_path / "crontab"),
        # mktemp's own default differs by platform (/tmp on Linux, the
        # per-user folder on macOS); pin it so every host path is the test's.
        "TMPDIR": str(tmp_path / "work"),
    }
    return {
        "tmp": tmp_path,
        "root": root,
        "store": store,
        "env": env,
        "dump": dump,
        "files": files,
        "set": set_dir,
        "manifest": manifest,
        "yaml": yaml,
        "record": record,
    }


def arguments(host, *extra):
    """The full option list for a run against the fixture's deployment."""
    return [
        "--confirm-deployment",
        UUID,
        "--set",
        str(host["set"]),
        "--database-dump",
        str(host["dump"]),
        "--files-tar",
        str(host["files"]),
        "--deployment-yaml",
        str(host["yaml"]),
        "--runtime-root",
        str(host["root"]),
        *extra,
    ]


def restore(host, *extra, mode="run", typed=UUID, **fake):
    """Run pk-restore; return (result, the Docker calls of this run)."""
    log = host["tmp"] / "docker.log"
    before = len(log.read_text().splitlines()) if log.exists() else 0
    result = subprocess.run(
        ["bash", str(SCRIPT), mode, *arguments(host, *extra)],
        input=f"{typed}\n",
        env={**host["env"], **fake},
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = log.read_text().splitlines()[before:] if log.exists() else []
    return result, calls


def output(result):
    """Everything the run printed."""
    return result.stdout + result.stderr


def stopped(calls):
    """Whether any call stopped a service or changed the database."""
    return any(" stop " in c or "--single-transaction" in c for c in calls)


def test_the_script_is_executable_bash():
    """An executable bash script that parses."""
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    assert os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_shellcheck_is_clean():
    """The script passes shellcheck as bash."""
    if shutil.which("shellcheck") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks shellcheck")
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "bash", str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "change",
    [
        ("--confirm-deployment", "0000000-0000-4000-8000-000000000000"),
        ("--set", "/tmp/not-a-set"),
        ("--image", "ghcr.io/example/stewardship:v1"),
        ("--from", "1"),
        ("--from", "5"),
        ("--from", "8"),
        ("--project", "Bad Name"),
        ("--runtime-root", "/srv/a,b"),
        ("--bogus", "x"),
    ],
)
def test_usage_errors_run_nothing(host, change):
    """A malformed option is a usage error before any Docker call."""
    result, calls = restore(host, *change)
    assert result.returncode == 2, result.stderr
    assert calls == []


def test_plan_runs_nothing_and_writes_nothing(host):
    """`plan` prints every step and touches neither Docker nor the root."""
    before = sorted(p.relative_to(host["root"]) for p in host["root"].rglob("*"))
    result, calls = restore(host, mode="plan")
    assert result.returncode == 0, result.stderr
    assert calls == []
    for text in (SET, IMAGE, "\n1. ", "\n4. ", "\n6. ", "\n7. ", "\n8. "):
        assert text in result.stdout
    after = sorted(p.relative_to(host["root"]) for p in host["root"].rglob("*"))
    assert after == before


def loaded_marker(host):
    """The schema comment step 6's transaction writes for the fixture's set."""
    return f"pk-restore:{SET}:{sha256(host['manifest'])}"


def test_a_run_restores_and_stops_for_the_review(host):
    """The steps in the runbook's order, keeping every replaced tree."""
    root = host["root"]
    # A plaintext copy an earlier run left behind is deleted by the checks.
    stale = host["store"] / "pk-restore-20261001T000000Z"
    stale.mkdir()
    (stale / "restore.pgdump").write_text("old\n")
    result, calls = restore(host)
    assert result.returncode == 0, output(result)
    joined = "\n".join(calls)

    def first(text):
        return next(i for i, c in enumerate(calls) if text in c)

    # The guards and the offline work come before the stop; then the steps.
    order = [
        "shobj_description",
        "obj_description('public'",
        "manifest_digest",
        "pull --quiet " + IMAGE,
        " validate-deployment --config /run/operator.yaml",
        " restore-check --manifest /set/manifest.json",
        "rm -rf /var/lib/postgresql/pk-restore-*",
        "pg_database_size",
        " collect-static --destination ",
        " stop web worker scheduler caddy",
        "cat > /var/lib/postgresql/pk-restore-",
        "sha256sum /var/lib/postgresql/pk-restore-",
        "pg_restore --file=/var/lib/postgresql/pk-restore-",
        "rm -f /var/lib/postgresql/pk-restore-",
        "-e PGAPPNAME=pk-restore postgres",
        " retarget-image --config /run/operator.yaml --image " + IMAGE,
        " pull --quiet",
        "admin-recovery revoke-automation-sessions",
        f"admin-recovery restore-begin --config {root}/config/services/"
        f"admin-recovery.yaml --backup-at {SET}",
        " up --detach --wait web caddy",
        "pk-stewardship health",
    ]
    positions = [first(text) for text in order]
    assert positions == sorted(positions)
    # The load is one named transaction: the reset first, the marker last.
    load = calls[first("--single-transaction")]
    assert load.index("prefix.sql") < load.index("restore.sql")
    assert load.index("restore.sql") < load.index("suffix.sql")
    assert (host["tmp"] / "state" / "comment").read_text().strip() == (
        loaded_marker(host)
    )
    # A set with migrations in its manifest is checked without its dump.
    assert "--dump" not in joined
    # Nothing is written to a container's memory-backed /tmp. The host's
    # own paths sit under the test's temporary directory, which may itself
    # be under /tmp, so they are left out of the check.
    assert "/tmp/" not in joined.replace(str(host["tmp"]), "<test>")
    # Only web and caddy start; the background services stay stopped.
    assert not any(" up " in c and ("worker" in c or "scheduler" in c) for c in calls)
    # The dump went through pg_restore on disk; every copy is gone, the
    # stale one included.
    copied = (host["tmp"] / "state" / "copied").read_bytes()
    assert copied == host["dump"].read_bytes()
    assert list(host["store"].iterdir()) == []
    # Every replaced tree is kept beside the restored one.
    stamp = next(p.name for p in root.glob("config.pre-restore-*"))[len("config") :]
    assert json.loads((root / "config" / "services" / "web.yaml").read_text())["set"]
    assert (root / f"credentials{stamp}" / "key").read_text() == "current key\n"
    assert (root / "credentials" / "key").read_text() == "set key\n"
    assert (root / "run" / "persistent" / "media" / "logo.png").read_text() == (
        "set logo\n"
    )
    assert (root / "run" / "persistent" / f"media{stamp}").is_dir()
    assert (root / f".stewardship-provisioned.json{stamp}").read_text() == (
        '{"image": "current"}\n'
    )
    assert json.loads(host["record"].read_text()) == {"image": IMAGE}
    assert (root / "credentials" / "key").stat().st_mode & 0o777 == 0o600
    assert (root / "cache" / f"static{stamp}" / "old.js").exists()
    assert [p.name for p in (root / "cache" / "static").iterdir()] == ["app.js"]
    assert not (root / "cache" / "static.next").exists()
    assert not list(root.glob(".restore-staging.*"))
    # Each step is logged with a timestamp, to the terminal and the log.
    logs = list((root / "logs").glob("restore-*.log"))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert "==> " in text and "8. Starting web and caddy" in text
    assert "Restore review" in result.stdout


@pytest.mark.parametrize(
    ("fake", "typed", "message"),
    [
        ({"FAKE_MARKER": "parishkit-stewardship:other"}, UUID, "provisioning marker"),
        ({}, "00000000-0000-4000-8000-000000000001", "typed UUID"),
        ({"FAKE_CRONTAB": "0 2 * * * docker compose run backup-worker"}, UUID, "cron"),
        ({"FAKE_TIMERS": "Mon 02:00 stewardship-backup.timer"}, UUID, "timers"),
        ({"FAKE_FAIL": "manifest_digest"}, UUID, "cannot read stewardship_backup_"),
        ({"FAKE_RECORDED": ""}, UUID, "older restore, or a set this deployment"),
        ({"FAKE_RECORDED": "0" * 64}, UUID, "not the manifest"),
        ({"FAKE_DIGESTS": "ghcr.io/example/other@sha256:" + "b" * 64}, UUID, "carry"),
        ({"FAKE_HELP": "usage: [{health,restore-check}]"}, UUID, "no validate-"),
        ({"FAKE_VALIDATE": "2"}, UUID, "does not load"),
        ({"FAKE_CHECK": "3"}, UUID, "use_image"),
        ({"FAKE_CHECK": "4"}, UUID, "users swap"),
        ({"FAKE_CHECK": "2"}, UUID, "refused"),
        ({"FAKE_FREE": "1000"}, UUID, "KiB free"),
        ({"FAKE_DBSIZE": str(10**12)}, UUID, "the restore needs"),
        ({"FAKE_OTHER": "persistent"}, UUID, "on another filesystem"),
        ({"FAKE_OTHER": "persistent/media"}, UUID, "on another filesystem"),
        ({"FAKE_PS_FAIL": "1"}, UUID, "cannot list running backup-worker"),
    ],
)
def test_every_guard_refuses_before_anything_stops(host, fake, typed, message):
    """A failed check exits 1, says why and has stopped nothing."""
    result, calls = restore(host, typed=typed, **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert "nothing changed" in output(result)
    assert not stopped(calls)
    assert not list(host["root"].glob("*.pre-restore-*"))


def test_the_system_crontab_is_checked(host):
    """A backup line in /etc/crontab refuses too."""
    (host["tmp"] / "crontab").write_text("0 2 * * * root rclone copy x y\n")
    result, calls = restore(host)
    assert result.returncode == 1
    assert "rclone copy" in output(result)
    assert not stopped(calls)


DPKG_TIMER = (
    "Tue 2026-10-13 00:00:00 EDT 14h Mon 2026-10-12 00:00:47 EDT 9h ago "
    "dpkg-db-backup.timer dpkg-db-backup.service"
)


def test_the_os_dpkg_backup_timer_is_ignored(host):
    """Debian and Ubuntu's own dpkg-db-backup timer is not a backup job."""
    result, _ = restore(host, FAKE_TIMERS=DPKG_TIMER)
    assert result.returncode == 0, output(result)


def test_the_dpkg_timer_does_not_hide_a_backup_timer(host):
    """Only the dpkg row is ignored; a backup timer beside it refuses."""
    timers = DPKG_TIMER + "\nMon 02:00 stewardship-backup.timer x.service"
    result, calls = restore(host, FAKE_TIMERS=timers)
    assert result.returncode == 1, output(result)
    assert "timers" in output(result)
    assert "stewardship-backup.timer" in output(result)
    assert not stopped(calls)


def test_the_dpkg_exception_is_for_timers_only(host):
    """A cron line naming the dpkg units is still a backup job."""
    line = "0 2 * * * /usr/local/bin/backup dpkg-db-backup.timer dpkg-db-backup.service"
    result, calls = restore(host, FAKE_CRONTAB=line)
    assert result.returncode == 1, output(result)
    assert "cron" in output(result)
    assert not stopped(calls)


LOCAL_IMAGE = "parishkit-stewardship-local:" + "c" * 40 + "-1791805639"


def local_environment(
    host, clock="normal", image=LOCAL_IMAGE, marker=True, profile="local"
):
    """Make the fixture the local laptop environment's deployment.

    marker and profile are the two signals local mode needs together; a
    clock of None leaves the clock-mode file missing.
    """
    root = host["root"]
    if marker:
        (root / ".parishkit-local").write_text("local\n")
    if clock is not None:
        (root / "run" / "local" / "clock").mkdir(parents=True)
        (root / "run" / "local" / "clock" / "mode").write_text(f"{clock}\n")
    host["yaml"].write_text(f"deployment:\n  profile: {profile}\n")
    manifest = json.loads(host["manifest"].read_text())
    host["manifest"].write_text(json.dumps({**manifest, "image": image}))
    host["env"]["FAKE_RECORDED"] = sha256(host["manifest"])


def test_the_local_environment_restores_with_its_local_tag(host):
    """A local build tag is used as built: never pulled, digest unchecked."""
    local_environment(host)
    result, calls = restore(host)
    assert result.returncode == 0, output(result)
    assert not any(c.startswith("pull ") or " pull " in c for c in calls)
    assert "image inspect " + LOCAL_IMAGE in calls
    assert any(" retarget-image " in c and c.endswith(LOCAL_IMAGE) for c in calls)
    assert any(" up --detach --wait web caddy" in c for c in calls)


@pytest.mark.parametrize(
    ("clock", "fake", "message"),
    [
        ("fake", {}, "clock mode is fake"),
        ("normal", {"FAKE_FAIL": "image inspect"}, "is not in the VM"),
    ],
)
def test_the_local_environment_refuses_before_anything_stops(
    host, clock, fake, message
):
    """A fake-clock deployment or a missing local image refuses."""
    local_environment(host, clock=clock)
    result, calls = restore(host, **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert not stopped(calls)


@pytest.mark.parametrize(
    ("marker", "profile"), [(True, "production"), (False, "local")]
)
def test_one_local_signal_alone_is_not_local_mode(host, marker, profile):
    """Without both the marker and the profile, a local tag is a usage error."""
    local_environment(host, marker=marker, profile=profile)
    result, calls = restore(host)
    assert result.returncode == 2, output(result)
    assert "complete reference" in output(result)
    assert calls == []


def test_the_local_environment_needs_its_clock_mode(host):
    """Both signals with no clock-mode file refuse before anything stops."""
    local_environment(host, clock=None)
    result, calls = restore(host)
    assert result.returncode == 1, output(result)
    assert "clock mode is missing" in output(result)
    assert not stopped(calls)


def test_the_local_plan_says_nothing_is_pulled(host):
    """The plan's checks describe the local image, not a pull."""
    local_environment(host)
    result, calls = restore(host, mode="plan")
    assert result.returncode == 0, output(result)
    assert "nothing is pulled" in result.stdout
    assert "is pulled and carries" not in result.stdout
    assert calls == []


def test_the_local_environment_takes_only_a_local_tag(host):
    """A registry digest there is a usage error, and nothing runs."""
    local_environment(host, image=IMAGE)
    result, calls = restore(host)
    assert result.returncode == 2, output(result)
    assert "LOCAL tag" in output(result)
    assert calls == []


def test_a_compose_file_outside_the_root_is_refused(host, tmp_path):
    """The project must be this runtime root's."""
    other = tmp_path / "elsewhere.json"
    other.write_text("{}\n")
    result, calls = restore(host, "--compose", str(other))
    assert result.returncode == 1
    assert "is not inside" in output(result)
    assert calls == []


def test_a_long_digest_list_is_read_whole(host):
    """grep -q on a pipe under pipefail would fail on a long answer."""
    result, _ = restore(host, FAKE_DIGEST_PAD="1")
    assert result.returncode == 0, output(result)


def test_a_file_that_does_not_match_the_manifest_is_refused(host):
    """The decrypted copies must be the set's own."""
    host["dump"].write_bytes(b"another dump\n")
    result, calls = restore(host)
    assert result.returncode == 1
    assert "database digest" in output(result)
    assert not stopped(calls)


def test_an_archive_with_other_members_is_refused(host):
    """A member a backup never writes refuses before anything moves."""
    write_tar(host["files"], {**SET_FILES, "etc/passwd": b"x\n"})
    manifest = json.loads(host["manifest"].read_text())
    manifest["files"]["plaintext_sha256"] = sha256(host["files"])
    host["manifest"].write_text(json.dumps(manifest))
    result, calls = restore(host, FAKE_RECORDED=sha256(host["manifest"]))
    assert result.returncode == 1
    assert "never writes" in output(result)
    assert not stopped(calls)


def test_an_old_set_needs_an_image_and_is_checked_with_its_dump(host):
    """A set from before #608 names no image or migrations."""
    manifest = json.loads(host["manifest"].read_text())
    del manifest["image"], manifest["migrations"]
    host["manifest"].write_text(json.dumps(manifest))
    result, calls = restore(host, mode="plan")
    assert result.returncode == 2 and "--image" in result.stderr
    fake = {"FAKE_RECORDED": sha256(host["manifest"])}
    result, calls = restore(host, "--image", IMAGE, **fake)
    assert result.returncode == 0, output(result)
    assert any("--dump /set/database.pgdump" in c for c in calls)


def test_a_running_backup_is_waited_for_with_a_logged_limit(host):
    """A backup that never ends gives up with what, limit and elapsed."""
    result, calls = restore(host, FAKE_BACKUP="abc123", PK_RESTORE_BACKUP_WAIT="0")
    assert result.returncode == 1
    assert "TIMEOUT: waiting for the running backup-worker" in output(result)
    assert "(limit 0s)" in output(result)
    assert not stopped(calls)


def test_an_expired_load_ends_its_session_and_did_not_commit(host):
    """A killed load is logged, ended in PostgreSQL and its copies deleted."""
    result, calls = restore(host, FAKE_TIMEOUT="--single-transaction")
    assert result.returncode == 1
    text = output(result)
    assert "TIMEOUT: the database load killed after " in text
    assert "(limit 3600s)" in text
    assert "The load did not commit" in text
    assert "run again with --from 6" in text
    assert any("pg_terminate_backend" in c for c in calls)
    assert list(host["store"].iterdir()) == []
    log = next((host["root"] / "logs").glob("restore-*.log")).read_text()
    assert "TIMEOUT: the database load" in log


def test_an_already_loaded_set_resumes_at_seven(host):
    """A full run over the loaded set says to resume at step 7."""
    result, calls = restore(host, FAKE_COMMENT=loaded_marker(host), FAKE_LATER_ROWS="0")
    assert result.returncode == 1
    assert "already loaded set" in output(result)
    assert "run again with --from 7" in output(result)
    assert not stopped(calls)


def test_a_load_that_committed_before_failing_says_resume_at_seven(host):
    """The schema comment, not the exit status, says whether it committed."""
    result, _ = restore(host, FAKE_LOAD_STATUS="1")
    assert result.returncode == 1
    assert "The load committed" in output(result)
    assert "run again with --from 7" in output(result)
    assert list(host["store"].iterdir()) == []
    # The resume it names then finishes.
    result, calls = restore(host, "--from", "7")
    assert result.returncode == 0, output(result)
    assert not any("--single-transaction" in c for c in calls)


def test_a_failed_conversion_never_loads_and_resumes_at_six(host):
    """A dump pg_restore cannot convert never reaches psql; --from 6 works."""
    result, calls = restore(host, FAKE_FAIL="pg_restore --file")
    assert result.returncode == 1
    assert not any("--single-transaction" in c for c in calls)
    assert "The load did not commit" in output(result)
    assert "run again with --from 6" in output(result)
    assert list(host["store"].iterdir()) == []
    result, calls = restore(host, "--from", "6")
    assert result.returncode == 0, output(result)
    assert any("--single-transaction" in c for c in calls)


def test_the_room_is_checked_again_with_the_real_sql(host):
    """A converted SQL larger than estimated stops before the load."""
    result, calls = restore(host, FAKE_FREE_AFTER="1000")
    assert result.returncode == 1
    assert "beside the converted SQL; the load needs" in output(result)
    assert not any("--single-transaction" in c for c in calls)
    assert "The load did not commit" in output(result)
    assert list(host["store"].iterdir()) == []


def test_from_six_needs_the_files_restored(host):
    """Without step 4's files in place, --from 6 refuses."""
    result, calls = restore(host, "--from", "6")
    assert result.returncode == 1
    assert "step 4 did not finish; run again with --from 4" in output(result)
    assert not stopped(calls)


def test_from_seven_needs_the_load_committed(host):
    """While the set's own row is there and unmarked, step 6 has not run."""
    result, calls = restore(host, "--from", "7")
    assert result.returncode == 1
    assert "has not loaded set" in output(result)
    assert "run again with --from 6" in output(result)
    assert not stopped(calls)


def test_from_seven_without_row_or_marker_is_restored_by_hand(host):
    """A database with neither proof is never taken as restored."""
    result, calls = restore(host, "--from", "7", FAKE_RECORDED="")
    assert result.returncode == 1
    assert "restore it by hand" in output(result)
    assert "--from" not in output(result).split("pk-restore:")[-1]
    assert not stopped(calls)


def test_from_seven_skips_the_files_and_the_database(host):
    """A resume after the load does not repeat it or read the old row."""
    result, calls = restore(
        host,
        "--from",
        "7",
        FAKE_COMMENT=loaded_marker(host),
        FAKE_RECORDED="",
        FAKE_LATER_ROWS="0",
    )
    assert result.returncode == 0, output(result)
    joined = "\n".join(calls)
    assert "pg_restore" not in joined and "--single-transaction" not in joined
    assert "restore-begin" in joined
    assert not list(host["root"].glob("config.pre-restore-*"))


@pytest.mark.parametrize(
    "comment",
    [
        f"pk-restore:20261009T020000Z:{'a' * 64}",
        f"pk-restore:{SET}:{'a' * 64}",
    ],
)
def test_from_seven_refuses_another_sets_marker(host, comment):
    """A marker for another set or digest is no proof this set loaded."""
    result, calls = restore(
        host, "--from", "7", FAKE_COMMENT=comment, FAKE_LATER_ROWS="0"
    )
    assert result.returncode == 1
    assert "has not loaded set" in output(result)
    assert not stopped(calls)


def test_a_stale_marker_with_later_backups_is_not_a_fresh_load(host):
    """The marker survives into later backups; their rows give it away."""
    fake = {"FAKE_COMMENT": loaded_marker(host), "FAKE_LATER_ROWS": "1"}
    result, calls = restore(host, "--from", "7", FAKE_RECORDED="", **fake)
    assert result.returncode == 1
    assert "restore it by hand" in output(result)
    assert not stopped(calls)
    # A full run does not take it as loaded either.
    result, calls = restore(host, **fake)
    assert result.returncode == 0, output(result)
    assert any("--single-transaction" in c for c in calls)


def test_a_failed_file_restore_removes_its_staging_directory(host):
    """The plaintext staging copy does not outlive a failed step 4."""
    persistent = host["root"] / "run" / "persistent"
    persistent.chmod(0o500)
    try:
        result, _ = restore(host)
    finally:
        persistent.chmod(0o700)
    assert result.returncode == 1
    assert "Removed the unfinished staging directory" in output(result)
    assert "run again with --from 4" in output(result)
    assert not list(host["root"].glob(".restore-staging.*"))


def gnu_timeout():
    """A GNU coreutils timeout (gtimeout on macOS), or None."""
    for name in ("timeout", "gtimeout"):
        found = shutil.which(name)
        if found:
            version = subprocess.run(
                [found, "--version"], capture_output=True, text=True
            ).stdout
            if "GNU coreutils" in version:
                return found
    return None


@pytest.mark.parametrize(
    ("name", "number", "hang"),
    [
        ("TERM", 15, "--single-transaction"),
        ("INT", 2, "--single-transaction"),
        ("HUP", 1, "--single-transaction"),
        ("TERM", 15, "cat > /var/lib/postgresql"),
        ("TERM", 15, "exec pg_restore"),
    ],
)
def test_a_signal_stops_step_six_at_once(host, name, number, hang):
    """A signal during the copy, conversion or load stops it at once.

    The load's session is ended, any conversion in the container stopped by
    its recorded pid, and the copies deleted.

    Runs with the real GNU timeout, so the background job, its timeout and
    the stand-in docker it waits on are all real processes.
    """
    real = gnu_timeout()
    if real is None:
        pytest.skip("GNU timeout (or gtimeout) is not installed")
    timeout = Path(host["env"]["PATH"].split(":")[0]) / "timeout"
    timeout.unlink()
    timeout.symlink_to(real)
    state = host["tmp"] / "state"
    # The typed confirmation comes from a file: on Python 3.12, communicate()
    # flushes a stdin pipe the test already closed and raises.
    typed = host["tmp"] / "typed"
    typed.write_text(UUID + "\n")
    with typed.open() as typed_in:
        process = subprocess.Popen(
            ["bash", str(SCRIPT), "run", *arguments(host)],
            stdin=typed_in,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**host["env"], "FAKE_HANG": hang},
            text=True,
        )
    deadline = time.monotonic() + 30
    while not (state / "hang.pid").exists():
        assert time.monotonic() < deadline, "the load never started"
        time.sleep(0.05)
    hung = int((state / "hang.pid").read_text())
    started = time.monotonic()
    process.send_signal(getattr(signal, "SIG" + name))
    text, _ = process.communicate(timeout=20)
    assert time.monotonic() - started < 10, "the signal waited for the load"
    assert process.returncode == 128 + number
    assert f"INTERRUPTED: SIG{name} in step 6" in text
    assert "The load did not commit" in text
    assert list(host["store"].iterdir()) == []
    log = next((host["root"] / "logs").glob("restore-*.log")).read_text()
    calls = (host["tmp"] / "docker.log").read_text()
    assert "INTERRUPTED" in log and "pg_terminate_backend" in calls
    # The conversion inside the container is stopped by its recorded pid.
    assert "pk-restore-*/convert.pid" in calls
    assert "run again with --from 6" in log
    # The hung client was stopped, not left behind.
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(hung, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "the load's client is still running"
        time.sleep(0.05)


def test_an_image_without_automation_sessions_skips_revoking(host):
    """A release without automation sessions has nothing to end."""
    help_text = (
        "usage: [{validate-deployment,collect-static,retarget-image,"
        "restore-check,restore-begin}]"
    )
    result, calls = restore(host, FAKE_HELP=help_text)
    assert result.returncode == 0, output(result)
    assert not any("revoke-automation-sessions" in c for c in calls)
