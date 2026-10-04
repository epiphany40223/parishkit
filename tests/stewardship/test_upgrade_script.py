"""The scripted release-digest upgrade and rollback (#460).

`tools/stewardship-upgrade.sh` has no harness that could run it against a
host, so these tests run its two halves with stand-ins: the local half with
a recording `ssh`, to show that a bad digest or option never reaches a host,
and the host half (`tools/stewardship-upgrade-host.sh`, which the local half
uploads) with a `docker` that answers as a healthy, set-up deployment would,
to pin the order of the runbook's steps, the abandon paths and the
rollback's refusals. The host half's `local` mode, which the local laptop
environment's `deploy` runs (#476, OPS-10.10), is pinned here too, together
with the proof that a Production invocation is unchanged by it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stewardship-upgrade.sh"
HOST = ROOT / "tools" / "stewardship-upgrade-host.sh"
REPO = "ghcr.io/example/stewardship"
OFFICIAL_REPO = "ghcr.io/epiphany40223/parishkit/stewardship"
UUID = "00000000-0000-4000-8000-000000000000"
CURRENT = f"{REPO}@sha256:" + "a" * 64
TARGET = f"{REPO}@sha256:" + "b" * 64
# The local laptop environment's images: LOCAL tags built inside its VM.
LOCAL_CURRENT = "parishkit-stewardship-local:" + "c" * 40 + "-1700000000"
LOCAL_TARGET = "parishkit-stewardship-local:" + "d" * 40 + "-dirty-1700000600"
DERIVED_PREFIX = "parishkit-stewardship-local-faketime-stewardship:"
DERIVED_TARGET = DERIVED_PREFIX + "d" * 40 + "-dirty-1700000600"


def host_script():
    """The host half, as the local half uploads it: the file itself."""
    return HOST.read_text()


def executable_stub(bin_dir, name, body):
    """Write a bash stand-in into a directory that can run it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    probe = bin_dir / "probe"
    if not probe.exists():
        probe.write_text("#!/usr/bin/env bash\nexit 0\n")
        probe.chmod(0o755)
        # The Compose test container mounts /tmp noexec, so the stand-ins
        # could not run there; the other CI jobs still run these tests.
        try:
            subprocess.run([str(probe)])
        except PermissionError:
            pytest.skip("the temporary directory cannot run the stand-in programs")
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def test_both_halves_parse():
    """`bash -n` accepts the local script and the uploaded host script."""
    for script in (SCRIPT, HOST):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script
        assert os.access(script, os.X_OK), script
        assert script.read_text().startswith("#!/usr/bin/env bash\n"), script
    # The local half uploads the file; it carries no heredoc of its own.
    assert "<<'REMOTE'" not in SCRIPT.read_text()


def test_shellcheck_is_clean():
    """Both halves pass shellcheck at its default severity, as the local scripts do."""
    if shutil.which("shellcheck") is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("the CI runner lacks shellcheck")
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "bash", str(SCRIPT), str(HOST)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# The local half: validation before ssh.


def run_local(tmp_path, *args, env=None, unset=()):
    """Run the local half with a recording ssh; return (result, calls, stdin)."""
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "ssh.calls"
    stdin = tmp_path / "ssh.stdin"
    executable_stub(
        bin_dir, "ssh", f'echo "ssh $*" >>"{calls}"; cat >>"{stdin}"; exit 0'
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STEWARDSHIP_")}
    full = {
        **base,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STEWARDSHIP_HOST": "host.invalid",
        "STEWARDSHIP_UUID": UUID,
        "STEWARDSHIP_IMAGE": f"{OFFICIAL_REPO}@sha256:" + "d" * 64,
        **(env or {}),
    }
    for name in unset:
        full.pop(name, None)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=full,
        text=True,
        capture_output=True,
        timeout=30,
    )
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded, (stdin.read_text() if stdin.exists() else "")


@pytest.mark.parametrize("args", [(), ("--rollback",)])
def test_a_bad_digest_is_refused_before_ssh(tmp_path, args):
    """Anything but repo@sha256:<64 lowercase hex> never reaches the host."""
    for n, bad in enumerate(
        (
            "ghcr.io/other/stewardship@sha256:" + "d" * 64,
            f"{OFFICIAL_REPO}:v1.1.0",
            f"{OFFICIAL_REPO}@sha256:" + "D" * 64,
            f"{OFFICIAL_REPO}@sha256:" + "d" * 63,
            f"{OFFICIAL_REPO}@sha256:" + "d" * 65,
            "sha256:" + "d" * 64,
        )
    ):
        result, calls, _ = run_local(
            tmp_path / str(n), *args, env={"STEWARDSHIP_IMAGE": bad}
        )
        assert result.returncode == 1, bad
        assert "STEWARDSHIP_IMAGE must be" in result.stderr, bad
        assert calls == [], bad


@pytest.mark.parametrize(
    "missing", ["STEWARDSHIP_HOST", "STEWARDSHIP_UUID", "STEWARDSHIP_IMAGE"]
)
def test_a_missing_variable_is_refused_before_ssh(tmp_path, missing):
    """Each required variable is named when it is unset or empty."""
    for n, env in enumerate(({}, {missing: ""})):
        result, calls, _ = run_local(
            tmp_path / str(n), env=env, unset=(missing,) if not env else ()
        )
        assert result.returncode == 1
        assert missing in result.stderr
        assert calls == []


def test_an_unknown_option_is_refused_before_ssh(tmp_path):
    """Only --rollback is an option; anything else prints the usage line."""
    for n, args in enumerate((("--upgrade",), ("--rollback", "x"), ("extra",))):
        result, calls, _ = run_local(tmp_path / str(n), *args)
        assert result.returncode == 2, args
        assert "usage:" in result.stderr and "[--rollback]" in result.stderr
        assert calls == []


def test_a_rollback_refuses_the_schema_change_override(tmp_path):
    """A rollback never migrates, so the override is refused, not ignored."""
    result, calls, _ = run_local(
        tmp_path, "--rollback", env={"STEWARDSHIP_SCHEMA_CHANGE": "1"}
    )
    assert result.returncode == 1
    assert "never migrates" in result.stderr
    assert "database-restore" in result.stderr
    assert calls == []
    # Only the explicit default is accepted.
    result, calls, _ = run_local(
        tmp_path / "zero", "--rollback", env={"STEWARDSHIP_SCHEMA_CHANGE": "0"}
    )
    assert result.returncode == 0 and len(calls) == 1


def test_a_valid_digest_uploads_the_host_script_once(tmp_path):
    """One ssh call, the arguments in order, the host half on its stdin."""
    image = f"{OFFICIAL_REPO}@sha256:" + "d" * 64
    result, calls, stdin = run_local(tmp_path)
    assert result.returncode == 0, result.stderr
    # The complete Production invocation, pinned: eight %q-quoted arguments
    # (repo image root project yaml uuid schema_change mode) and no profile,
    # so the host half runs in production mode exactly as before the local
    # mode existed (#476: Production output byte-identical).
    assert calls == [
        'ssh host.invalid f=$(mktemp) && cat > "$f" && bash "$f" '
        f"{OFFICIAL_REPO} {image} /opt/parishkit stewardship "
        f"/etc/parishkit/stewardship-deployment.yaml {UUID} 0 upgrade ; "
        'rc=$?; rm -f "$f"; exit $rc'
    ]
    assert stdin == host_script()
    # The uploaded half is itself valid bash.
    assert subprocess.run(["bash", "-n"], input=stdin, text=True).returncode == 0
    result, calls, _ = run_local(tmp_path / "rollback", "--rollback")
    assert result.returncode == 0, result.stderr
    assert f" {UUID} 0 rollback ; rc=" in calls[0]


def test_the_optional_variables_reach_the_host(tmp_path):
    """Root, project, YAML path and repository override the defaults."""
    repo = "ghcr.io/example/other"
    image = f"{repo}@sha256:" + "e" * 64
    result, calls, _ = run_local(
        tmp_path,
        env={
            "STEWARDSHIP_IMAGE": image,
            "STEWARDSHIP_IMAGE_REPO": repo,
            "STEWARDSHIP_ROOT": "/srv/pk",
            "STEWARDSHIP_PROJECT": "proj",
            "STEWARDSHIP_YAML": "/etc/pk/d.yaml",
            "STEWARDSHIP_SCHEMA_CHANGE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    expected = f"{repo} {image} /srv/pk proj /etc/pk/d.yaml {UUID} 1 upgrade ; rc="
    assert expected in calls[0]


# ---------------------------------------------------------------------------
# The host half: a stand-in docker that behaves like a set-up deployment.

# The stand-in records every call and keeps the set of stopped services in
# FAKE_STATE so `ps --services --status running` answers truthfully after a
# `stop` or an `up`. Switches: FAKE_NOOP is what the upgrade check answers
# (FAKE_NOOP_LATER, when set, is what every run after the first answers);
# FAKE_RENDER_FAIL makes rendering it fail; FAKE_BACKUP_FAIL makes the
# backup report no off-host copy; FAKE_STOP_FAIL makes `stop web` fail;
# FAKE_WEB_FAIL makes web never turn healthy; FAKE_DEBUG_ON gives one
# container debug logging; FAKE_DIGESTS is what a RepoDigests inspect
# answers; FAKE_RETARGET_FAIL makes `retarget-image` fail with nothing
# written; FAKE_RESTART_WORKER brings the worker back the moment web stops
# (the runbook's "restarted meanwhile" case); FAKE_SERVICES_AFTER is the
# service list the re-rendered Compose file has after a retarget.
# `retarget-image` rewrites compose.json and the three role documents as
# the real command would, so the final checks read what the run itself
# wrote. For the local mode: FAKE_OFFSITE is the off-site state the backup
# reports (default uploaded); FAKE_BACKUP_UNRECORDED makes it report no
# backup at all; FAKE_BACKUP_GARBAGE makes its last line a refusal that is
# not JSON; FAKE_DEBUG_VALUE is what every container's debug variable
# says (default 0); FAKE_COMPOSE_FILES is the project's ConfigFiles answer
# (default the one Compose file); FAKE_OVERRIDE names the faketime override
# `retarget-image` re-renders with derived image names; `image inspect`
# finds every image but the derived fake-clock application image, so a
# fake-clock run has exactly one derived image to build. Limits: the
# upgrade check's SQL is never executed (the stand-in answers FAKE_NOOP),
# `install -o` ownership is not exercised, and no container runs, so the
# images' own refusals are out of scope.
FAKE_DOCKER = r"""
echo "$*" >>"$FAKE_LOG"
stopped="$FAKE_STATE/stopped"
touch "$stopped"
all=(postgres valkey web worker scheduler mail-dispatch caddy)
if [ -e "$FAKE_STATE/retargeted" ] && [ -n "$FAKE_SERVICES_AFTER" ]; then
    read -r -a all <<<"$FAKE_SERVICES_AFTER"
fi
last=${*: -1}
json() {
    recorded=true
    [ -z "$FAKE_BACKUP_UNRECORDED" ] || recorded=false
    echo '{"backup_recorded": '"$recorded"', "offsite": {"state": "'"$1"'"}}'
}
case "$*" in
    "image inspect "*faketime-stewardship*) exit 1 ;;
    "image inspect "*) ;;
    *" stop "*)
        if [ -n "$FAKE_STOP_FAIL" ] && [[ "$*" == *" stop web"* ]]; then
            echo "stop refused by the stand-in" >&2
            exit 1
        fi
        for s in "$@"; do [[ "$s" = -* ]] || echo "$s" >>"$stopped"; done
        if [ -n "$FAKE_RESTART_WORKER" ] && [ "$last" = web ]; then
            grep -vxF worker "$stopped" >"$stopped.new" || true
            mv "$stopped.new" "$stopped"
        fi ;;
    *" up --detach "*)
        if [ -n "$FAKE_WEB_FAIL" ] && [[ " $* " == *" web "* ]]; then exit 1; fi
        for s in "$@"; do
            grep -vxF "$s" "$stopped" >"$stopped.new" || true
            mv "$stopped.new" "$stopped"
        done ;;
    *" ps --services --status running")
        printf '%s\n' "${all[@]}" | grep -vxF -f "$stopped" || true ;;
    *" config --services") printf '%s\n' "${all[@]}" ;;
    *" ps -q "*) ;;
    *" ps --quiet") printf '%s\n' id-web id-worker ;;
    "compose ls"*)
        files=${FAKE_COMPOSE_FILES:-$FAKE_COMPOSE}
        echo '[{"Name":"'"$FAKE_PROJECT"'","ConfigFiles":"'"$files"'"}]' ;;
    *"RepoDigests"*) echo "${FAKE_DIGESTS:-$last}" ;;
    *"{{.Name}}"*) echo "/stewardship-$last" ;;
    *"Config.Env"*)
        on=${FAKE_DEBUG_VALUE:-0}
        [ -z "$FAKE_DEBUG_ON" ] || [ "$last" != id-worker ] || on=1
        echo "PARISHKIT_DEBUG_LOGGING=$on" ;;
    *" collect-static --destination "*)
        echo "collected" >"$last/new.js"
        echo "collected 1 file" ;;
    *" upgrade-check "*)
        [ -z "$FAKE_RENDER_FAIL" ] || exit 2
        echo "SELECT true;" ;;
    *"PGOPTIONS"*)
        cat >/dev/null
        echo x >>"$FAKE_STATE/checks"
        if [ -n "$FAKE_NOOP_LATER" ] && [ "$(wc -l <"$FAKE_STATE/checks")" -gt 1 ]
        then echo "$FAKE_NOOP_LATER"; else echo "$FAKE_NOOP"; fi ;;
    *" run --rm -T backup-worker")
        echo '{"level":"DEBUG"}'
        if [ -n "$FAKE_BACKUP_GARBAGE" ]; then echo "ERROR: backup refused; no JSON"
        elif [ -n "$FAKE_BACKUP_FAIL" ]; then json failed
        else json "${FAKE_OFFSITE:-uploaded}"; fi ;;
    *" retarget-image --config "*)
        [ -z "$FAKE_RETARGET_FAIL" ] || { echo "retarget refused" >&2; exit 1; }
        bulk=false batch=20 latency=""
        for arg in "$@"; do
            case "$arg" in
                PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1) bulk=true ;;
                PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=*) batch=${arg#*=} ;;
                PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS=*)
                    latency=', "local_smtp_latency_ms": '"${arg#*=}" ;;
            esac
        done
        echo '{"services": {"web": {"image": "'"$last"'"}}}' >"$FAKE_COMPOSE"
        if [ -n "$FAKE_OVERRIDE" ]; then
            derived="parishkit-stewardship-local-faketime-stewardship:${last##*:}"
            echo '{"services": {"web": {"image": "'"$derived"'"},' \
                '"postgres": {"image": "faketime-postgres:x"},' \
                '"valkey": {"image": "faketime-valkey:x"}}}' >"$FAKE_OVERRIDE"
        fi
        for role in worker mail-dispatch scheduler; do
            doc='{"deployment": {"bulk_family_send": '"$bulk"','
            echo "$doc"' "bulk_send_batch": '"$batch$latency"'}}' \
                >"$(dirname "$FAKE_COMPOSE")/$role.yaml"
        done
        touch "$FAKE_STATE/retargeted" ;;
    *" run --rm -T migration") echo "migrated" ;;
    *"database-grants"*) echo "granted" ;;
    *"exec -T caddy sha256sum"*) sha256sum "$FAKE_CADDYFILE" ;;
    *"pk-stewardship health"*) [ -z "$FAKE_WEB_FAIL" ] || exit 1 ;;
    *"ps --all --format json")
        for s in $(printf '%s\n' "${all[@]}" | grep -vxE 'postgres|valkey'); do
            health=healthy
            [ -z "$FAKE_WEB_FAIL" ] || [ "$s" != web ] || health=unhealthy
            echo '{"Service":"'$s'","State":"running","Health":"'$health'"}'
        done ;;
    *"import parishkit"*) echo 1.1.0 ;;
    *" top "*) printf '%s\n' "runtime --queue a" "runtime --queue b" ;;
esac
exit 0
"""


def run_host(
    tmp_path,
    mode,
    *,
    noop="t",
    status=0,
    kept=None,
    bulk=True,
    prepare=None,
    profile=None,
    clock="normal",
    image=None,
    env=None,
    **fake,
):
    """Run the host half against the stand-ins.

    Returns the recorded docker calls, the combined output and the runtime
    root. `noop` is the upgrade check's answer; `status` the exit status
    the script must end with; `kept` is the content of a kept static tree
    for the target digest, when there is one; `bulk` sets the deployment's
    bulk Family send switch; `prepare(root)` may alter the runtime root
    before the run; `fake` sets the stand-in's FAKE_* switches. `profile`
    None runs the eight-argument Production invocation; "local" appends
    `local BUILD` and lays the root out as the local environment's `up`
    leaves it (marker, LOCAL deployment YAML, clock-mode marker saying
    `clock`, the override file in fake mode) with `image` (default a LOCAL
    tag) as the target; any other value is passed through as the profile.
    `env` adds to the script's environment.
    """
    if shutil.which("jq") is None or shutil.which("sha256sum") is None:
        # The runner has both; only the in-image run (which does not see
        # this variable) may skip, as the dev deploy tests do.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(
                "the CI runner lacks jq or sha256sum; host-half tests cannot run"
            )
        pytest.skip("the host-side script needs jq and sha256sum")
    root = tmp_path / "root"
    services = root / "config" / "services"
    services.mkdir(parents=True)
    (services / "Caddyfile").write_text("caddy\n")
    local = profile == "local"
    current = LOCAL_CURRENT if local else CURRENT
    target = image or (LOCAL_TARGET if local else TARGET)
    (services / "compose.json").write_text(
        json.dumps({"services": {"web": {"image": current}}})
    )
    origin = "https://localhost:8443" if local else "https://stewardship.example.test"
    web = {"deployment": {"postgres": {"name": "stewardship"}, "public_origin": origin}}
    (services / "web.yaml").write_text(json.dumps(web))
    yaml = tmp_path / "deployment.yaml"
    build = tmp_path / "build"
    (build / "deploy" / "stewardship").mkdir(parents=True)
    (build / "deploy" / "stewardship" / "Dockerfile.faketime").write_text("ARG BASE\n")
    if local:
        (root / ".parishkit-local").write_text("parishkit-local deployment x\n")
        yaml.write_text(
            "deployment:\n  profile: local\n  public_origin: https://localhost:8443\n"
        )
        (root / "run" / "local" / "clock").mkdir(parents=True)
        (root / "run" / "local" / "clock" / "mode").write_text(clock + "\n")
        if clock == "fake":
            (services / "compose.faketime.json").write_text(
                json.dumps(
                    {
                        "services": {
                            "web": {"image": DERIVED_PREFIX + current.split(":")[1]},
                            "postgres": {"image": "faketime-postgres:x"},
                            "valkey": {"image": "faketime-valkey:x"},
                        }
                    }
                )
            )
    for role in ("worker", "mail-dispatch", "scheduler"):
        (services / f"{role}.yaml").write_text(
            json.dumps(
                {"deployment": {"bulk_family_send": bulk, "bulk_send_batch": 25}}
            )
        )
    (root / "cache" / "static").mkdir(parents=True)
    (root / "cache" / "static" / "old.js").write_text("old")
    if kept is not None:
        hex_ = target.rsplit(":", 1)[1]
        (root / "cache" / f"static.{hex_}").mkdir()
        (root / "cache" / f"static.{hex_}" / "kept.js").write_text(kept)
    if prepare is not None:
        prepare(root)
    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "docker", FAKE_DOCKER)
    # Root-only `install -o`, real waiting and the public-origin probe are
    # replaced; FAKE_ORIGIN_FAIL makes the origin unreachable.
    executable_stub(bin_dir, "install", 'mkdir -p "${@: -1}"')
    executable_stub(bin_dir, "sleep", "exit 0")
    # The stand-in answers as the application does through caddy (the local
    # mode reads the Server header; Production only the exit status).
    executable_stub(
        bin_dir,
        "curl",
        'echo "curl $*" >>"$FAKE_LOG"; [ -z "$FAKE_ORIGIN_FAIL" ] || exit 7; '
        'printf "HTTP/2 200\\r\\nserver: gunicorn\\r\\n"',
    )
    # coreutils timeout is Linux-only here; the stand-in just runs the command.
    executable_stub(bin_dir, "timeout", 'shift; exec "$@"')
    state = tmp_path / "state"
    state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    log = tmp_path / "docker.log"
    log.touch()
    project = "parishkit-local" if local else "stewardship"
    trailing = [] if profile is None else [profile, str(build)]
    override = str(services / "compose.faketime.json") if clock == "fake" else ""
    result = subprocess.run(
        [
            "bash",
            str(HOST),
            REPO,
            target,
            str(root),
            project,
            str(yaml),
            UUID,
            fake.pop("schema_change", "0"),
            mode,
            *trailing,
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_STATE": str(state),
            "FAKE_NOOP": noop,
            "FAKE_PROJECT": project,
            "FAKE_COMPOSE": str(services / "compose.json"),
            "FAKE_OVERRIDE": override,
            "FAKE_CADDYFILE": str(services / "Caddyfile"),
            "STEWARDSHIP_LOG_DIR": str(logs),
            **{"FAKE_" + name.upper(): str(value) for name, value in fake.items()},
            **(env or {}),
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == status, output
    # The whole log is also kept on the host, named after the mode (a usage
    # error ends before the log opens).
    kept_logs = list(logs.glob(f"stewardship-{mode}-*.log"))
    if status != 2:
        assert len(kept_logs) == 1 and "Log: " in kept_logs[0].read_text()
    return log.read_text().splitlines(), output, root


def first(calls, fragment):
    """Index of the first recorded call containing the fragment."""
    return next(i for i, call in enumerate(calls) if fragment in call)


def test_an_upgrade_follows_the_runbook_order(tmp_path):
    """Image-only work first, backup before web stops, web starts first."""
    calls, output, root = run_host(tmp_path, "upgrade")
    stop_background = first(calls, " stop worker scheduler mail-dispatch")
    stop_web = first(calls, " stop web")
    early = (
        f"pull --quiet {TARGET}",
        " collect-static ",
        " upgrade-check ",
        "PGOPTIONS",
    )
    for fragment in early:
        assert first(calls, fragment) < stop_background, fragment
    backup = first(calls, "backup-worker")
    retarget = first(calls, " retarget-image ")
    assert stop_background < backup < stop_web < retarget
    # caddy keeps serving its maintenance page; it is never stopped.
    assert not any(" stop " in call and "caddy" in call for call in calls)
    # Step 4's decisive check runs after the retarget, and t skips both commands.
    assert retarget < [i for i, c in enumerate(calls) if "PGOPTIONS" in c][1]
    assert not any(" migration" in call for call in calls)
    assert not any("database-grants" in call for call in calls)
    start_web = first(calls, "up --detach --wait web")
    start_rest = first(calls, "up --detach --wait worker scheduler mail-dispatch")
    assert retarget < start_web < first(calls, "up --detach --wait caddy") < start_rest
    assert "skipped: the upgrade check answered t" in output
    assert "web was down for" in output
    # The bulk switch is carried into the retarget, and the new documents
    # show it on; debug logging is off in the environment the retarget sees.
    assert "PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1" in calls[retarget]
    assert "PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=25" in calls[retarget]
    assert "bulk Family send is on (batch 25); keeping it on" in output
    # Static: refreshed in place from static.next, previous tree kept by digest.
    cache = root / "cache"
    assert not (cache / "static.next").exists()
    assert (cache / "static" / "new.js").exists()
    assert not (cache / "static" / "old.js").exists()
    assert (cache / f"static.{'a' * 64}" / "old.js").exists()
    origin = "https://stewardship.example.test"
    assert f"curl -fsS -o /dev/null --max-time 20 {origin}/" in calls
    assert f"Upgraded to {TARGET} in" in output
    assert f"Replaced release: {CURRENT}" in output


def test_an_upgrade_with_a_schema_change_refuses_without_the_override(tmp_path):
    """Anything but t before the stops needs STEWARDSHIP_SCHEMA_CHANGE=1."""
    calls, output, _ = run_host(tmp_path, "upgrade", noop="f", status=1)
    assert "set STEWARDSHIP_SCHEMA_CHANGE=1" in output
    assert "deploy --schema-change" not in output
    assert "Failed before anything stopped; nothing changed" in output
    assert not any(" stop " in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)
    # Local mode names the operator command instead.
    calls, output, _ = run_host(
        tmp_path / "local", "upgrade", noop="f", status=1, profile="local"
    )
    assert "re-run as 'deploy --schema-change'" in output
    assert "STEWARDSHIP_SCHEMA_CHANGE" not in output
    assert not any(" stop " in call for call in calls)


def test_an_upgrade_with_the_override_runs_migration_and_grants(tmp_path):
    """With the override, both commands run after the retarget, before web."""
    calls, output, _ = run_host(tmp_path, "upgrade", noop="f", schema_change="1")
    retarget = first(calls, " retarget-image ")
    migration = first(calls, "run --rm -T migration")
    assert retarget < migration < first(calls, "database-grants")
    assert migration < first(calls, "up --detach --wait web")
    assert "running migration and grants: the check answered: f" in output


def test_a_failed_backup_abandons_before_web_stops(tmp_path):
    """No off-host copy: the background services come back, web never stops."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, backup_fail=True)
    assert "did not complete with its off-host copy" in output
    assert "restarting the background services" in output
    assert not any(" stop web" in call for call in calls)
    restart = first(calls, "up --detach --wait worker scheduler mail-dispatch")
    assert first(calls, "backup-worker") < restart
    assert not any(" retarget-image " in call for call in calls)


@pytest.mark.parametrize("profile", [None, "local"])
def test_a_backup_that_prints_no_json_abandons_before_web_stops(tmp_path, profile):
    """A refusal line instead of the JSON document is read as no backup."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, profile=profile, backup_garbage=True
    )
    assert "ERROR: backup refused; no JSON" in output
    assert "did not complete with its off-host copy; abandoning the upgrade" in output
    assert "restarting the background services" in output
    assert not any(" stop web" in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)


def test_a_failed_stop_of_web_starts_everything_again(tmp_path):
    """With nothing retargeted, a failure after the stop restarts the services."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, stop_fail=True)
    assert "stop refused by the stand-in" in output
    assert "nothing retargeted; starting everything again" in output
    assert not any(" retarget-image " in call for call in calls)
    # One `up` for every online service of the current file, and nothing after.
    restart = first(
        calls, "up --detach --wait web worker scheduler mail-dispatch caddy"
    )
    assert first(calls, " stop web") < restart == len(calls) - 1


def test_an_unreadable_render_refuses_before_anything_stops(tmp_path):
    """Without a rendered check there is no proof, so nothing is touched."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, render_fail=True)
    assert "Could not render the upgrade check" in output
    assert not any(" stop " in call for call in calls)


def test_a_rollback_restores_the_kept_tree_without_migrating(tmp_path):
    """The rollback checks before retargeting, never migrates, reuses the tree."""
    calls, output, root = run_host(tmp_path, "rollback", kept="kept")
    assert not any(" collect-static " in call for call in calls)
    kept = root / "cache" / f"static.{'b' * 64}"
    assert f"Using the kept static tree {kept}" in output
    checks = [i for i, c in enumerate(calls) if "PGOPTIONS" in c]
    stop_web = first(calls, " stop web")
    retarget = first(calls, " retarget-image ")
    # Advisory before anything stops; decisive after web stops, before retarget.
    assert len(checks) == 2 and checks[0] < stop_web < checks[1] < retarget
    assert first(calls, "backup-worker") < stop_web
    assert "a rollback never migrates" in output
    assert "Migration: not repeated by a rollback" in output
    assert not any(" migration" in call or "database-grants" in call for call in calls)
    # Retargeted in the previous image, to the previous digest, bulk kept on.
    retarget_call = (
        f" {TARGET} retarget-image --config /run/operator.yaml --image {TARGET}"
    )
    assert retarget_call in calls[retarget]
    assert "PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1" in calls[retarget]
    cache = root / "cache"
    assert (cache / "static" / "kept.js").read_text() == "kept"
    assert not (cache / "static" / "old.js").exists()
    assert (cache / f"static.{'a' * 64}" / "old.js").exists()
    assert (cache / f"static.{'b' * 64}" / "kept.js").exists()
    start_web = first(calls, "up --detach --wait web")
    assert retarget < start_web < first(calls, "up --detach --wait worker")
    assert f"Rolled back to {TARGET} in" in output
    assert f"Replaced release: {CURRENT}" in output


def test_a_rollback_without_a_kept_tree_collects_in_the_previous_image(tmp_path):
    """No cache/static.<digest>: the previous image collects into static.next."""
    calls, output, root = run_host(tmp_path, "rollback")
    collect = first(calls, " collect-static ")
    assert f" {TARGET} collect-static --destination " in calls[collect]
    assert collect < first(calls, " stop ")
    assert (root / "cache" / "static" / "new.js").exists()
    assert not (root / "cache" / "static.next").exists()


def test_a_rollback_refuses_a_changed_schema_before_anything_stops(tmp_path):
    """The previous image's check not answering t means a database restore."""
    calls, output, _ = run_host(tmp_path, "rollback", noop="f", status=1)
    assert "image-only" in output and "never restores a database" in output
    assert "stewardship-deployment-runbook.md#rollback" in output
    assert "Failed before anything stopped; nothing changed" in output
    assert not any(" stop " in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)


def test_a_rollback_whose_decisive_check_fails_starts_everything_again(tmp_path):
    """A refusal after web stopped, before the retarget, restarts the services."""
    calls, output, _ = run_host(
        tmp_path, "rollback", noop="t", noop_later="f", status=1
    )
    assert "never restores a database" in output
    assert "nothing retargeted; starting everything again" in output
    assert not any(" retarget-image " in call for call in calls)
    stop_web = first(calls, " stop web")
    restart = first(
        calls, "up --detach --wait web worker scheduler mail-dispatch caddy"
    )
    assert stop_web < restart == len(calls) - 1


def test_a_rollback_carries_a_switched_off_bulk_send_too(tmp_path):
    """With the switch off, no variable is passed and no check expects it."""
    calls, output, _ = run_host(tmp_path, "rollback", bulk=False)
    retarget = first(calls, " retarget-image ")
    assert "BULK_FAMILY_SEND" not in calls[retarget]
    assert "keeping it on" not in output
    assert f"Rolled back to {TARGET}" in output


@pytest.mark.parametrize("mode", ["upgrade", "rollback"])
def test_a_pulled_image_without_the_reference_is_refused(tmp_path, mode):
    """A pull that does not carry exactly the named digest stops the run."""
    calls, output, _ = run_host(
        tmp_path, mode, status=1, digests=f"{REPO}@sha256:" + "c" * 64
    )
    assert "does not carry" in output
    assert not any(" stop " in call for call in calls)


@pytest.mark.parametrize("mode", ["upgrade", "rollback"])
def test_problems_after_the_start_fail_the_run_and_name_them(tmp_path, mode):
    """Unhealthy web, debug logging on and a silent origin are each named."""
    calls, output, _ = run_host(
        tmp_path, mode, status=1, web_fail=True, debug_on=True, origin_fail=True
    )
    assert "NOT healthy" in output
    assert "web health check failing" in output
    assert "web: web running unhealthy" in output
    assert "/stewardship-id-worker: PARISHKIT_DEBUG_LOGGING=1" in output
    assert "public origin https://stewardship.example.test does not answer" in output
    # The other services were still started.
    assert any("up --detach --wait worker" in call for call in calls)


@pytest.mark.parametrize("mode", ["upgrade", "rollback"])
def test_a_failed_retarget_leaves_the_maintenance_page_up(tmp_path, mode):
    """After the retarget began nothing is restarted; the message says why."""
    calls, output, _ = run_host(tmp_path, mode, status=1, retarget_fail=True)
    assert "retarget refused" in output
    assert "caddy serves the maintenance page" in output
    assert "starting everything again" not in output
    assert not any("up --detach" in call for call in calls)
    if mode == "rollback":
        assert "see the runbook's Rollback section" in output
    else:
        assert "Re-run with the same digest, or roll back" in output


def test_services_restarted_during_the_stop_come_back_on_abandon(tmp_path):
    """A worker that came back and was stopped again is started with the rest."""
    calls, output, _ = run_host(
        tmp_path, "rollback", noop_later="f", status=1, restart_worker=True
    )
    assert "Online services restarted meanwhile (worker); stopping them too." in output
    stop_web = first(calls, " stop web")
    assert any(call.endswith(" stop worker") for call in calls[stop_web:])
    assert "starting everything again" in output
    assert "up --detach --wait web worker scheduler mail-dispatch caddy" in calls[-1]


def test_an_upgrade_starts_and_checks_a_service_the_new_release_adds(tmp_path):
    """Step 6 reads the re-rendered file: a new service is started and checked."""
    after = "postgres valkey web worker scheduler mail-dispatch caddy digests"
    calls, output, _ = run_host(tmp_path, "upgrade", services_after=after)
    retarget = first(calls, " retarget-image ")
    start_rest = first(
        calls, "up --detach --wait worker scheduler mail-dispatch digests"
    )
    assert retarget < start_rest
    assert "all 6 online services healthy" in output


def test_a_rollback_does_not_name_a_service_the_old_release_lacks(tmp_path):
    """A service only the newer release has is neither started nor a problem."""
    after = "postgres valkey web worker scheduler caddy"
    calls, output, _ = run_host(tmp_path, "rollback", services_after=after)
    assert any(call.endswith("up --detach --wait worker scheduler") for call in calls)
    assert not any("up --detach" in call and "mail-dispatch" in call for call in calls)
    assert "mail-dispatch: missing" not in output
    assert "all 4 online services healthy" in output


def test_a_changed_batch_size_is_a_named_problem(tmp_path):
    """The rendered worker document must carry the batch size that was read."""
    calls, output, _ = run_host(tmp_path, "upgrade")
    assert (
        "PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=25"
        in calls[first(calls, " retarget-image ")]
    )
    assert "bulk send batch is" not in output


def test_a_partial_static_refresh_says_how_to_restore(tmp_path):
    """The ERR trap around the in-place refresh names the kept tree to copy back."""
    if os.geteuid() == 0:
        pytest.skip("root can delete from a read-only directory")
    locked = tmp_path / "root" / "cache" / "static" / "locked"

    def lock(root):
        # A file in a read-only directory makes the in-place delete fail.
        locked.mkdir()
        (locked / "file.js").write_text("x")
        locked.chmod(0o500)

    try:
        _, output, root = run_host(tmp_path, "upgrade", status=1, prepare=lock)
    finally:
        # run_host skips before prepare runs where jq is missing (the compose
        # image), so the directory may not exist.
        if locked.exists():
            locked.chmod(0o700)
    aside = root / "cache" / f"static.{'a' * 64}"
    assert "Static refresh failed partway" in output
    assert f"cp -a {aside}/. {root / 'cache' / 'static'}/" in output
    assert (aside / "locked" / "file.js").exists()
    assert "caddy serves the maintenance page" in output


# ---------------------------------------------------------------------------
# The host half's local mode (#476, OPS-10.10): the local laptop environment's
# `deploy`, and the proof that Production is untouched by it.


def local_root_id(image):
    """What names a LOCAL image's kept static tree: the tag after the colon."""
    return image.split(":", 1)[1]


def test_production_mode_is_unchanged_by_a_local_marker(tmp_path):
    """Eight arguments are Production: the marker, clock and debug rules never apply."""

    def marked(root):
        (root / ".parishkit-local").write_text("marker\n")
        (root / "run" / "local" / "clock").mkdir(parents=True)
        (root / "run" / "local" / "clock" / "mode").write_text("fake\n")

    calls, output, _ = run_host(
        tmp_path, "upgrade", prepare=marked, env={"PARISHKIT_DEBUG_LOGGING": "1"}
    )
    assert any(f"pull --quiet {TARGET}" == c for c in calls)
    assert "clock mode" not in output and "faketime" not in "".join(calls)
    # Production exports 0 whatever the caller's environment says, so the
    # containers answering 0 is not a problem.
    assert "NOT healthy" not in output and f"Upgraded to {TARGET} in" in output
    probe = "curl -fsS -o /dev/null --max-time 20 https://stewardship.example.test/"
    assert probe in calls


def test_production_mode_still_requires_the_uploaded_offsite_copy(tmp_path):
    """not_configured is accepted by local mode only; Production abandons."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, offsite="not_configured")
    assert "did not complete with its off-host copy" in output
    assert not any(" stop web" in call for call in calls)


def test_an_unknown_profile_or_a_local_mode_without_a_build_is_a_usage_error(tmp_path):
    """Only production (implicit) and `local BUILD` are accepted."""
    calls, output, _ = run_host(tmp_path / "a", "upgrade", status=2, profile="staging")
    assert "usage:" in output and calls == []
    # Local without the packed checkout directory.
    root = tmp_path / "b" / "root"
    arguments = [REPO, LOCAL_TARGET, str(root), "parishkit-local", "y", UUID, "0"]
    result = subprocess.run(
        ["bash", str(HOST), *arguments, "upgrade", "local"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "packed checkout" in result.stderr


def test_local_mode_refuses_anything_but_a_local_deployment(tmp_path):
    """No marker, no LOCAL profile, a digest or an unbuilt image: nothing runs."""

    def unmarked(root):
        (root / ".parishkit-local").unlink()

    calls, output, _ = run_host(
        tmp_path / "marker", "upgrade", status=1, profile="local", prepare=unmarked
    )
    assert "does not carry the local marker" in output and calls == []

    def other_profile(root):
        yaml = root.parent / "deployment.yaml"
        yaml.write_text("deployment:\n  profile: production\n")

    calls, output, _ = run_host(
        tmp_path / "profile",
        "upgrade",
        status=1,
        profile="local",
        prepare=other_profile,
    )
    assert "does not say 'profile: local'" in output and calls == []
    calls, output, _ = run_host(
        tmp_path / "digest", "upgrade", status=1, profile="local", image=TARGET
    )
    assert "is not a LOCAL image tag" in output and calls == []

    def no_clock(root):
        (root / "run" / "local" / "clock" / "mode").unlink()

    calls, output, _ = run_host(
        tmp_path / "clock", "upgrade", status=1, profile="local", prepare=no_clock
    )
    assert "expected fake or normal" in output
    assert calls == [f"image inspect {LOCAL_TARGET}"]
    assert "Failed before anything stopped; nothing changed" in output


def test_a_local_upgrade_follows_the_production_steps_without_a_pull(tmp_path):
    """Same order as Production; the image is local, the off-site copy unconfigured."""
    calls, output, root = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        offsite="not_configured",
        debug_value="1",
        env={"PARISHKIT_DEBUG_LOGGING": "1"},
    )
    assert not any(" pull " in call or "RepoDigests" in call for call in calls)
    assert "Using the local image" in output and "nothing to pull" in output
    inspect = first(calls, f"image inspect {LOCAL_TARGET}")
    stop_background = first(calls, " stop worker scheduler mail-dispatch")
    for fragment in (" collect-static ", " upgrade-check ", "PGOPTIONS"):
        assert inspect < first(calls, fragment) < stop_background, fragment
    backup = first(calls, "backup-worker")
    stop_web = first(calls, " stop web")
    retarget = first(calls, " retarget-image ")
    assert stop_background < backup < stop_web < retarget
    assert (
        f" {LOCAL_TARGET} retarget-image --config /run/operator.yaml" in calls[retarget]
    )
    assert calls[retarget].endswith(f" --image {LOCAL_TARGET}")
    assert retarget < first(calls, "up --detach --wait web")
    # Every Compose call names the local project and one file (normal mode).
    for call in calls:
        if call.startswith("compose -f"):
            assert "-p parishkit-local" in call and "faketime" not in call
    # The kept tree is named after the replaced tag, not a digest.
    kept = root / "cache" / f"static.{local_root_id(LOCAL_CURRENT)}"
    assert (kept / "old.js").exists()
    assert (root / "cache" / "static" / "new.js").exists()
    # The deployment's debug setting is kept and checked, not forced to 0.
    assert "debug logging 1" in output and "NOT healthy" not in output
    assert "curl -ksSI --max-time 20 https://localhost:8443/" in calls
    assert f"Upgraded to {LOCAL_TARGET} in" in output
    assert f"Replaced release: {LOCAL_CURRENT}" in output


def test_a_local_upgrade_still_requires_the_backup(tmp_path):
    """Local mode waives the off-host copy, not the backup: both failures abandon."""
    calls, output, _ = run_host(
        tmp_path / "unrecorded",
        "upgrade",
        status=1,
        profile="local",
        offsite="not_configured",
        backup_unrecorded=True,
    )
    assert "did not complete with its off-host copy; abandoning" in output
    assert "restarting the background services" in output
    assert not any(" stop web" in call for call in calls)
    # An off-site copy that was configured but failed is not "not configured".
    calls, output, _ = run_host(
        tmp_path / "failed", "upgrade", status=1, profile="local", backup_fail=True
    )
    assert "abandoning" in output and not any(" stop web" in c for c in calls)


def test_a_local_upgrade_in_fake_clock_mode_keeps_the_override(tmp_path):
    """Every Compose call carries the override; the derived image is built in step 3."""
    calls, output, root = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        clock="fake",
        offsite="not_configured",
        compose_files=str(root_of(tmp_path) / "compose.json")
        + ","
        + str(root_of(tmp_path) / "compose.faketime.json"),
    )
    override = str(root / "config" / "services" / "compose.faketime.json")
    for call in calls:
        if call.startswith("compose -f"):
            assert f" -f {override} -p parishkit-local" in call, call
    assert "fake clock mode" in output
    retarget = first(calls, " retarget-image ")
    build = first(calls, "build --quiet --file")
    assert retarget < build < first(calls, "up --detach --wait web")
    dockerfile = tmp_path / "build" / "deploy" / "stewardship" / "Dockerfile.faketime"
    assert calls[build] == (
        f"build --quiet --file {dockerfile} --build-arg BASE={LOCAL_TARGET} "
        f"--tag {DERIVED_TARGET} {dockerfile.parent}"
    )
    # The store images' derived forms exist already: built once, never again.
    assert sum("build --quiet" in c for c in calls) == 1
    assert "faketime-postgres:x (already built)" in output
    assert f"Upgraded to {LOCAL_TARGET} in" in output


def root_of(tmp_path):
    """The services directory run_host lays out under tmp_path."""
    return tmp_path / "root" / "config" / "services"


def test_a_local_rollback_uses_the_kept_tree_and_refuses_a_changed_schema(tmp_path):
    """Image-only, as in Production: kept tree, no pull, refusal on f."""
    calls, output, root = run_host(
        tmp_path / "ok",
        "rollback",
        profile="local",
        kept="kept",
        offsite="not_configured",
    )
    assert not any(" pull " in call or " collect-static " in call for call in calls)
    tree = root / "cache" / f"static.{local_root_id(LOCAL_TARGET)}"
    assert f"Using the kept static tree {tree}" in output
    assert (root / "cache" / "static" / "kept.js").read_text() == "kept"
    assert f"Rolled back to {LOCAL_TARGET} in" in output
    calls, output, _ = run_host(
        tmp_path / "f", "rollback", status=1, profile="local", noop="f"
    )
    assert "never restores a database" in output
    assert not any(" stop " in call for call in calls)


# BG-12's rehearsal: local mode renders the bulk switch and the modeled SMTP
# latency the caller chose; Production mode never reads either variable.
REHEARSAL = {
    "PARISHKIT_LOCAL_BULK_FAMILY_SEND": "on",
    "PARISHKIT_LOCAL_SMTP_LATENCY_MS": "600",
}


def test_local_mode_renders_the_chosen_bulk_switch_and_latency(tmp_path):
    """on and 600 reach retarget-image; the checks find them rendered."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", profile="local", bulk=False, env=REHEARSAL
    )
    retarget = calls[first(calls, " retarget-image ")]
    assert "PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1" in retarget
    assert "PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS=600" in retarget
    assert "modeled SMTP latency 600 ms per message" in output
    assert "NOT healthy" not in output


def test_local_mode_turns_the_bulk_send_off_and_carries_the_latency(tmp_path):
    """off drops the switch; an unset latency keeps the rendered one."""

    def rendered_latency(root):
        for role in ("worker", "mail-dispatch", "scheduler"):
            path = root / "config" / "services" / f"{role}.yaml"
            document = json.loads(path.read_text())
            document["deployment"]["local_smtp_latency_ms"] = 450
            path.write_text(json.dumps(document))

    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        prepare=rendered_latency,
        env={"PARISHKIT_LOCAL_BULK_FAMILY_SEND": "off"},
    )
    retarget = calls[first(calls, " retarget-image ")]
    assert "BULK_FAMILY_SEND" not in retarget and "keeping it on" not in output
    assert "PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS=450" in retarget
    assert "NOT healthy" not in output


def test_local_latency_is_base_ten_and_never_rendered_into_a_rollback(tmp_path):
    """0600 is 600 ms; a rollback drops the latency and says so."""
    calls, _, _ = run_host(
        tmp_path / "zeros",
        "upgrade",
        profile="local",
        env={"PARISHKIT_LOCAL_SMTP_LATENCY_MS": "0600"},
    )
    assert "LOCAL_SMTP_LATENCY_MS=600" in calls[first(calls, " retarget-image ")]
    calls, output, _ = run_host(
        tmp_path / "rollback",
        "rollback",
        profile="local",
        env={"PARISHKIT_LOCAL_SMTP_LATENCY_MS": "600"},
    )
    assert "LATENCY" not in calls[first(calls, " retarget-image ")]
    assert "not carried into a rollback" in output and "NOT healthy" not in output


@pytest.mark.parametrize(
    "name, value",
    [
        ("PARISHKIT_LOCAL_BULK_FAMILY_SEND", "maybe"),
        ("PARISHKIT_LOCAL_SMTP_LATENCY_MS", "fast"),
        ("PARISHKIT_LOCAL_SMTP_LATENCY_MS", "5001"),
    ],
)
def test_local_mode_refuses_an_unclear_rehearsal_choice(tmp_path, name, value):
    """Refused before anything stops."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", profile="local", status=1, env={name: value}
    )
    assert "refusing" in output
    assert not any(" stop " in call for call in calls)


def test_production_mode_never_reads_the_rehearsal_choices(tmp_path):
    """Eight arguments: the switch carries over and no latency is rendered."""
    calls, output, _ = run_host(tmp_path, "upgrade", bulk=False, env=REHEARSAL)
    retarget = calls[first(calls, " retarget-image ")]
    assert "BULK_FAMILY_SEND" not in retarget and "LATENCY" not in retarget
    assert "latency" not in output and f"Upgraded to {TARGET} in" in output
