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
                *"SELECT pg_reload_conf()"*)
                    printf "%s\n" "2026-10-25 04:00:00.000001+00" t ;;
                *" -v since=infinity"*)
                    # The hba check of the file alone, before the reload:
                    # FAKE_HBA_FILE, or what every good file answers.
                    cat >/dev/null
                    echo "${FAKE_HBA_FILE:-hba_reload_pending}" ;;
                *" -v since="*)
                    # FAKE_HBA: the hba check's answers after the reload,
                    # one per call, the last repeated (the query on stdin).
                    cat >/dev/null
                    read -r -a answers <<<"${FAKE_HBA:-t}"
                    echo x >>"$FAKE_STATE/hba"
                    n=$(wc -l <"$FAKE_STATE/hba")
                    [ "$n" -le "${#answers[@]}" ] || n=${#answers[@]}
                    echo "${answers[$((n - 1))]}" ;;
                *"shobj_description"*)
                    echo "${FAKE_MARKER-parishkit-stewardship:$FAKE_UUID}" ;;
                *"obj_description"*)
                    if [ -n "${FAKE_COMMENT+x}" ]; then echo "$FAKE_COMMENT"
                    else cat "$FAKE_STATE/comment" 2>/dev/null; fi ;;
                *"started_at >= "*)
                    later=${FAKE_LATER_ROWS:-}
                    if [ -z "$later" ]; then
                        if [ -e "$FAKE_STATE/comment" ]; then later=0; else later=1; fi
                    fi
                    if [ "$later" = 0 ]; then echo f; else echo t; fi ;;
                *"relnamespace"*)
                    if [ -n "${FAKE_RELATIONS:-}" ]; then echo "$FAKE_RELATIONS"
                    elif [ -e "$FAKE_STATE/comment" ]; then echo 5; else echo 0; fi ;;
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
# The database network layout (#2111), as in test_upgrade_script: when
# FAKE_LAYOUT_RUNNING names the layout Docker runs (OLD or NEW),
# FAKE_NETWORKS_<layout> is what `network inspect` answers and
# FAKE_POSTGRES_<layout> the networks postgres is on; retarget-image renders
# FAKE_COMPOSE_<FAKE_RENDER_LAYOUT>, and starting postgres and valkey after it
# moves Docker to it.
layout=$(cat "$FAKE_STATE/layout" 2>/dev/null || echo "${FAKE_LAYOUT_RUNNING:-}")
networks_var=FAKE_NETWORKS_$layout
postgres_var=FAKE_POSTGRES_$layout
case "$*" in
    "network ls --quiet --filter "*)
        [ -z "$layout" ] || jq -r '.[].Id' <<<"${!networks_var}" ;;
    "network inspect --format {{.Name}}"*)
        jq -r --arg id "$last" '.[] | select(.Id == $id) | .Name' \
            <<<"${!networks_var}" ;;
    "network inspect net-"*) echo "${!networks_var}" ;;
    "network rm "*) echo "$last" >>"$FAKE_STATE/networks_removed" ;;
    "inspect --format "*"NetworkSettings.Networks"*" id-caddy")
        echo "stewardship_proxy " ;;
    "ps --all --quiet --filter "*"service=postgres"*) echo id-postgres ;;
    "inspect id-postgres")
        # Without a layout, postgres runs on the networks the file renders.
        on=${!postgres_var:-$(jq -c '.services.postgres.networks // {}
            | with_entries(.key |= "stewardship_" + .)' "$FAKE_COMPOSE")}
        echo '[{"State": {"Running": true},' \
            '"NetworkSettings": {"Networks": '"$on"'}}]' ;;
    "ps --all --filter "*"{{.ID}} {{.State}}"*)
        printf '%s\n' "id-postgres running postgres False" \
            "id-valkey running valkey False"
        # FAKE_LAYOUT_CADDY: a same-host restore's caddy, stopped in step 1.
        [ -z "${FAKE_LAYOUT_CADDY:-}" ] || echo "id-caddy exited caddy False" ;;
    *" up --detach --wait postgres valkey")
        # Before the retarget (step 5) the set's own rendering starts.
        [ ! -e "$FAKE_STATE/rendered" ] ||
            cp "$FAKE_STATE/rendered" "$FAKE_STATE/layout" ;;
    *" --entrypoint pg_restore "*)
        # The set's runtime row, as pg_restore --data-only prints it.
        printf 'COPY public.stewardship_system_configuration %s FROM stdin;\n' \
            '(created_at, id, mode)'
        printf '2026-10-10\t%s\ttesting\n\\.\n' "${FAKE_SET_ID:-$FAKE_UUID}" ;;
    "ps --all --format "*) printf '%b' "${FAKE_ALL:-}" ;;
    "ps --all --filter "*) printf '%s\n' ${FAKE_CREATED:-postgres valkey web} ;;
    *" ps --all --quiet web") echo webid ;;
    *"NetworkSettings.Networks"*)
        networks=${FAKE_NETWORKS-stewardship_backend stewardship_application-egress}
        for n in $networks; do
            grep -qxF "$n" "$FAKE_STATE/disconnected" 2>/dev/null || echo "$n"
        done ;;
    "network inspect "*)
        case $last in *backend | *proxy) echo true ;; *) echo false ;; esac ;;
    "network disconnect "*)
        [ -n "${FAKE_STUCK:-}" ] || echo "$3" >>"$FAKE_STATE/disconnected" ;;
    *"PortBindings"*) echo "${FAKE_PORTS:-0} ${FAKE_PUBLISH_ALL:-false}" ;;
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
    *" retarget-image "*)
        echo '{"image": "'"$last"'"}' >"$FAKE_RECORD"
        if [ -n "${FAKE_RENDER_LAYOUT:-}" ]; then
            rendered_var=FAKE_COMPOSE_$FAKE_RENDER_LAYOUT
            echo "${!rendered_var}" >"$FAKE_COMPOSE"
            echo "$FAKE_RENDER_LAYOUT" >"$FAKE_STATE/rendered"
        fi ;;
    *" collect-static --destination "*) echo collected >"$last/app.js" ;;
    *" hba-check --config "*) echo "SELECT 'hba';" ;;
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
    """Write a files.tar with the given {name: bytes or None for a directory}.

    A ``Path`` value is a symbolic link to that path.
    """
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in members.items():
            entry = tarfile.TarInfo(name)
            if isinstance(data, Path):
                entry.type, entry.linkname = tarfile.SYMTYPE, str(data)
                archive.addfile(entry)
            elif data is None:
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
    # This host's name and addresses, and what the public origin resolves to.
    stub(
        bin_dir,
        "hostname",
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = -I ]; then echo "${FAKE_ADDRESSES-10.0.0.5}"; '
        'else echo "${FAKE_HOSTNAME:-drillbox}"; fi\n',
    )
    stub(
        bin_dir,
        "getent",
        '#!/usr/bin/env bash\n[ -n "${FAKE_RESOLVES-203.0.113.9}" ] || exit 2\n'
        'echo "${FAKE_RESOLVES-203.0.113.9} STREAM $2"\n',
    )
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
        "backups": str(root / "backups"),
        "logs": str(root / "logs"),
        "reports": str(root / "reports"),
        "run": str(root / "run"),
        "persistent_root": str(root / "run" / "persistent"),
        "caddy": str(root / "run" / "persistent" / "caddy"),
        "valkey": str(root / "run" / "persistent" / "valkey"),
    }
    document = {
        "deployment": {
            "paths": paths,
            "postgres": {"name": "stw"},
            "public_origin": "https://stewardship.example.org",
        }
    }
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
    yaml.chmod(0o644)  # a world-readable file, as Production keeps it
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


def test_an_unreadable_yaml_is_named_not_called_an_unknown_field(host):
    """A YAML the container user cannot read gets the plain refusal."""
    host["yaml"].chmod(0o200)  # the test user is the container user
    result, calls = restore(host)
    assert result.returncode == 1, output(result)
    text = output(result)
    assert "not readable by the container's user" in text
    assert str(host["yaml"]) in text
    assert "0644" in text
    assert "does not load" not in text
    assert not stopped(calls)


def _as_root_for_other_owner(host):
    """Make the script think it runs as root so a foreign owner is allowed.

    Without it the run refuses earlier ("run as root") for any owner but the
    test user, so the group and other columns could not be reached.
    """
    fake_id = Path(host["env"]["PATH"].split(":")[0]) / "id"
    fake_id.write_text('#!/bin/sh\n[ "$1" = -u ] && echo 0 || exec /usr/bin/id "$@"\n')
    fake_id.chmod(0o755)


def test_a_group_readable_yaml_is_accepted_for_the_runtime_group(host):
    """Mode 0640 passes when the container user shares the file's group."""
    host["yaml"].chmod(0o640)
    # Another uid in the file's own group: the group column decides.
    _as_root_for_other_owner(host)
    host["env"]["PK_RESTORE_OWNER"] = f"{os.getuid() + 1}:{host['yaml'].stat().st_gid}"
    result, _ = restore(host)
    assert "not readable by the container's user" not in output(result)


def test_an_other_only_yaml_is_refused_for_a_stranger(host):
    """Mode 0640 refuses when the container user is neither owner nor group."""
    host["yaml"].chmod(0o640)
    _as_root_for_other_owner(host)
    host["env"]["PK_RESTORE_OWNER"] = (
        f"{os.getuid() + 1}:{host['yaml'].stat().st_gid + 1}"
    )
    result, calls = restore(host)
    assert result.returncode == 1, output(result)
    assert "not readable by the container's user" in output(result)
    assert not stopped(calls)


def test_a_readable_but_invalid_yaml_keeps_the_unknown_field_message(host):
    """A world-readable YAML that fails validation keeps the old advice."""
    host["yaml"].chmod(0o644)
    result, calls = restore(host, FAKE_VALIDATE="2")
    assert result.returncode == 1, output(result)
    assert "does not load" in output(result)
    assert "not readable" not in output(result)
    assert not stopped(calls)


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


# --- A replacement host (--replacement) and a drill host (--drill) ---------

HELP_NEW_HOST = HELP.replace("restore-begin}", "restore-begin,database-roles}")


@pytest.fixture
def new_host(host):
    """The same set, restored onto a host with no runtime root yet.

    The drill marker (a test hook) names the stand-in hostname, so --drill
    admits this host; the set's own web.yaml (in files.tar) names the paths
    and the public origin, which resolves to another host's address unless
    FAKE_RESOLVES says otherwise ("" for no address).
    """
    shutil.rmtree(host["root"])
    marker = host["tmp"] / "restore-drill-host"
    marker.write_text("parishkit-restore-drill-host drillbox\n")
    host["env"]["PK_RESTORE_DRILL_MARKER"] = str(marker)
    host["env"]["PK_RESTORE_TEST_HOOKS"] = "1"
    host["env"]["FAKE_HELP"] = HELP_NEW_HOST
    host["marker"] = marker
    return host


def new(host, *extra, mode="run", typed=UUID, **fake):
    """Run pk-restore --replacement with the recorded manifest digest."""
    return restore(
        host,
        "--replacement",
        "--manifest-digest",
        sha256(host["manifest"]),
        *extra,
        mode=mode,
        typed=typed,
        **fake,
    )


def changed(host, calls):
    """Whether anything past the checks happened on the new host."""
    lock = host["root"] / "run" / "startup.lock"
    return lock.exists() or any(
        " up " in c or "database-roles" in c or "collect-static" in c for c in calls
    )


@pytest.mark.parametrize(
    "extra",
    [
        ("--replacement",),
        ("--drill",),
        ("--manifest-digest", "0" * 64),
        ("--replacement", "--manifest-digest", "not-hex"),
    ],
)
def test_replacement_usage_errors_run_nothing(host, extra):
    """--replacement needs a digest; the digest is only for --replacement."""
    result, calls = restore(host, *extra)
    assert result.returncode == 2, output(result)
    assert calls == []


def test_the_local_environment_has_no_replacement(host):
    """The laptop environment restores in place only."""
    (host["root"] / ".parishkit-local").write_text("")
    host["yaml"].write_text("profile: local\n")
    tag = "parishkit-stewardship-local:" + "a" * 40 + "-1"
    result, calls = new(host, "--image", tag)
    assert result.returncode == 2, output(result)
    assert "no replacement host" in output(result)
    assert calls == []


@pytest.mark.parametrize("drill", [False, True])
def test_the_replacement_plan_runs_nothing(new_host, drill):
    """`plan` lists steps 3 to 8 and creates nothing, not even the root."""
    extra = ("--drill",) if drill else ()
    result, calls = new(new_host, *extra, mode="plan")
    assert result.returncode == 0, output(result)
    assert calls == []
    assert not new_host["root"].exists()
    for text in ("\n3. ", "\n4. ", "\n5. ", "\n6. ", "\n7. ", "\n8. "):
        assert text in result.stdout
    assert ("web alone" in result.stdout) is drill
    assert ("drill marker" in result.stdout) is drill


def test_a_replacement_prepares_the_host_and_creates_the_roles(new_host):
    """Steps 3, 4, 5, 6, 7 and 8 in order onto a host with nothing yet."""
    root = new_host["root"]
    result, calls = new(new_host)
    assert result.returncode == 0, output(result)

    def first(text):
        return next(i for i, c in enumerate(calls) if text in c)

    order = [
        "ps --all --format",
        "pull --quiet " + IMAGE,
        " validate-deployment --config /run/operator.yaml",
        " restore-check --manifest /set/manifest.json",
        "--entrypoint pg_restore",
        " collect-static --destination ",
        " up --detach --wait postgres valkey",
        f"database-provision database-roles --config {root}/config/services/"
        f"database-provision.yaml --confirm-deployment {UUID}",
        "shobj_description",
        "cat > /var/lib/postgresql/pk-restore-",
        "-e PGAPPNAME=pk-restore postgres",
        " retarget-image --config /run/operator.yaml --image " + IMAGE,
        " pull --quiet",
        "admin-recovery restore-begin",
        " up --detach --wait web caddy",
        "pk-stewardship health",
    ]
    positions = [first(text) for text in order]
    assert positions == sorted(positions)
    # A new host has nothing to stop and nothing to move aside.
    assert not stopped(calls[: first("--single-transaction")])
    assert not list(root.rglob("*.pre-restore-*"))
    # Step 3's directories and the startup interlock, private to the owner.
    for name in ("backups", "cache", "logs", "reports", "run"):
        assert (root / name).stat().st_mode & 0o777 == 0o700
    for name in ("caddy/config", "caddy/data", "postgresql", "valkey"):
        assert (root / "run" / "persistent" / name).is_dir()
    lock = root / "run" / "startup.lock"
    assert lock.read_text() == "parishkit-stewardship-startup-v1\n"
    assert lock.stat().st_mode & 0o777 == 0o600
    # The set's files are in place, and the static files were collected.
    assert (root / "credentials" / "key").read_text() == "set key\n"
    assert json.loads(new_host["record"].read_text()) == {"image": IMAGE}
    assert [p.name for p in (root / "cache" / "static").iterdir()] == ["app.js"]
    assert (new_host["tmp"] / "state" / "comment").read_text().strip() == (
        loaded_marker(new_host)
    )
    assert list(new_host["store"].iterdir()) == []
    assert len(list((root / "logs").glob("restore-*.log"))) == 1


def test_an_unrecorded_digest_goes_ahead_and_says_so(new_host):
    """Without the printed digest nothing proves the set's origin."""
    result, _ = restore(
        new_host, "--replacement", "--manifest-digest", "unrecorded", mode="run"
    )
    assert result.returncode == 0, output(result)
    assert "nothing proves where set" in output(result)


@pytest.mark.parametrize(
    ("setup", "fake", "extra", "typed", "message"),
    [
        (
            None,
            {"FAKE_ALL": "stewardship|web|/srv/x/compose.json\\n"},
            (),
            UUID,
            "a container of project stewardship",
        ),
        ("store", {}, (), UUID, "already has a database"),
        ("config", {}, (), UUID, "already holds a deployment"),
        ("record", {}, (), UUID, "already holds a deployment"),
        (
            None,
            {"FAKE_SET_ID": "00000000-0000-4000-8000-00000000000f"},
            (),
            UUID,
            "runtime row names another",
        ),
        (None, {}, (), "00000000-0000-4000-8000-000000000001", "typed UUID"),
        (None, {"FAKE_CRONTAB": "0 2 * * * rclone copy a b"}, (), UUID, "cron"),
        (None, {"FAKE_HELP": HELP}, (), UUID, "no database-roles"),
        (None, {"FAKE_CHECK": "3"}, (), UUID, "use_image"),
        (None, {"FAKE_FREE": "1000"}, (), UUID, "KiB free"),
        (None, {}, ("--compose", "/elsewhere/compose.json"), UUID, "not inside"),
    ],
)
def test_every_replacement_guard_refuses_before_anything_changes(
    new_host, setup, fake, extra, typed, message
):
    """A failed check exits 1, says why and has prepared nothing."""
    root = new_host["root"]
    if setup == "store":
        new_host["store"].mkdir(parents=True)
        (new_host["store"] / "PG_VERSION").write_text("18\n")
    elif setup == "config":
        (root / "config").mkdir(parents=True)
    elif setup == "record":
        root.mkdir()
        new_host["record"].write_text("{}\n")
    result, calls = new(new_host, *extra, typed=typed, **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert "nothing changed" in output(result)
    assert not changed(new_host, calls)


def test_a_wrong_manifest_digest_refuses(new_host):
    """The manifest must be the one the backup printed."""
    result, calls = restore(
        new_host, "--replacement", "--manifest-digest", "0" * 64, mode="run"
    )
    assert result.returncode == 1, output(result)
    assert "not the manifest the backup recorded" in output(result)
    assert not changed(new_host, calls)


def test_a_path_outside_the_root_is_restored_by_hand(new_host):
    """Every path of the set must be under the runtime root."""
    with tarfile.open(new_host["files"]) as archive:
        document = json.loads(archive.extractfile("config/services/web.yaml").read())
    document["deployment"]["paths"]["media"] = "/srv/media"
    write_tar(
        new_host["files"],
        {**SET_FILES, "config/services/web.yaml": json.dumps(document).encode()},
    )
    manifest = json.loads(new_host["manifest"].read_text())
    manifest["files"]["plaintext_sha256"] = sha256(new_host["files"])
    new_host["manifest"].write_text(json.dumps(manifest))
    result, calls = new(new_host)
    assert result.returncode == 1, output(result)
    assert "outside" in output(result)
    assert not changed(new_host, calls)


def test_a_failed_role_step_resumes_at_five(new_host):
    """A failure in step 5 names --from 5, which then finishes the restore."""
    result, _ = new(new_host, FAKE_FAIL="database-roles")
    assert result.returncode == 1, output(result)
    assert "run again with --from 5" in output(result)
    result, calls = new(new_host, "--from", "5")
    assert result.returncode == 0, output(result)
    # Steps 3 and 4 are not repeated; the static files are collected again.
    assert not any("startup.lock" in c for c in calls)
    assert any(" collect-static " in c for c in calls)
    assert any("database-roles" in c for c in calls)
    assert any("--single-transaction" in c for c in calls)


def test_a_failed_load_on_a_new_host_did_not_commit_and_resumes_at_six(new_host):
    """An empty database answers that the load did not commit."""
    result, _ = new(new_host, FAKE_FAIL="--single-transaction")
    assert result.returncode == 1, output(result)
    assert "did not commit" in output(result)
    assert "run again with --from 6" in output(result)
    result, calls = new(new_host, "--from", "6")
    assert result.returncode == 0, output(result)
    assert not any("database-roles" in c for c in calls)


def test_from_five_needs_the_files_restored(new_host):
    """--from 5 before step 4 finished refuses."""
    result, calls = new(new_host, "--from", "5")
    assert result.returncode == 1, output(result)
    assert "step 4 did not finish" in output(result)
    assert not changed(new_host, calls)


def test_from_six_refuses_a_database_with_other_data(new_host):
    """A replacement loads only into an empty database."""
    result, _ = new(new_host, FAKE_FAIL="--single-transaction")
    assert result.returncode == 1
    result, calls = new(new_host, "--from", "6", FAKE_RELATIONS="40")
    assert result.returncode == 1, output(result)
    assert "already holds data" in output(result)
    assert not any("--single-transaction" in c for c in calls)


def test_from_seven_on_a_new_host_needs_the_set_loaded(new_host):
    """--from 7 checks the load's own proof."""
    result, _ = new(new_host, FAKE_FAIL="retarget-image")
    assert result.returncode == 1
    assert "run again with --from 7" in output(result)
    result, calls = new(new_host, "--from", "7", FAKE_COMMENT="")
    assert result.returncode == 1, output(result)
    assert "has not loaded" in output(result)
    result, calls = new(new_host, "--from", "7")
    assert result.returncode == 0, output(result)
    assert not any("--single-transaction" in c for c in calls)


def test_a_drill_starts_web_alone_with_no_route_out(new_host):
    """No caddy, no pull, and web is cut off from egress before it starts."""
    result, calls = new(new_host, "--drill")
    assert result.returncode == 0, output(result)

    def first(text):
        return next(i for i, c in enumerate(calls) if text in c)

    order = [
        "admin-recovery restore-begin",
        " create web",
        "network disconnect stewardship_application-egress webid",
        "PortBindings",
        "start webid",
        "pk-stewardship health",
        "ps --all --filter",
    ]
    positions = [first(text) for text in order]
    assert positions == sorted(positions)
    assert not any("caddy" in c and (" up " in c or " start " in c) for c in calls)
    assert not any(" up " in c and " web" in c for c in calls)
    assert not any("compose" in c and c.endswith(" pull --quiet") for c in calls)
    assert not any("network disconnect stewardship_backend" in c for c in calls)
    assert "Destroy this host" in result.stdout


def test_a_drill_resolving_elsewhere_runs(new_host):
    """An origin that resolves to another host's address is not this host."""
    result, _ = new(new_host, "--drill", FAKE_RESOLVES="203.0.113.9")
    assert result.returncode == 0, output(result)


@pytest.mark.parametrize(
    ("marker", "fake", "message"),
    [
        (None, {}, "is missing"),
        ("parishkit-restore-drill-host otherbox\n", {}, "does not mark this host"),
        ("parishkit-restore-drill-host\n", {}, "does not mark this host"),
        (
            "parishkit-restore-drill-host stewardship.example.org\n",
            {"FAKE_HOSTNAME": "stewardship.example.org"},
            "the set's public origin",
        ),
        (
            "parishkit-restore-drill-host drillbox\n",
            {"FAKE_RESOLVES": "10.0.0.5"},
            "resolves to this host",
        ),
    ],
)
def test_every_drill_guard_refuses_before_anything_changes(
    new_host, marker, fake, message
):
    """Without the drill marker, or on the origin's own host, nothing runs."""
    if marker is None:
        new_host["marker"].unlink()
    else:
        new_host["marker"].write_text(marker)
    result, calls = new(new_host, "--drill", **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert "nothing changed" in output(result)
    assert not changed(new_host, calls)


def test_a_drill_against_a_live_deployment_refuses(host):
    """A host that holds a deployment (Production's shape) is never drilled."""
    marker = host["tmp"] / "restore-drill-host"
    marker.write_text("parishkit-restore-drill-host drillbox\n")
    result, calls = restore(
        host,
        "--drill",
        "--manifest-digest",
        sha256(host["manifest"]),
        PK_RESTORE_DRILL_MARKER=str(marker),
        PK_RESTORE_TEST_HOOKS="1",
        FAKE_HELP=HELP_NEW_HOST,
    )
    assert result.returncode == 1, output(result)
    assert "already holds a deployment" in output(result)
    assert not stopped(calls)
    assert not any(" up " in c or "pull" in c for c in calls)


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        ({"FAKE_STUCK": "1"}, "which is not internal"),
        ({"FAKE_PORTS": "1"}, "publishes a port"),
        ({"FAKE_PUBLISH_ALL": "true"}, "publishes a port"),
    ],
)
def test_a_drill_web_with_a_route_out_never_starts(new_host, fake, message):
    """web starts only once it is on internal networks alone."""
    result, calls = new(new_host, "--drill", **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert not any(c.startswith("start ") for c in calls)
    # The web it created and refused is removed.
    assert "rm --force webid" in calls


def test_a_drill_with_other_containers_refuses(new_host):
    """Only postgres, valkey and web may exist on a drill host."""
    result, _ = new(new_host, "--drill", FAKE_CREATED="postgres valkey web caddy")
    assert result.returncode == 1, output(result)
    assert "other containers (caddy)" in output(result)


def test_a_drill_needs_the_origin_to_resolve(new_host):
    """An origin that does not resolve cannot be shown not to be this host."""
    result, calls = new(new_host, "--drill", FAKE_RESOLVES="")
    assert result.returncode == 1, output(result)
    assert "does not resolve" in output(result)
    assert not changed(new_host, calls)


def test_the_drill_marker_path_is_fixed_without_the_test_hook(new_host):
    """PK_RESTORE_DRILL_MARKER counts only for the tests' own hook."""
    result, calls = new(new_host, "--drill", PK_RESTORE_TEST_HOOKS="")
    assert result.returncode == 1, output(result)
    assert "/etc/parishkit/restore-drill-host" in output(result)
    assert not changed(new_host, calls)


def test_a_renderer_named_project_under_the_root_is_refused(new_host):
    """A project started without -p (any name) from the root counts too."""
    compose = new_host["root"] / "config" / "services" / "compose.json"
    listing = f"parishkit-stewardship|postgres|{compose}\\n"
    result, calls = new(new_host, FAKE_ALL=listing)
    assert result.returncode == 1, output(result)
    assert "project parishkit-stewardship" in output(result)
    assert not changed(new_host, calls)


def test_an_unlistable_store_is_refused(new_host):
    """A store that cannot be listed is never taken for an empty one."""
    if os.geteuid() == 0:
        pytest.skip("root lists any directory")
    new_host["store"].mkdir(parents=True)
    new_host["store"].chmod(0)
    try:
        result, calls = new(new_host)
    finally:
        new_host["store"].chmod(0o700)
    assert result.returncode == 1, output(result)
    assert "cannot list" in output(result)
    assert not changed(new_host, calls)


@pytest.mark.parametrize(
    ("drill", "service", "admitted"),
    [
        (False, "caddy", True),
        (True, "caddy", False),
        (False, "worker", False),
        (False, "mail-dispatch", False),
    ],
)
def test_a_resume_admits_only_the_restore_containers(
    new_host, drill, service, admitted
):
    """--from 5 refuses a background or (drilling) caddy container."""
    extra = ("--drill",) if drill else ()
    result, _ = new(new_host, *extra, FAKE_FAIL="database-roles")
    assert result.returncode == 1, output(result)
    compose = new_host["root"] / "config" / "services" / "compose.json"
    listing = "".join(
        f"stewardship|{name}|{compose}\\n" for name in ("postgres", "valkey", service)
    )
    result, calls = new(new_host, *extra, "--from", "5", FAKE_ALL=listing)
    assert (result.returncode == 0) is admitted, output(result)
    if not admitted:
        assert "a resume admits only" in output(result)
        assert not any("database-roles" in c for c in calls)


def test_another_project_from_the_root_is_refused_on_resume(new_host):
    """A resume never runs beside a second project from the same root."""
    result, _ = new(new_host, FAKE_FAIL="database-roles")
    assert result.returncode == 1
    compose = new_host["root"] / "config" / "services" / "compose.json"
    result, calls = new(
        new_host,
        "--from",
        "5",
        FAKE_ALL=f"parishkit-stewardship|postgres|{compose}\\n",
    )
    assert result.returncode == 1, output(result)
    assert "also runs from" in output(result)
    assert not any("database-roles" in c for c in calls)


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        ({"FAKE_FAIL": "NetworkSettings.Networks"}, "cannot list web's networks"),
        ({"FAKE_NETWORKS": ""}, "web is on no network"),
    ],
)
def test_a_drill_web_whose_networks_cannot_be_shown_never_starts(
    new_host, fake, message
):
    """A failed or empty network listing refuses; it never passes vacuously."""
    result, calls = new(new_host, "--drill", **fake)
    assert result.returncode == 1, output(result)
    assert message in output(result)
    assert not any(c.startswith("start ") for c in calls)
    assert "rm --force webid" in calls


def test_a_drill_host_with_no_address_is_refused(new_host):
    """An empty hostname -I cannot show this host is not the origin's."""
    result, calls = new(new_host, "--drill", FAKE_ADDRESSES="")
    assert result.returncode == 1, output(result)
    assert "lists no address" in output(result)
    assert not changed(new_host, calls)


# --- PostgreSQL's connection sources (#2111) --------------------------------


def layout(running, rendered):
    """The stand-in's FAKE_* settings for a database network layout change."""
    from .test_upgrade_script import LAYOUT

    return {
        "FAKE_LAYOUT_RUNNING": running,
        "FAKE_RENDER_LAYOUT": rendered,
        **{"FAKE_" + name.upper(): value for name, value in LAYOUT.items()},
    }


@pytest.mark.parametrize("drill", [False, True], ids=["same host", "drill"])
def test_a_set_from_before_the_source_rules_recreates_the_stores_first(request, drill):
    """postgres and valkey move to the rendered networks before any one-off.

    postgres runs on the networks of the set's (older) rendering, from the
    current deployment or from step 5; once retarget-image renders the
    source rules, every container is removed and postgres and valkey start
    again on the rendered networks, before revoke-automation-sessions and
    restore-begin need the admin-recovery network.
    """
    if drill:
        result, calls = new(
            request.getfixturevalue("new_host"), "--drill", **layout("OLD", "NEW")
        )
    else:
        result, calls = restore(request.getfixturevalue("host"), **layout("OLD", "NEW"))
    assert result.returncode == 0, output(result)
    retarget = next(i for i, c in enumerate(calls) if " retarget-image " in c)
    stop = next(i for i, c in enumerate(calls) if c.endswith(" stop postgres valkey"))
    remove = calls.index("rm id-postgres id-valkey")
    start = max(
        i
        for i, c in enumerate(calls)
        if c.endswith(" up --detach --wait postgres valkey")
    )
    recovery = next(i for i, c in enumerate(calls) if " restore-begin " in c)
    assert retarget < stop < remove < start < recovery
    assert "the database networks change; recreating postgres and valkey" in output(
        result
    )
    assert "network backend differs from the rendered one (ip_range)" in output(result)


def test_an_unchanged_layout_restores_without_recreating_the_stores(host):
    """A set rendered with the same networks needs no restart of postgres."""
    result, calls = restore(host, **layout("NEW", "NEW"))
    assert result.returncode == 0, output(result)
    assert not any(c.endswith(" stop postgres valkey") for c in calls)
    assert "the database networks change" not in output(result)


def test_the_database_sources_are_checked_from_web_and_admin_recovery(host):
    """An image with the source check runs it from both services after health."""
    help_text = HELP.replace("restore-begin}", "restore-begin,database-sources}")
    result, calls = restore(host, FAKE_HELP=help_text)
    assert result.returncode == 0, output(result)
    health = next(i for i, c in enumerate(calls) if "pk-stewardship health" in c)
    web = next(
        i
        for i, c in enumerate(calls)
        if " exec -T web " in c and "database-sources" in c
    )
    oneoff = next(
        i for i, c in enumerate(calls) if " admin-recovery database-sources " in c
    )
    assert health < web < oneoff
    assert calls[web].endswith(
        f"database-sources --config {host['root']}/config/services/web.yaml"
    )
    # Without the command (a release before #2111) nothing is checked; the
    # set is loaded already, so the repeat resumes at step 7. Step 7 moved
    # the static tree aside under the run's per-second stamp; clear that so
    # a repeat within the same second does not refuse to reuse the name.
    for aside in (host["root"] / "cache").glob("static.pre-restore-*"):
        shutil.rmtree(aside)
    result, calls = restore(host, "--from", "7")
    assert result.returncode == 0, output(result)
    assert not any("database-sources" in c for c in calls)
    assert "has no database source check" in output(result)


def test_a_source_difference_fails_the_restore_but_leaves_web_up(host):
    """The login and source were printed by the check; the review must wait."""
    help_text = HELP.replace("restore-begin}", "restore-begin,database-sources}")
    result, calls = restore(host, FAKE_HELP=help_text, FAKE_FAIL="database-sources")
    assert result.returncode == 1, output(result)
    assert "or the database sources differ from the rendered ones" in output(result)
    assert "do not release the Restore review" in output(result)
    assert "Stopped in step" not in output(result)
    assert not any(c.startswith(("rm ", "stop ")) for c in calls)


def test_a_stopped_caddy_keeps_its_networks(host):
    """Step 1 stopped caddy; the full stop must not take its networks away."""
    result, calls = restore(host, FAKE_LAYOUT_CADDY="1", **layout("OLD", "NEW"))
    assert result.returncode == 0, output(result)
    assert any(c.endswith(" id-caddy") and "NetworkSettings" in c for c in calls)
    removed = (host["tmp"] / "state" / "networks_removed").read_text().split()
    assert removed == ["net-backend"]


HELP_HBA = HELP.replace("restore-begin}", "restore-begin,database-sources,hba-check}")


def rules(running, rendered):
    """A layout whose retargeted file renders PostgreSQL's source rules."""
    from .test_upgrade_script import _rendered

    fake = layout(running, rendered)
    document = _rendered(rendered == "NEW")
    document["services"]["postgres"]["command"] = ["postgres", "-c", "hba_file=/x"]
    fake[f"FAKE_COMPOSE_{rendered}"] = json.dumps(document)
    return fake


@pytest.mark.parametrize("running", ["NEW", "OLD"], ids=["matching", "recreated"])
def test_the_rendered_rules_are_reloaded_and_checked_before_any_one_off(host, running):
    """Recreated or not, postgres reloads the rules and the check answers t."""
    result, calls = restore(host, FAKE_HELP=HELP_HBA, **rules(running, "NEW"))
    assert result.returncode == 0, output(result)
    retarget = next(i for i, c in enumerate(calls) if " retarget-image " in c)
    render = next(i for i, c in enumerate(calls) if " hba-check --config " in c)
    pending = next(i for i, c in enumerate(calls) if " -v since=infinity" in c)
    reload = next(i for i, c in enumerate(calls) if "pg_reload_conf()" in c)
    checks = [i for i, c in enumerate(calls) if " -v since=2026-10-25" in c]
    begin = next(i for i, c in enumerate(calls) if " restore-begin " in c)
    web = next(
        i
        for i, c in enumerate(calls)
        if " exec -T web " in c and "database-sources" in c
    )
    # Rendered offline and read-only, like the retarget.
    assert "--network none" in calls[render] and "readonly" in calls[render]
    assert retarget < render < pending < reload < checks[0] < begin
    # The source check repeats the hba check, since the probes cannot tell
    # the rendered method from another.
    assert begin < checks[-1] < web
    assert "the rendered source rules are in force" in output(result)
    assert "the hba check answers t" in output(result)
    stopped = any(c.endswith(" stop postgres valkey") for c in calls)
    assert stopped is (running == "OLD")


@pytest.mark.parametrize(
    ("answers", "why"),
    [
        ({"FAKE_HBA_FILE": "hba_file_differs"}, "the mounted pg_hba.conf differs"),
        ({"FAKE_HBA": "hba_file_invalid"}, "pg_hba.conf has errors"),
        ({"FAKE_HBA": "hba_file_not_mounted"}, "PostgreSQL does not use the mounted"),
    ],
)
def test_rules_not_in_force_stop_the_restore_before_any_one_off(host, answers, why):
    """Nothing connects to a database on rules the check did not prove."""
    result, calls = restore(host, FAKE_HELP=HELP_HBA, **rules("NEW", "NEW"), **answers)
    assert result.returncode == 1, output(result)
    assert f"The hba check failed: {why}" in output(result)
    assert "source rules are not in force" in output(result)
    assert not any(" restore-begin " in c for c in calls)


def test_an_image_with_rules_but_no_hba_check_is_refused(host):
    """A rendering with rules needs the image's check to prove them."""
    help_text = HELP.replace("restore-begin}", "restore-begin,database-sources}")
    result, calls = restore(host, FAKE_HELP=help_text, **rules("NEW", "NEW"))
    assert result.returncode == 1, output(result)
    assert "renders source rules but has no hba-check" in output(result)
    assert not any(" restore-begin " in c for c in calls)


def test_a_failed_hba_check_at_the_source_check_fails_with_web_up(host):
    """Rules that stop being the rendered ones after step 7 fail the run."""
    result, calls = restore(
        host, FAKE_HELP=HELP_HBA, FAKE_HBA="t hba_file_differs", **rules("NEW", "NEW")
    )
    assert result.returncode == 1, output(result)
    assert "The hba check failed: the mounted pg_hba.conf differs" in output(result)
    assert "do not release the Restore review" in output(result)
    assert any(" restore-begin " in c for c in calls)
    assert not any(c.startswith(("rm ", "stop ")) for c in calls)


def test_a_rendering_without_rules_reloads_and_checks_nothing(host):
    """A restore with an image from before #2111 has no rules to prove."""
    result, calls = restore(host, FAKE_HELP=HELP_HBA, **layout("NEW", "NEW"))
    assert result.returncode == 0, output(result)
    for fragment in ("hba-check", "pg_reload_conf", " -v since="):
        assert not any(fragment in c for c in calls), fragment


# ---------------------------------------------------------------------------
# The off-site uploader (#235): an image that lists uploader-password-check.
# Step 4 keeps the host's password file for a set without one, step 7 runs
# database-grants --after-restore SET, and step 8 checks the sources from
# the backup-uploader profile too.

HELP_UPLOADER = HELP.replace(
    "restore-begin}",
    "restore-begin,database-sources,database-grants,uploader-password-check}",
)
UPLOADER_FILE = Path("credentials") / "database" / "backup-uploader"


def uploader_set(host, password=None, extra=None):
    """Re-seal the fixture's set with a credentials/database directory.

    With ``password``, the set carries its own uploader password file;
    ``extra`` adds or replaces members (``False`` drops one). The manifest's
    plaintext digest
    follows the new files.tar.
    """
    set_web = json.dumps(
        {
            **json.loads((host["root"] / "config/services/web.yaml").read_text()),
            "set": True,
        }
    ).encode()
    members = {
        **SET_FILES,
        "config/services/web.yaml": set_web,
        "credentials/database": None,
        "credentials/database/web": b"set web password\n",
    }
    if password is not None:
        members[str(UPLOADER_FILE)] = password
    members.update(extra or {})
    write_tar(host["files"], {k: v for k, v in members.items() if v is not False})
    manifest = json.loads(host["manifest"].read_text())
    manifest["files"]["plaintext_sha256"] = sha256(host["files"])
    host["manifest"].write_text(json.dumps(manifest))
    host["env"]["FAKE_RECORDED"] = sha256(host["manifest"])


def host_password(host, value=b"the host's uploader password\n", tree=None):
    """Give the host (or a moved-aside ``tree``) an uploader password file."""
    path = (tree or host["root"] / "credentials") / "database" / "backup-uploader"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def index(calls, text):
    """The first call containing ``text``."""
    return next(i for i, c in enumerate(calls) if text in c)


def test_an_uploader_image_keeps_the_hosts_password_and_runs_the_grants(host):
    """A set from before the login: the host's file, then grants after restore."""
    import stat

    uploader_set(host)
    host_password(host)
    result, calls = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 0, output(result)
    restored = host["root"] / UPLOADER_FILE
    assert restored.read_bytes() == b"the host's uploader password\n"
    assert not restored.is_symlink()
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600
    assert restored.stat().st_uid == os.getuid()
    assert "kept the host's off-site uploader password file from " in result.stdout
    assert "credentials.pre-restore-" in result.stdout
    services = f"{host['root']}/config/services"
    grants = index(calls, "database-provision database-grants")
    assert calls[grants].endswith(
        f"database-grants --config {services}/database-provision.yaml "
        f"--confirm-deployment {UUID} --after-restore {SET}"
    )
    assert index(calls, " retarget-image ") < grants < index(calls, " restore-begin ")
    sources = index(calls, " backup-uploader database-sources ")
    assert calls[sources].endswith(
        f"database-sources --config {services}/backup-uploader.yaml"
    )
    assert index(calls, " admin-recovery database-sources ") < sources
    assert "from web, admin-recovery and backup-uploader" in result.stdout


def test_a_set_with_its_own_uploader_password_keeps_it(host):
    """The set's file is the cluster's; nothing is copied over it."""
    uploader_set(host, password=b"the set's uploader password\n")
    host_password(host)
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 0, output(result)
    assert (host["root"] / UPLOADER_FILE).read_bytes() == (
        b"the set's uploader password\n"
    )
    assert "kept the host's off-site uploader password file" not in result.stdout


def test_a_resumed_run_takes_the_newest_moved_aside_file(host):
    """--from 4 after a failed run: this run's moved-aside tree has none.

    The failed run's tree (the newest other one) holds the host's own file;
    an older restore's tree holds another, which is not taken.
    """
    uploader_set(host)
    older = host["root"] / "credentials.pre-restore-20260901T000000Z"
    newer = host["root"] / "credentials.pre-restore-20261001T000000Z"
    host_password(host, b"an older restore's password\n", tree=older)
    host_password(host, b"the host's own password\n", tree=newer)
    result, _ = restore(host, "--from", "4", FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 0, output(result)
    assert (host["root"] / UPLOADER_FILE).read_bytes() == b"the host's own password\n"
    assert f"from {newer}/database/backup-uploader" in result.stdout


def test_a_linked_uploader_file_is_never_followed(host, tmp_path):
    """A symlink in the moved-aside tree is not a regular file: nothing copied."""
    uploader_set(host)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("not a password\n")
    link = host["root"] / "credentials" / "database" / "backup-uploader"
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 0, output(result)
    assert not (host["root"] / UPLOADER_FILE).exists()
    assert "no moved-aside tree has one" in result.stdout


@pytest.mark.parametrize("kind", ["link", "file"])
def test_a_planted_temporary_name_is_refused_not_written_through(host, tmp_path, kind):
    """A set's backup-uploader.copying link (or file) is never written into.

    Root's cp would otherwise write the host's password through the link,
    and chown and chmod would re-own and re-mode the link's target.

    Each kind runs on a fresh host: a second run on the same host within
    the same UTC second reuses the first run's pre-restore stamp, and
    step 4 then refuses the existing moved-aside config tree before it
    reaches the temporary name (#2344).
    """
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched\n")
    victim.chmod(0o644)
    planted = victim if kind == "link" else b"a planted file\n"
    uploader_set(host, extra={f"{UPLOADER_FILE}.copying": planted})
    host_password(host)
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 1, output(result)
    assert "backup-uploader.copying already exists" in output(result)
    assert victim.read_bytes() == b"untouched\n"
    assert victim.stat().st_mode & 0o777 == 0o644
    assert not (host["root"] / UPLOADER_FILE).exists()


def test_a_linked_restored_database_directory_is_refused(host, tmp_path):
    """The set's credentials/database is a link: nothing is copied through it."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    members = {"credentials/database": elsewhere, "credentials/database/web": False}
    uploader_set(host, extra=members)
    host_password(host)
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 1, output(result)
    assert "(or it is a symbolic link)" in output(result)
    assert list(elsewhere.iterdir()) == []


def test_a_linked_moved_aside_database_directory_is_refused(host, tmp_path):
    """The moved-aside tree's database/ is a link: its target is never read.

    An older restored tree could hold one; any root-readable file there
    would otherwise become the uploader's password.
    """
    uploader_set(host)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "backup-uploader").write_bytes(b"any root-readable file\n")
    (host["root"] / "credentials" / "database").symlink_to(elsewhere)
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 1, output(result)
    assert "or its database directory is a symbolic link" in output(result)
    assert not (host["root"] / UPLOADER_FILE).exists()


@pytest.mark.parametrize("where", ["rendering", "yaml"])
def test_a_moved_uploader_password_file_refuses_before_anything_stops(host, where):
    """postgres.password_files moving the file: step 4 could not keep it.

    Step 7's database-grants would then refuse with the services down, so
    the move (rendered, or in the deployment YAML) refuses up front.
    """
    uploader_set(host)
    host_password(host)
    moved = str(host["tmp"] / "elsewhere" / "backup-uploader")
    if where == "rendering":
        web = host["root"] / "config/services/web.yaml"
        document = json.loads(web.read_text())
        postgres = document["deployment"].setdefault("postgres", {})
        postgres.setdefault("password_files", {})["backup-uploader"] = moved
        web.write_text(json.dumps(document))
    else:
        host["yaml"].write_text(
            f"deployment:\n  postgres:\n    password_files:\n"
            f"      backup-uploader: {moved}\n"
        )
    result, calls = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 1, output(result)
    assert "postgres.password_files moves the off-site uploader's" in output(result)
    assert not stopped(calls)


def test_the_default_uploader_path_in_the_rendering_is_not_a_move(host):
    """A rendering that names the default path explicitly restores as usual."""
    uploader_set(host)
    host_password(host)
    web = host["root"] / "config/services/web.yaml"
    document = json.loads(web.read_text())
    postgres = document["deployment"].setdefault("postgres", {})
    postgres.setdefault("password_files", {})["backup-uploader"] = str(
        host["root"] / UPLOADER_FILE
    )
    web.write_text(json.dumps(document))
    result, _ = restore(host, FAKE_HELP=HELP_UPLOADER)
    assert result.returncode == 0, output(result)


def test_an_image_without_the_uploader_runs_no_grants_or_uploader_check(host):
    """Every image lists smoke; only uploader-password-check is the marker."""
    uploader_set(host)
    host_password(host)
    help_text = HELP.replace("restore-begin}", "restore-begin,database-sources,smoke}")
    result, calls = restore(host, FAKE_HELP=help_text)
    assert result.returncode == 0, output(result)
    assert not any("database-grants" in c for c in calls)
    assert not any(" backup-uploader " in c for c in calls)
    assert "from web and admin-recovery" in result.stdout


def test_an_uploader_image_without_database_grants_refuses_first(host):
    """The needed list refuses before anything stops."""
    help_text = HELP_UPLOADER.replace("database-grants,", "")
    result, calls = restore(host, FAKE_HELP=help_text)
    assert result.returncode == 1, output(result)
    assert "has no database-grants" in output(result)
    assert not stopped(calls)


def test_a_refused_restore_grant_stops_in_step_seven(host):
    """A mismatched file (or any refusal) stops there; --from 7 resumes."""
    uploader_set(host)
    host_password(host)
    result, calls = restore(host, FAKE_HELP=HELP_UPLOADER, FAKE_FAIL="database-grants")
    assert result.returncode == 1, output(result)
    assert "Stopped in step 7" in output(result)
    assert "run again with --from 7" in output(result)
    assert not any(" restore-begin " in c for c in calls)


@pytest.mark.parametrize("drill", [False, True])
def test_a_new_host_creates_the_login_through_the_restore_grants(new_host, drill):
    """Replacement and drill: step 7's grants, then the uploader's source check."""
    new_host["env"]["FAKE_HELP"] = HELP_NEW_HOST.replace(
        "database-roles}",
        "database-roles,database-sources,database-grants,uploader-password-check}",
    )
    result, calls = new(new_host, *(["--drill"] if drill else []))
    assert result.returncode == 0, output(result)
    grants = index(calls, "database-provision database-grants")
    assert calls[grants].endswith(f"--after-restore {SET}")
    assert index(calls, " retarget-image ") < grants < index(calls, " restore-begin ")
    assert index(calls, "database-roles") < grants
    assert any(" backup-uploader database-sources " in c for c in calls)
