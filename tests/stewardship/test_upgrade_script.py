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
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from parishkit.parishsoft import DEFAULT_API_BASE_URL

from .host_stubs import install_gnu_tools

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stewardship-upgrade.sh"
HOST = ROOT / "tools" / "stewardship-upgrade-host.sh"
PRUNE = ROOT / "tools" / "stewardship-prune-images.sh"
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
    """`bash -n` accepts the local script and the uploaded host scripts."""
    for script in (SCRIPT, HOST, PRUNE):
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
        ["shellcheck", "-s", "bash", str(SCRIPT), str(HOST), str(PRUNE)],
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
    # Only the host half: this stand-in reports no replaced release, so the
    # image pruning (#1661) is skipped.
    assert result.returncode == 0 and len(calls) == 1


def test_a_valid_digest_uploads_the_host_script_once(tmp_path):
    """One host-half ssh call, the arguments in order, the host half on its stdin.

    This stand-in reports no replaced release, so the image pruning
    (#1661), which test_prune_images_script.py pins, is skipped.
    """
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
# service list the re-rendered Compose file has after a retarget;
# FAKE_MIGRATE_FAIL makes the full migration fail (as the users swap does on
# an unconverted configuration) and FAKE_USERS_FAIL the users conversion.
# The digest retirement guard (#1834): FAKE_RETIREMENT is what the image's
# `test -f` of the retirement file exits with (default 0, the file is
# there), and FAKE_CONVERTED what the conversion query answers (default t).
# The users swap's pending state is kept in FAKE_STATE/swap_pending: the base
# migration, or a full one that fails, leaves it; a full one that succeeds
# clears it; and while it lasts every `up` is refused (recorded in
# FAKE_STATE/refused), as every online service refuses that schema.
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
# FAKE_HANG: a call containing it hangs, as a stuck one-off's client does.
if [ -n "$FAKE_HANG" ] && [[ "$*" == *"$FAKE_HANG"* ]]; then exec /bin/sleep 60; fi
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
    digest=$(printf 'f%.0s' {1..64})
    echo '{"backup_recorded": '"$recorded"', "manifest_digest": "'"$digest"'",' \
        '"offsite": {"state": "'"$1"'"}}'
}
# The database network layout (#2111), when FAKE_LAYOUT_RUNNING names the
# layout Docker runs (old or new): FAKE_NETWORKS_<layout> is what `network
# inspect` answers and FAKE_POSTGRES_<layout> the networks postgres is on.
# The retarget renders FAKE_COMPOSE_<FAKE_RENDER_LAYOUT>; starting postgres
# and valkey moves Docker to the rendered layout (unless FAKE_LAYOUT_STUCK).
# FAKE_ONEOFF adds a running one-off backup-worker container.
layout=$(cat "$FAKE_STATE/layout" 2>/dev/null || echo "${FAKE_LAYOUT_RUNNING-}")
# run_host upper-cases the FAKE_* names it is given.
upper=$(tr '[:lower:]' '[:upper:]' <<<"$layout")
networks_var=FAKE_NETWORKS_$upper
postgres_var=FAKE_POSTGRES_$upper
case "$*" in
    # The command-list probe (#235): FAKE_HELP is every image's list, and
    # FAKE_HELP_RUNNING the running image's (CURRENT) when set;
    # FAKE_HELP_FAIL names an image that does not answer.
    *" --help")
        img=${*: -2:1}
        [ "$img" != "${FAKE_HELP_FAIL-}" ] || exit 1
        listed="usage: pk-stewardship {config-check,retarget-image,smoke}"
        if [ -n "${FAKE_HELP_RUNNING-}" ] && [ "$img" = "${FAKE_RUNNING_IMAGE-}" ]; then
            echo "$FAKE_HELP_RUNNING"
        else
            echo "${FAKE_HELP:-$listed}"
        fi ;;
    # The contract's route-out preflight (#235): FAKE_ENGINE is the Engine
    # version (FAKE_ENGINE_FAIL: no answer); FAKE_PROBE_INTERNAL and
    # FAKE_PROBE_GATEWAY what the backup worker's network answers; the
    # probe records its arguments and prints FAKE_PROBE (default closed),
    # and FAKE_PROBE_ERR on its error output, exiting FAKE_PROBE_RC.
    "version --format {{.Server.Version}}")
        [ -z "${FAKE_ENGINE_FAIL-}" ] || exit 1
        echo "${FAKE_ENGINE:-27.3.1}" ;;
    "network inspect --format {{.Internal}} "*"_offline-backup-worker")
        echo "${FAKE_PROBE_INTERNAL:-true}" ;;
    "network inspect --format {{range .IPAM.Config}}{{.Gateway}}{{end}} "*"-worker")
        echo "${FAKE_PROBE_GATEWAY-172.29.242.65}" ;;
    *" egress-probe --gateway "*)
        echo "$*" >"$FAKE_STATE/probe"
        [ -z "${FAKE_PROBE_ERR-}" ] || echo "$FAKE_PROBE_ERR" >&2
        closed='{"closed": true, "engine_version": "27.3.1", "reached": []}'
        echo "${FAKE_PROBE:-$closed}"
        exit "${FAKE_PROBE_RC:-0}" ;;
    # The off-site uploader's step-1 copy (#235): FAKE_UPLOAD is its last
    # line (default: the digest's copy recorded); the arguments are logged.
    *" -T backup-uploader upload --config "*)
        copied='{"required_copy": "uploaded", "sets": 1, "state": "uploaded"}'
        echo '{"level":"DEBUG"}'
        echo "${FAKE_UPLOAD:-$copied}"
        [ -z "${FAKE_UPLOAD_FAIL-}" ] || exit 2 ;;
    # The uploader's password preflight: FAKE_UPLOADER_LOGINS is the count,
    # FAKE_UPLOADER_QUERY_FAIL fails it, FAKE_UPLOADER_ANSWER is the check's.
    *"count(*) FROM pg_authid"*)
        [ -z "$FAKE_UPLOADER_QUERY_FAIL" ] || exit 1
        echo "${FAKE_UPLOADER_LOGINS-0}" ;;
    *"coalesce(rolpassword"*) echo 'SCRAM-SHA-256$4096:c2FsdA==$x:y' ;;
    *" uploader-password-check --config "*)
        cat >"$FAKE_STATE/verifier_seen"
        [ -z "$FAKE_UPLOADER_CHECK_FAIL" ] || exit 2
        echo "${FAKE_UPLOADER_ANSWER:-match}" ;;
    # The subnet-overlap preflight: FAKE_UPLOADER_NETWORK is the subnet of the
    # project's own uploader network when Docker has it; FAKE_SUBNETS the
    # other networks, one "name subnet..." line each.
    *"{{range .IPAM.Config}}{{.Subnet}} {{end}} "*"_offline-backup-uploader")
        [ -n "${FAKE_UPLOADER_NETWORK-}" ] || exit 1
        echo "$FAKE_UPLOADER_NETWORK " ;;
    "network ls --quiet --no-trunc")
        [ -z "${FAKE_SUBNETS-}" ] || awk '{print "id-" $1}' <<<"$FAKE_SUBNETS" ;;
    "network inspect --format {{.Name}}{{range .IPAM.Config}} {{.Subnet}}{{end}} "*)
        echo "$FAKE_SUBNETS" ;;
    "network ls --quiet --filter "*)
        [ -z "$layout" ] || jq -r '.[].Id' <<<"${!networks_var}" ;;
    "network inspect --format "*)
        jq -r --arg id "$last" '.[] | select(.Id == $id) | .Name' \
            <<<"${!networks_var}" ;;
    "inspect --format "*"NetworkSettings.Networks"*" id-caddy")
        echo "stewardship_proxy " ;;
    "network inspect "*) echo "${!networks_var}" ;;
    "network rm "*) echo "$last" >>"$FAKE_STATE/networks_removed" ;;
    "ps --all --quiet --filter "*"service=postgres"*)
        [ -e "$FAKE_STATE/postgres_removed" ] || echo id-postgres ;;
    "inspect id-postgres")
        # Without a layout, postgres runs on the networks the file renders.
        on=${!postgres_var:-$(jq -c '.services.postgres.networks // {}
            | with_entries(.key |= "stewardship_" + .)' "$FAKE_COMPOSE")}
        echo '[{"State": {"Running": true},' \
            '"NetworkSettings": {"Networks": '"$on"'}}]' ;;
    "ps --all --filter "*" --format "*)
        printf '%s\n' "id-postgres running postgres False" \
            "id-valkey running valkey False" "id-caddy running caddy False" \
            "id-web exited web False"
        [ -z "$FAKE_ONEOFF" ] || echo "id-backup running backup-worker True" ;;
    "rm id-"*) touch "$FAKE_STATE/postgres_removed" ;;
    *" up --detach --wait postgres valkey")
        rm -f "$FAKE_STATE/postgres_removed"
        [ -n "$FAKE_LAYOUT_STUCK" ] || cp "$FAKE_STATE/rendered" "$FAKE_STATE/layout" ;;
    *" --entrypoint test "*0171_retire_digests.sql) exit "${FAKE_RETIREMENT:-0}" ;;
    *"0041_report_conversion"*) echo "${FAKE_CONVERTED:-t}" ;;
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
        if [ -e "$FAKE_STATE/swap_pending" ]; then
            echo "$*" >>"$FAKE_STATE/refused"
            exit 1
        fi
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
    *" hba-check --config "*)
        [ -z "$FAKE_HBA_RENDER_FAIL" ] || exit 2
        echo "SELECT 'hba_reload_pending';" ;;
    *"SELECT pg_reload_conf()"*) printf '%s\n' "2026-10-25 04:00:00.000001+00" t ;;
    *" -v since=infinity"*)
        # The check before the reload, of the file alone: FAKE_HBA_FILE, or
        # the pending answer every good file gives against infinity.
        cat >/dev/null
        echo "${FAKE_HBA_FILE:-hba_reload_pending}" ;;
    *" -v since="*)
        # FAKE_HBA: the hba check's answers after the reload, one per call,
        # the last repeated. The query arrives on stdin, as psql reads it.
        cat >/dev/null
        read -r -a answers <<<"${FAKE_HBA:-t}"
        echo x >>"$FAKE_STATE/hba"
        n=$(wc -l <"$FAKE_STATE/hba")
        [ "$n" -le "${#answers[@]}" ] || n=${#answers[@]}
        echo "${answers[$((n - 1))]}" ;;
    *" database-sources --config "*)
        # Read stdin as a real `compose exec` does, so a caller that forgets
        # </dev/null inside a read loop loses the rest of its list.
        cat >/dev/null
        # FAKE_SOURCES_FAIL: the one service whose check finds a difference.
        if [ -n "$FAKE_SOURCES_FAIL" ] && [[ " $* " == *" $FAKE_SOURCES_FAIL "* ]]; then
            echo "MISMATCH: pk_stewardship_operator is allowed from" \
                "172.29.240.13 ($FAKE_SOURCES_FAIL): the rendered rules refuse" \
                "it (security failure)" >&2
            exit 1
        fi
        echo "database sources match the rendered rules" ;;
    *"PGOPTIONS"*)
        cat >/dev/null
        echo x >>"$FAKE_STATE/checks"
        if [ -n "$FAKE_NOOP_LATER" ] && [ "$(wc -l <"$FAKE_STATE/checks")" -gt 1 ]
        then echo "$FAKE_NOOP_LATER"; else echo "$FAKE_NOOP"; fi ;;
    *" run --rm -T backup-worker"|*" run --rm -T backup-worker backup --config "*)
        echo '{"level":"DEBUG"}'
        if [ -n "$FAKE_BACKUP_GARBAGE" ]; then echo "ERROR: backup refused; no JSON"
        elif [ -n "$FAKE_BACKUP_FAIL" ]; then json failed
        elif [ -e "$FAKE_STATE/base_partial" ]; then
            # Migrations outside the swap pending: only the resume reuses
            # the earlier run's backup; a plain backup refuses.
            if [ "$last" = --users-upgrade-resume ]; then
                echo '{"backup_recorded": false, "backup_reused": true,' \
                    '"reused_backup": {"completed_at": "2026-10-10T04:00:00+00:00",' \
                    '"manifest_digest": "'"$(printf 'e%.0s' {1..64})"'"},' \
                    '"offsite": {"state": "'"${FAKE_OFFSITE:-uploaded}"'"}}'
            else echo "ERROR: backup refused or incomplete"; fi
        else json "${FAKE_OFFSITE:-uploaded}"; fi ;;
    *" retarget-image --config "*)
        [ -z "$FAKE_RETARGET_FAIL" ] || { echo "retarget refused" >&2; exit 1; }
        bulk=false batch=20 latency="" window="" minutes="" publication=false
        for arg in "$@"; do
            case "$arg" in
                PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1) bulk=true ;;
                PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES=*)
                    minutes=${arg#*=}
                    window=', "export_download_window_minutes": '"$minutes" ;;
                PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=*) batch=${arg#*=} ;;
                PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS=*)
                    latency=', "local_smtp_latency_ms": '"${arg#*=}" ;;
                PARISHKIT_STEWARDSHIP_PARISHSOFT_BASE_URL=*)
                    # FAKE_RETARGET_NO_PARISHSOFT: a retarget that lost it.
                    [ -n "$FAKE_RETARGET_NO_PARISHSOFT" ] ||
                        latency="$latency"', "parishsoft_base_url": "'"${arg#*=}"'"' ;;
                PARISHKIT_STEWARDSHIP_PARISHSOFT_PUBLICATION=1)
                    # FAKE_RETARGET_NO_PUBLICATION: a retarget that lost it.
                    [ -n "$FAKE_RETARGET_NO_PUBLICATION" ] || {
                        publication=true
                        latency="$latency"', "parishsoft_publication": true'
                    } ;;
                PARISHKIT_STEWARDSHIP_MAIL_REDIRECT=*)
                    # FAKE_RETARGET_NO_REDIRECT: a retarget that lost it.
                    [ -n "$FAKE_RETARGET_NO_REDIRECT" ] ||
                        latency="$latency"', "mail_redirect": "'"${arg#*=}"'"' ;;
            esac
        done
        if [ -n "$FAKE_HBA_RULES" ]; then
            # The source rules (#2111): PostgreSQL's command names the
            # mounted file, and the application services name their documents.
            jq -n --arg i "$last" --arg c "$(dirname "$FAKE_COMPOSE")" '{services: {
                web: {image: $i, command: ["runtime", "--config", "\($c)/web.yaml"]},
                worker: {command: ["runtime", "--config", "\($c)/worker.yaml"]},
                migration: {profiles: ["migration"],
                    command: ["migrate", "--config", "\($c)/migration.yaml"]},
                caddy: {command: ["exec caddy run"]},
                postgres: {command: ["postgres", "-c",
                    "hba_file=/etc/postgresql/hba/pg_hba.conf"]}}}' >"$FAKE_COMPOSE"
        else
            echo '{"services": {"web": {"image": "'"$last"'"}}}' >"$FAKE_COMPOSE"
        fi
        if [ -n "$FAKE_OVERRIDE" ]; then
            derived="parishkit-stewardship-local-faketime-stewardship:${last##*:}"
            echo '{"services": {"web": {"image": "'"$derived"'"},' \
                '"postgres": {"image": "faketime-postgres:x"},' \
                '"valkey": {"image": "faketime-valkey:x"}}}' >"$FAKE_OVERRIDE"
        fi
        for role in worker mail-dispatch scheduler; do
            doc='{"deployment": {"bulk_family_send": '"$bulk"','
            echo "$doc"' "bulk_send_batch": '"$batch$latency$window"'}}' \
                >"$(dirname "$FAKE_COMPOSE")/$role.yaml"
        done
        # web.yaml keeps its other inputs; only the window and the
        # publication switch are re-rendered.
        web="$(dirname "$FAKE_COMPOSE")/web.yaml"
        key=.deployment.export_download_window_minutes
        if [ -n "$minutes" ]; then
            jq "$key = $minutes" "$web" >"$web.new"
        else
            jq "del($key)" "$web" >"$web.new"
        fi
        key=.deployment.parishsoft_publication
        if [ "$publication" = true ]; then
            jq "$key = true" "$web.new" >"$web"
        else
            jq "del($key)" "$web.new" >"$web"
        fi
        rm -f "$web.new"
        if [ -n "$FAKE_RENDER_LAYOUT" ]; then
            upper=$(tr '[:lower:]' '[:upper:]' <<<"$FAKE_RENDER_LAYOUT")
            rendered_var=FAKE_COMPOSE_$upper
            jq --arg image "$last" '.services.web.image = $image' \
                <<<"${!rendered_var}" >"$FAKE_COMPOSE"
            echo "$FAKE_RENDER_LAYOUT" >"$FAKE_STATE/rendered"
        fi
        touch "$FAKE_STATE/retargeted" ;;
    *" --rm -T migration migrate --config "*" --users-upgrade-base")
        if [ -n "$FAKE_BASE_FAIL" ]; then
            # Stopped partway: migrations outside the swap still pending.
            touch "$FAKE_STATE/base_partial"
            echo "Migration refused partway" >&2
            exit 1
        fi
        rm -f "$FAKE_STATE/base_partial"
        touch "$FAKE_STATE/swap_pending"
        echo "migrated to the base" ;;
    *" --rm -T migration")
        if [ -n "$FAKE_MIGRATE_FAIL" ]; then
            touch "$FAKE_STATE/swap_pending"
            echo "Migration 0052 refused" >&2
            if [ -n "$FAKE_MIGRATE_TRACEBACK" ]; then
                above="The above exception was the direct cause"
                above+=" of the following exception:"
                # An uncaught psycopg error, chained as Django raises it,
                # whose frames, message and DETAIL carry a row's values.
                printf '%s\n' "Traceback (most recent call last):" \
                    '  File "/app/x.py", line 1, in <module>' \
                    "    cursor.execute(sql, ['secret-param@example.org'])" \
                    'psycopg.errors.UniqueViolation: duplicate key value' \
                    'DETAIL:  Key (email)=(secret-detail@example.org) exists.' \
                    "" "$above" \
                    "" "Traceback (most recent call last):" \
                    '  File "/app/y.py", line 2, in <module>' \
                    'django.db.utils.IntegrityError: duplicate key value' \
                    'DETAIL:  Key (email)=(secret-chained@example.org) exists.' \
                    "ERROR: IntegrityError (SQLSTATE 23505)" \
                    "  DETAIL:  secret-loose@example.org" >&2
            fi
            exit 1
        fi
        rm -f "$FAKE_STATE/swap_pending"
        echo "migrated" ;;
    *" upgrade-users "*"--operation-id "*)
        [ -z "$FAKE_USERS_FAIL" ] || { echo "upgrade refused" >&2; exit 1; }
        echo '{"users_upgrade_applied": true}' ;;
    *"CREATE EXTENSION IF NOT EXISTS pg_trgm"*)
        if [ -n "$FAKE_EXTENSION_FAIL" ]; then
            echo 'ERROR:  permission denied to create extension "pg_trgm"' >&2
            exit 1
        fi
        echo "CREATE EXTENSION" ;;
    *"database-grants"*)
        if [ -n "$FAKE_GRANTS_FAIL" ]; then
            # The cause, then an empty last line, as a real traceback ends.
            printf '%s\n' "grants refused" "UndefinedFunction: no such function" "" >&2
            exit 1
        fi
        echo "granted" ;;
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


# What `ss -Htlnu` answers by default: listeners at the wildcard addresses
# (IPv4, IPv6 and "*"), at the backup worker's gateway, and on loopback
# only, which the probe skips.
SS_LISTENERS = "\n".join(
    [
        "tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*",
        "tcp   LISTEN 0      4096               *:443              *:*",
        "tcp   LISTEN 0      4096       127.0.0.1:5000       0.0.0.0:*",
        "udp   UNCONN 0      0      127.0.0.53%lo:53         0.0.0.0:*",
        "udp   UNCONN 0      0            0.0.0.0:68         0.0.0.0:*",
        "tcp   LISTEN 0      4096            [::]:22            [::]:*",
        "tcp   LISTEN 0      4096   172.29.242.65:9999       0.0.0.0:*",
    ]
)


# The host's INPUT chain by default: the one-shot networks' drop first,
# ahead of ufw's jumps, as the backup runbook's unit leaves it.
INPUT_CHAIN = "\n".join(
    [
        "-P INPUT DROP",
        "-A INPUT -s 172.29.242.0/24 -j DROP",
        "-A INPUT -j ufw-before-logging-input",
        "-A INPUT -j ufw-before-input",
    ]
)


def runbook_unit(block="172.29.242.0/24"):
    """The backup runbook's INPUT-drop unit, for BLOCK, as `systemctl cat` shows it.

    The unit the Administrator installs is the runbook's text itself, so the
    check and the runbook cannot drift apart.
    """
    text = (ROOT / "docs" / "guides" / "stewardship-backup-runbook.md").read_text(
        encoding="utf-8"
    )
    start = text.index("[Unit]\nDescription=Stewardship: drop traffic")
    unit = text[start : text.index("\nEOF\n", start)]
    return unit.replace("OFFLINE_BLOCK", block)


def lay_out(tmp_path, local, current, target, clock, bulk, kept, prepare):
    """Lay the runtime root out as a set-up deployment, before the first run."""
    root = tmp_path / "root"
    services = root / "config" / "services"
    services.mkdir(parents=True)
    (services / "Caddyfile").write_text("caddy\n")
    (services / "compose.json").write_text(
        json.dumps({"services": {"web": {"image": current}}})
    )
    origin = "https://localhost:8443" if local else "https://stewardship.example.test"
    web = {
        "deployment": {
            "postgres": {"name": "stewardship"},
            "public_origin": origin,
            "paths": {"backups": str(root / "backups")},
        }
    }
    (root / "backups").mkdir(mode=0o700)
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
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "docker.log").touch()


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
    again=False,
    interrupt=None,
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
    `env` adds to the script's environment. `again` repeats a run in the
    same `tmp_path`, keeping the runtime root and the stand-in's state as
    the earlier run left them (the other layout arguments are then unused).
    `interrupt` is `(fragment, signal)`: once a docker call containing the
    fragment is recorded, the script is sent the signal.
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
    local = profile == "local"
    current = LOCAL_CURRENT if local else CURRENT
    target = image or (LOCAL_TARGET if local else TARGET)
    yaml = tmp_path / "deployment.yaml"
    build = tmp_path / "build"
    if not again:
        lay_out(tmp_path, local, current, target, clock, bulk, kept, prepare)
    bin_dir = tmp_path / "bin"
    executable_stub(bin_dir, "docker", FAKE_DOCKER)
    # Root-only `install -o`, real waiting and the public-origin probe are
    # replaced; FAKE_ORIGIN_FAIL makes the origin unreachable.
    executable_stub(bin_dir, "install", 'mkdir -p "${@: -1}"')
    executable_stub(bin_dir, "sleep", "exit 0")
    # The off-site copy lock and crontabs (#235): util-linux flock is not on
    # every test host, so the stand-in records each call and exits with
    # FAKE_FLOCK_FAIL when set (75 is `flock -E 75 -w`'s expired wait);
    # crontab answers FAKE_CRONTAB, and the system crontab and cron.d are
    # the test's own (below). The lock needs GNU stat and ln.
    executable_stub(
        bin_dir,
        "flock",
        'echo "$*" >>"$FAKE_STATE/flock"; '
        '[ -z "$FAKE_FLOCK_FAIL" ] || exit "$FAKE_FLOCK_FAIL"',
    )
    install_gnu_tools(bin_dir)
    executable_stub(bin_dir, "crontab", 'echo "${FAKE_CRONTAB:-}"')
    executable_stub(bin_dir, "chown", 'echo "$*" >>"$FAKE_STATE/chown"')
    # The contract's route-out preflight (#235): the INPUT chain (FAKE_INPUT,
    # default INPUT_CHAIN; FAKE_IPTABLES_FAIL fails it), the drop's unit
    # (FAKE_UNIT, default the runbook's; FAKE_UNIT_DISABLED), the host's
    # resolution of the Google API name (FAKE_NO_GOOGLE) and its listening
    # sockets (FAKE_SS, or FAKE_SS_FAIL).
    executable_stub(
        bin_dir,
        "iptables",
        'echo "iptables $*" >>"$FAKE_LOG"; [ -z "$FAKE_IPTABLES_FAIL" ] || exit 1; '
        'printf "%s\\n" "${FAKE_INPUT-$INPUT_DEFAULT}"',
    )
    executable_stub(
        bin_dir,
        "systemctl",
        'echo "systemctl $*" >>"$FAKE_LOG"; case "$1" in '
        'is-enabled) [ -z "$FAKE_UNIT_DISABLED" ] ;; '
        'cat) printf "%s\\n" "${FAKE_UNIT-$UNIT_DEFAULT}" ;; *) exit 1 ;; esac',
    )
    executable_stub(
        bin_dir,
        "getent",
        '[ -z "$FAKE_NO_GOOGLE" ] || exit 2; '
        'echo "142.250.1.95    STREAM www.googleapis.com"',
    )
    executable_stub(
        bin_dir,
        "ss",
        '[ -z "$FAKE_SS_FAIL" ] || exit 1; printf "%s\\n" "${FAKE_SS:-$SS_DEFAULT}"',
    )
    # The host's routes for the uploader's subnet-overlap preflight (#235).
    executable_stub(bin_dir, "ip", 'echo "ip $*" >>"$FAKE_LOG"; echo "$FAKE_ROUTES"')
    # The stand-in answers as the application does through caddy (the local
    # mode reads the Server header; Production only the exit status).
    executable_stub(
        bin_dir,
        "curl",
        'echo "curl $*" >>"$FAKE_LOG"; [ -z "$FAKE_ORIGIN_FAIL" ] || exit 7; '
        'printf "HTTP/2 200\\r\\nserver: gunicorn\\r\\n"',
    )
    # coreutils timeout is Linux-only here; the stand-in skips its options
    # and runs the command, unless the command contains FAKE_TIMEOUT: then
    # it answers as an expired limit does, without running it. A one-off
    # `docker compose run` that inherited the copy lock's descriptors 8 or
    # 9 is recorded in held-fds (#235).
    executable_stub(
        bin_dir,
        "timeout",
        'if [[ "$*" == *" run --name "* ]] && '
        "{ [ -e /dev/fd/8 ] || [ -e /dev/fd/9 ]; }; "
        'then echo "$*" >>"$FAKE_STATE/held-fds"; fi; '
        'if [ -n "$FAKE_TIMEOUT" ] && [[ "$*" == *"$FAKE_TIMEOUT"* ]]; then '
        'echo "timeout $*" >>"$FAKE_LOG"; exit 124; fi; '
        'while [[ $1 == -* ]]; do shift; done; shift; exec "$@"',
    )
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    log = tmp_path / "docker.log"
    project = "parishkit-local" if local else "stewardship"
    trailing = [] if profile is None else [profile, str(build)]
    override = str(services / "compose.faketime.json") if clock == "fake" else ""
    calls_before = len(log.read_text().splitlines())
    process = subprocess.Popen(
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
            "PK_RESTORE_CRONTAB": str(tmp_path / "crontab"),
            "PK_RESTORE_CRON_DIR": str(tmp_path / "cron.d"),
            "SS_DEFAULT": SS_LISTENERS,
            "INPUT_DEFAULT": INPUT_CHAIN,
            "UNIT_DEFAULT": runbook_unit(),
            **{"FAKE_" + name.upper(): str(value) for name, value in fake.items()},
            **(env or {}),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if interrupt is not None:
            fragment, signum = interrupt
            deadline = time.monotonic() + 30
            while not any(
                fragment in call for call in log.read_text().splitlines()[calls_before:]
            ):
                assert process.poll() is None, "the run ended before the fragment"
                assert time.monotonic() < deadline, "the fragment was never called"
                time.sleep(0.05)
            process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    output = stdout + stderr
    assert process.returncode == status, output
    # The whole log is also kept on the host, named after the mode (a usage
    # error ends before the log opens). A repeated run in the same second
    # appends to the earlier run's file.
    kept_logs = list(logs.glob(f"stewardship-{mode}-*.log"))
    if status != 2:
        assert kept_logs and all("Log: " in path.read_text() for path in kept_logs)
        assert again or len(kept_logs) == 1
    # Only this run's docker calls.
    return log.read_text().splitlines()[calls_before:], output, root


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
    checks = [i for i, c in enumerate(calls) if "PGOPTIONS" in c and "0041" not in c]
    assert retarget < checks[1]
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


def test_an_owning_inspection_login_refuses_before_anything_stops(tmp_path):
    """The ownership cause (#2291) stops step 1 even with the override.

    database-grants would refuse it in step 4, after everything stopped.
    """
    cause = "The inspection login owns objects (large objects in database x: 1)."
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop=cause, schema_change="1", status=1
    )
    assert f"advisory answer: {cause}" in output
    assert "#clean-up-when-the-inspection-login-owns-objects" in output
    assert "Failed before anything stopped; nothing changed" in output
    assert not any(" stop " in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)


def test_an_upgrade_with_the_override_runs_migration_and_grants(tmp_path):
    """With the override, both commands run after the retarget, before web."""
    calls, output, _ = run_host(tmp_path, "upgrade", noop="f", schema_change="1")
    retarget = first(calls, " retarget-image ")
    migration = first(calls, "--rm -T migration")
    assert retarget < migration < first(calls, "database-grants")
    assert migration < first(calls, "up --detach --wait web")
    assert "running migration and grants: the check answered: f" in output


def test_the_operator_installs_the_text_search_extension_before_migrating(tmp_path):
    """pg_trgm (#765) is installed as the superuser right before the
    migration, only when step 4 migrates; a refusal stops the run with the
    maintenance page up and nothing migrated."""
    install = "psql -U pk_stewardship_operator -d stewardship -At -v ON_ERROR_STOP=1 "
    calls, output, _ = run_host(tmp_path, "upgrade", noop="f", schema_change="1")
    extension = first(
        calls, "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"
    )
    assert install in calls[extension] and " exec -T postgres " in calls[extension]
    assert (
        first(calls, " retarget-image ") < extension < first(calls, "--rm -T migration")
    )
    assert "pg_trgm is installed" in output
    # The users upgrade installs it before its base migration too.
    calls, _, _ = run_host(
        tmp_path / "users", "upgrade", noop="f", schema_change="1", env=USERS
    )
    assert first(calls, "pg_trgm") < first(calls, "--users-upgrade-base")
    # A release that migrates nothing never touches it.
    calls, _, _ = run_host(tmp_path / "current", "upgrade")
    assert not any("pg_trgm" in call for call in calls)
    calls, output, _ = run_host(
        tmp_path / "refused",
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        extension_fail=True,
    )
    refused = 'ERROR:  permission denied to create extension "pg_trgm"'
    assert f"Could not install the pg_trgm extension: {refused}" in output
    assert not any(" migration" in call for call in calls)
    assert "caddy serves the maintenance page" in output
    # A hung install is killed under its own limit and logged: what ran,
    # the limit and the elapsed time; nothing migrates, the page stays up.
    calls, output, _ = run_host(
        tmp_path / "hung",
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        timeout="CREATE EXTENSION",
    )
    killed = next(call for call in calls if call.startswith("timeout "))
    assert killed.startswith("timeout --kill-after=10 60 docker compose ")
    assert "TIMEOUT: installing the pg_trgm extension killed after " in output
    assert "(limit 60s)" in output
    assert not any(" migration" in call for call in calls)
    assert "caddy serves the maintenance page" in output
    log = next((tmp_path / "hung" / "logs").glob("stewardship-upgrade-*.log"))
    assert "TIMEOUT: installing the pg_trgm extension killed after " in log.read_text()


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


def test_the_retirement_guard_names_the_real_retire_digests_migration():
    """The host script's release-order guard must name a migration that exists.

    The guard probes the image for one hard-coded file name; a later renumber
    would make that probe always miss and silently disable the guard.
    """
    names = re.findall(r"/migrations/(\d{4}_[a-z_]+\.sql)$", HOST.read_text(), re.M)
    assert names == ["0171_retire_digests.sql"]
    migrations = ROOT / "src/parishkit/stewardship/schema/migrations"
    assert (migrations / names[0]).is_file()
    assert "retire" in names[0] and "digest" in names[0]


def test_the_retirement_guard_names_the_real_conversion_migration():
    """The guard's applied-migration probe must name a migration that exists.

    The query hard-codes the report email conversion's Django migration; a
    renumber of it would make every converted database answer f, so the
    guard would refuse every upgrade (closed, but disruptive).
    """
    names = re.findall(
        r"app='(stewardship_[a-z]+)' AND name='(\d{4}_[a-z_]+)'", HOST.read_text()
    )
    assert names == [("stewardship_reports", "0041_report_conversion")]
    migrations = ROOT / "src/parishkit/stewardship/reports/migrations"
    assert (migrations / f"{names[0][1]}.py").is_file()


@pytest.mark.parametrize("converted", ["f", 'ERROR:  relation "x" does not exist'])
def test_the_digest_retirement_refuses_an_unconverted_database_first(
    tmp_path, converted
):
    """A release with 0171 on an unconverted database stops before anything.

    The conversion's migration not applied, a definition still a digest or
    a failed query all answer something other than t: the run refuses in
    step 1, naming the converting release, with nothing stopped.
    """
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop="f", schema_change="1", status=1, converted=converted
    )
    test = first(calls, " --entrypoint test ")
    assert "/app/src/parishkit/stewardship/schema/migrations/0171_" in calls[test]
    query = first(calls, "0041_report_conversion")
    assert "default_transaction_read_only=on" in calls[query]
    assert "kind IN ('daily_digest','weekly_digest')" in calls[query]
    assert "has not finished the report email conversion" in output
    assert "Deploy the release that converts the digests" in output
    assert "pk-stewardship upgrade-reports" in output
    assert "Failed before anything stopped; nothing changed" in output
    assert not any(" stop " in call for call in calls)
    assert not any("backup-worker" in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)
    assert "Locally:" not in output
    # The local profile has no releases: it names a commit to deploy.
    calls, output, _ = run_host(
        tmp_path / "local", "upgrade", noop="f", schema_change="1", status=1,
        converted=converted, profile="local",
    )  # fmt: skip
    assert "Locally: deploy a commit that converts the digests" in output
    assert "frozen file 0161 but not 0171" in output
    assert not any(" stop " in call for call in calls)


def test_the_digest_retirement_check_needs_the_file_and_an_upgrade(tmp_path):
    """Only an upgrade to an image with 0171 asks the database; t passes.

    An image without the file never runs the query, a converted database
    upgrades as usual, a rollback never asks, and a failure to ask the
    image refuses.
    """
    calls, _, _ = run_host(tmp_path / "absent", "upgrade", retirement="1")
    assert any(" --entrypoint test " in call for call in calls)
    assert not any("0041_report_conversion" in call for call in calls)
    calls, output, _ = run_host(tmp_path / "converted", "upgrade")
    assert first(calls, "0041_report_conversion") < first(calls, " stop web")
    assert f"Upgraded to {TARGET} in" in output
    calls, _, _ = run_host(tmp_path / "rollback", "rollback", kept="kept\n")
    assert not any(" --entrypoint test " in call for call in calls)
    calls, output, _ = run_host(
        tmp_path / "broken", "upgrade", status=1, retirement="125"
    )
    assert "whether it retires the digests (exit 125); refusing" in output
    assert not any(" stop " in call for call in calls)


def test_a_refused_retirement_migration_names_the_restore(tmp_path):
    """The step-4 fallback says 0161 and later may have committed: restore first."""
    _, output, _ = run_host(
        tmp_path, "upgrade", noop="f", schema_change="1", status=1, migrate_fail="1"
    )
    assert "the conversion's 0161 and any later one up to the one" in output
    assert "that refused) may already have committed" in output
    assert "Restore the pre-upgrade backup with the previous" in output
    assert "do not run the" in output


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


def _window(minutes):
    """A ``prepare`` that renders the worker document with ``minutes``."""

    def prepare(root):
        """Add the export download window to the current worker document."""
        path = root / "config" / "services" / "worker.yaml"
        document = json.loads(path.read_text())
        document["deployment"]["export_download_window_minutes"] = minutes
        path.write_text(json.dumps(document))

    return prepare


def test_the_export_download_window_is_carried_over_and_can_change(tmp_path):
    """The default renders nothing; a set window is carried into the retarget,
    and the operator's variable changes it (#1398)."""
    name = "PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES"
    calls, output, _ = run_host(tmp_path / "default", "upgrade")
    assert name not in calls[first(calls, " retarget-image ")]
    assert "export download window" not in output
    calls, output, _ = run_host(tmp_path / "kept", "upgrade", prepare=_window(30))
    assert f"{name}=30" in calls[first(calls, " retarget-image ")]
    assert "export download window is 30 minutes; keeping it" in output
    calls, output, _ = run_host(
        tmp_path / "changed", "upgrade", prepare=_window(30), env={name: "15"}
    )
    assert f"{name}=15" in calls[first(calls, " retarget-image ")]
    # Base ten: 060 is the default, which is passed as nothing and rendered
    # as nothing, so the post-check names no problem.
    calls, output, _ = run_host(tmp_path / "zero", "upgrade", env={name: "060"})
    assert name not in calls[first(calls, " retarget-image ")]
    assert "export download window is" not in output


@pytest.mark.parametrize("value", ["1h", "0", "14", "241", "1000", "-5", "15.5"])
def test_an_out_of_range_window_is_refused_before_anything_stops(tmp_path, value):
    """The window is bounded before web stops, as the SMTP latency is: the
    loader's own refusal would come only at the retarget, with web down."""
    name = "PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES"
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, env={name: value})
    assert "export download window must be 15-240 minutes" in output
    assert not any(" retarget-image " in call for call in calls)
    assert not any(" stop " in call for call in calls)


def test_a_rollback_does_not_carry_the_window(tmp_path):
    """A rollback renders the 60-minute default and says so: the previous
    release may not know the variable, and its loader would refuse it after
    web had stopped (#1398)."""
    name = "PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES"
    calls, output, _ = run_host(tmp_path, "rollback", prepare=_window(30))
    assert name not in calls[first(calls, " retarget-image ")]
    assert "is not carried into a rollback" in output
    assert "export download window is 30 minutes; keeping it" not in output


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
    env = REHEARSAL | {"PARISHKIT_LOCAL_PARISHSOFT": "real"}
    calls, output, _ = run_host(tmp_path, "upgrade", bulk=False, env=env)
    retarget = calls[first(calls, " retarget-image ")]
    assert "BULK_FAMILY_SEND" not in retarget and "LATENCY" not in retarget
    assert "PARISHSOFT" not in retarget and "ParishSoft" not in output
    assert "latency" not in output and f"Upgraded to {TARGET} in" in output


# Which ParishSoft pk-local reads (#1399): the fake unless the caller chose
# the real API, carried over like the latency and dropped from a rollback.
REAL_PARISHSOFT = "PARISHKIT_STEWARDSHIP_PARISHSOFT_BASE_URL=" + DEFAULT_API_BASE_URL


def rendered_real_parishsoft(root):
    """The running worker document already reads the real ParishSoft API."""
    path = root / "config" / "services" / "worker.yaml"
    document = json.loads(path.read_text())
    document["deployment"]["parishsoft_base_url"] = DEFAULT_API_BASE_URL
    path.write_text(json.dumps(document))


# Census publication (#1884): rendered with the fake, never with the real API
# and never into a rollback.
PUBLICATION = "PARISHKIT_STEWARDSHIP_PARISHSOFT_PUBLICATION=1"


def test_local_mode_renders_the_chosen_parishsoft(tmp_path):
    """real renders the real API; keep carries it; fake and rollback drop it.
    Publication is on with the fake, except in a rollback (#1884)."""
    calls, output, _ = run_host(
        tmp_path / "real",
        "upgrade",
        profile="local",
        env={"PARISHKIT_LOCAL_PARISHSOFT": "real"},
    )
    retarget = calls[first(calls, " retarget-image ")]
    assert REAL_PARISHSOFT in retarget and PUBLICATION not in retarget
    assert "reading the real ParishSoft API (never writing)" in output
    assert "Update ParishSoft" not in output
    assert "NOT healthy" not in output
    calls, output, _ = run_host(
        tmp_path / "keep", "upgrade", profile="local", prepare=rendered_real_parishsoft
    )
    retarget = calls[first(calls, " retarget-image ")]
    assert REAL_PARISHSOFT in retarget and PUBLICATION not in retarget
    assert "NOT healthy" not in output
    for mode, env, publication in (
        ("upgrade", {"PARISHKIT_LOCAL_PARISHSOFT": "fake"}, True),
        ("rollback", {}, False),
    ):
        calls, output, _ = run_host(
            tmp_path / mode,
            mode,
            profile="local",
            prepare=rendered_real_parishsoft,
            env=env,
        )
        retarget = calls[first(calls, " retarget-image ")]
        assert "PARISHSOFT_BASE_URL" not in retarget
        assert (PUBLICATION in retarget) is publication
        assert "NOT healthy" not in output
    assert "not carried into a rollback (it reads the fake)" in output
    assert "Update ParishSoft is not carried into a rollback" in output
    # The fake is still not rendered at all; publication is.
    calls, output, _ = run_host(tmp_path / "default", "upgrade", profile="local")
    retarget = calls[first(calls, " retarget-image ")]
    assert "PARISHSOFT_BASE_URL" not in retarget and PUBLICATION in retarget
    assert "Update ParishSoft writes to the fake ParishSoft only" in output


def test_local_mode_reports_a_publication_switch_the_retarget_lost(tmp_path):
    """The post-check names the publication switch it did not find rendered."""
    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        status=1,
        env={"FAKE_RETARGET_NO_PUBLICATION": "1"},
    )
    assert "NOT healthy" in output
    assert "worker: ParishSoft publication is false, not true" in output
    assert "web: ParishSoft publication is false, not true" in output


def test_local_mode_reports_a_parishsoft_choice_the_retarget_lost(tmp_path):
    """The post-check names the ParishSoft base URL it did not find rendered."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        status=1,
        env={"PARISHKIT_LOCAL_PARISHSOFT": "real", "FAKE_RETARGET_NO_PARISHSOFT": "1"},
    )
    assert "NOT healthy" in output
    assert (
        f"worker: ParishSoft base URL is 'the fake', not '{DEFAULT_API_BASE_URL}'"
        in output
    )


def test_local_mode_warns_about_the_real_api_under_the_fake_clock(tmp_path):
    """Faked processes check ParishSoft's certificate against a past date."""
    env = {"PARISHKIT_LOCAL_PARISHSOFT": "real"}
    _, output, _ = run_host(
        tmp_path / "fake", "upgrade", profile="local", clock="fake", env=env
    )
    assert "WARNING: the real ParishSoft API under the fake clock" in output
    _, output, _ = run_host(tmp_path / "normal", "upgrade", profile="local", env=env)
    assert "WARNING" not in output


def test_local_mode_refuses_an_unclear_parishsoft_choice(tmp_path):
    """An unknown choice, or a rendered URL that is not the real API, is
    refused before anything stops."""

    def other(root):
        path = root / "config" / "services" / "worker.yaml"
        document = json.loads(path.read_text())
        document["deployment"]["parishsoft_base_url"] = "https://other.example"
        path.write_text(json.dumps(document))

    for name, prepare, env in (
        ("choice", None, {"PARISHKIT_LOCAL_PARISHSOFT": "production"}),
        ("rendered", other, {}),
    ):
        calls, output, _ = run_host(
            tmp_path / name,
            "upgrade",
            profile="local",
            status=1,
            prepare=prepare,
            env=env,
        )
        assert "refusing" in output
        assert not any(" stop " in call for call in calls)


# The forced mail redirect (#1399): carried over like the ParishSoft choice,
# "off" removes it, and a rollback drops it (a real key is then refused).
REDIRECT = "PARISHKIT_STEWARDSHIP_MAIL_REDIRECT=jeff@example.org"


def rendered_redirect(root):
    """The running worker document already carries a mail redirect."""
    path = root / "config" / "services" / "worker.yaml"
    document = json.loads(path.read_text())
    document["deployment"]["mail_redirect"] = "jeff@example.org"
    path.write_text(json.dumps(document))


def test_local_mode_renders_the_mail_redirect(tmp_path):
    """An address renders it; keep carries it; off and rollback drop it."""
    calls, output, _ = run_host(
        tmp_path / "set",
        "upgrade",
        profile="local",
        env={"PARISHKIT_LOCAL_MAIL_REDIRECT": "jeff@example.org"},
    )
    assert REDIRECT in calls[first(calls, " retarget-image ")]
    assert "every message goes only to the mail redirect jeff@example.org" in output
    assert "NOT healthy" not in output
    calls, output, _ = run_host(
        tmp_path / "keep", "upgrade", profile="local", prepare=rendered_redirect
    )
    assert REDIRECT in calls[first(calls, " retarget-image ")]
    assert "NOT healthy" not in output
    for mode, env in (
        ("upgrade", {"PARISHKIT_LOCAL_MAIL_REDIRECT": "off"}),
        ("rollback", {}),
    ):
        calls, output, _ = run_host(
            tmp_path / mode, mode, profile="local", prepare=rendered_redirect, env=env
        )
        assert "MAIL_REDIRECT" not in calls[first(calls, " retarget-image ")]
        assert "NOT healthy" not in output
    assert "mail redirect is not carried into a rollback" in output
    # Unchanged from before the setting: nothing is rendered by default.
    calls, _, _ = run_host(tmp_path / "default", "upgrade", profile="local")
    assert "MAIL_REDIRECT" not in calls[first(calls, " retarget-image ")]


def test_local_mode_reports_a_redirect_the_retarget_lost(tmp_path):
    """The post-check names the mail redirect it did not find rendered."""
    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        status=1,
        env={
            "PARISHKIT_LOCAL_MAIL_REDIRECT": "jeff@example.org",
            "FAKE_RETARGET_NO_REDIRECT": "1",
        },
    )
    assert "NOT healthy" in output
    assert "worker: mail redirect is 'unset', not 'jeff@example.org'" in output


@pytest.mark.parametrize(
    "value", ["Jeff <jeff@example.org>", "a@example.org,b@example.org", "nobody"]
)
def test_local_mode_refuses_an_unclear_redirect(tmp_path, value):
    """Anything but one plain address is refused before anything stops."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        profile="local",
        status=1,
        env={"PARISHKIT_LOCAL_MAIL_REDIRECT": value},
    )
    assert "The mail redirect must be one plain email address; refusing." in output
    assert not any(" stop " in call for call in calls)


def test_production_mode_never_reads_the_redirect(tmp_path):
    """Production renders no redirect whatever the caller's environment says."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        env={"PARISHKIT_LOCAL_MAIL_REDIRECT": "jeff@example.org"},
    )
    assert "MAIL_REDIRECT" not in calls[first(calls, " retarget-image ")]
    assert "redirect" not in output and f"Upgraded to {TARGET} in" in output


def test_host_half_names_the_real_parishsoft_api():
    """The host half's copy of the real API's base URL is the package's."""
    assert (
        f"parishsoft_api={DEFAULT_API_BASE_URL}\n"
        in (ROOT / "tools" / "stewardship-upgrade-host.sh").read_text()
    )


# ---------------------------------------------------------------------------
# The users upgrade (#952 slice 4): the conversion between two migrations.

USERS = {
    "STEWARDSHIP_USERS_OPERATION": "11111111-2222-4333-8444-555555555555",
    "STEWARDSHIP_USERS_OPERATOR": "Test Operator",
    "STEWARDSHIP_USERS_REASON": "Users revamp release",
    "STEWARDSHIP_USERS_ADMINISTRATORS": "first@example.test,second@example.test",
}


def test_the_users_upgrade_converts_between_the_two_migrations(tmp_path):
    """Base migration, grants, preview, conversion, migration, grants, then web."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop="f", schema_change="1", env=USERS
    )
    base = first(calls, "migration migrate --config ")
    assert calls[base].endswith(
        "--rm -T migration migrate --config "
        f"{tmp_path}/root/config/services/migration.yaml --users-upgrade-base"
    )
    grants = [i for i, call in enumerate(calls) if "database-grants" in call]
    preview = first(calls, " upgrade-users ")
    convert = first(calls, "--operation-id ")
    full = next(i for i, call in enumerate(calls) if call.endswith("--rm -T migration"))
    assert first(calls, " retarget-image ") < base < grants[0] < preview < convert
    assert convert < full < grants[1] < first(calls, "up --detach --wait web")
    assert len(grants) == 2
    assert calls[preview].endswith(
        f"--config {tmp_path}/root/config/services/admin-recovery.yaml "
        f"--confirm-deployment {UUID} --preview"
    )
    # The operator's inputs reach the command exactly, one argument each.
    assert calls[convert].endswith(
        f"--confirm-deployment {UUID} "
        "--operation-id 11111111-2222-4333-8444-555555555555 "
        "--operator-name Test Operator --reason Users revamp release "
        "--confirm-administrators first@example.test,second@example.test"
    )
    assert "users upgrade: operation 11111111-2222-4333-8444-555555555555, " in output
    assert "keeping 2 confirmed Administrators" in output
    # No address is printed by the script itself.
    assert "first@example.test" not in output.replace(calls[convert], "")
    # Already current: the decisive check's t skips all of it.
    calls, output, _ = run_host(
        tmp_path / "current",
        "upgrade",
        noop="f",
        noop_later="t",
        schema_change="1",
        env=USERS,
    )
    assert not any("upgrade-users" in call or " migration" in call for call in calls)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"STEWARDSHIP_USERS_REASON": ""}, "needs all four"),
        ({"STEWARDSHIP_USERS_OPERATION": "not-a-uuid"}, "lowercase UUID"),
        (
            {"STEWARDSHIP_USERS_ADMINISTRATORS": "a@example.test, b@example.test"},
            "comma-separated addresses",
        ),
        ({"STEWARDSHIP_USERS_ADMINISTRATORS": "nobody"}, "comma-separated addresses"),
    ],
)
def test_users_upgrade_inputs_are_refused_before_anything_stops(
    tmp_path, change, message
):
    """Partial or malformed inputs end the run while the site is still up."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop="f", status=1, schema_change="1", env=USERS | change
    )
    assert message in output
    assert "Failed before anything stopped; nothing changed" in output
    assert not any(" stop " in call for call in calls)


def test_users_upgrade_needs_the_override_and_never_rolls_back(tmp_path):
    """The conversion migrates, so it needs the override; a rollback refuses it."""
    calls, output, _ = run_host(tmp_path, "upgrade", noop="f", status=1, env=USERS)
    assert "needs the schema-change override as well" in output
    assert not any(" stop " in call for call in calls)
    calls, output, _ = run_host(
        tmp_path / "rollback", "rollback", noop="f", status=1, env=USERS
    )
    assert "A rollback never converts" in output
    assert not any(" stop " in call for call in calls)


def test_a_refused_conversion_stops_before_the_swap_with_web_down(tmp_path):
    """No full migration, no web; the message names recovery and the repeat."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        users_fail=True,
        env=USERS,
    )
    assert "The users upgrade did not complete" in output
    assert "Admin-access recovery works" in output
    assert "caddy serves the maintenance page" in output
    assert not any(call.endswith("--rm -T migration") for call in calls)
    assert not any("up --detach --wait web" in call for call in calls)


def test_a_release_needing_the_conversion_without_it_stops_at_its_swap(tmp_path):
    """Without the inputs the swap's refusal ends the run and says what to do."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        migrate_fail=True,
    )
    assert "repeat the run with the" in output and "STEWARDSHIP_USERS_*" in output
    assert "caddy serves the maintenance page" in output
    assert not any("upgrade-users" in call for call in calls)
    assert not any("database-grants" in call for call in calls)
    assert not any("up --detach --wait web" in call for call in calls)


def test_the_laptop_half_passes_the_users_inputs_only_when_set(tmp_path):
    """An env prefix, %q-quoted, before the host half; refused for a rollback."""
    result, calls, _ = run_local(tmp_path, env=USERS)
    assert result.returncode == 0, result.stderr
    assert (
        'cat > "$f" && env '
        "STEWARDSHIP_USERS_OPERATION=11111111-2222-4333-8444-555555555555 "
        "STEWARDSHIP_USERS_OPERATOR=Test\\ Operator "
        "STEWARDSHIP_USERS_REASON=Users\\ revamp\\ release "
        "STEWARDSHIP_USERS_ADMINISTRATORS=first@example.test\\,second@example.test "
        'bash "$f" '
    ) in calls[0]
    result, calls, _ = run_local(tmp_path / "rollback", "--rollback", env=USERS)
    assert result.returncode == 1 and calls == []
    assert "A rollback never converts" in result.stderr


# Each way a users-release run can stop in step 4 with the base migration
# applied (only the users swap pending): the first run's FAKE_* switches,
# environment and exit status (a killed command's own, under `set -e`), and
# what its output says.
STOPPED_IN_STEP_4 = {
    "without the users inputs": (
        dict(migrate_fail=True),
        {},
        1,
        "repeat the run with the",
    ),
    "a refused conversion": (
        dict(users_fail=True),
        USERS,
        1,
        "The users upgrade did not complete",
    ),
    "a failed final migration": (
        dict(migrate_fail=True),
        USERS,
        1,
        "Migration 0052 refused",
    ),
    "an interruption after the base migration": (
        dict(timeout=" upgrade-users "),
        USERS,
        124,
        "TIMEOUT: the users upgrade preview killed after",
    ),
}


def stopped_in_step_4(tmp_path, case):
    """Run the first, failing upgrade; return its root with the swap pending."""
    fake, env, status, message = STOPPED_IN_STEP_4[case]
    calls, output, root = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=status,
        schema_change="1",
        env=env,
        **fake,
    )
    assert message in output
    assert "caddy serves the maintenance page" in output
    assert (tmp_path / "state" / "swap_pending").exists()
    assert not any(" up --detach " in call for call in calls)
    return root


@pytest.mark.parametrize("case", sorted(STOPPED_IN_STEP_4))
def test_a_repeated_run_finishes_the_users_upgrade_after_step_4_stopped(tmp_path, case):
    """The repeat backs up, converts and migrates, and only then starts anything.

    Web is already down when it begins, so it says so; its step-1 backup
    runs on the swap-pending schema (the backup command admits it); and no
    service is started until the full migration cleared the pending swap.
    """
    stopped_in_step_4(tmp_path, case)
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop="f", schema_change="1", env=USERS, again=True
    )
    assert "web is not running: repeating a run" in output
    assert "(same digest: re-running the upgrade)" in output
    backup = first(calls, "backup-worker")
    convert = first(calls, "--operation-id ")
    full = next(i for i, call in enumerate(calls) if call.endswith("--rm -T migration"))
    assert backup < first(calls, " retarget-image ") < convert < full
    assert full < first(calls, " up --detach ")
    assert not (tmp_path / "state" / "refused").exists()
    assert f"Upgraded to {TARGET} in" in output


@pytest.mark.parametrize(
    ("fake", "phase"),
    [
        (dict(backup_fail=True), "backup"),
        (dict(stop_fail=True), "stop web"),
        (dict(retarget_fail=True), "retarget"),
    ],
)
def test_a_repeated_run_that_fails_before_the_retarget_starts_nothing(
    tmp_path, fake, phase
):
    """Web was down when the repeat began, so no failure starts a service.

    Before this rule, a failed stop started every service and a failed
    backup restarted the background ones, into a schema they refuse.
    """
    stopped_in_step_4(tmp_path, "a refused conversion")
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        env=USERS,
        again=True,
        **fake,
    )
    assert not any(" up --detach " in call for call in calls), phase
    assert not (tmp_path / "state" / "refused").exists(), phase
    assert "caddy serves the maintenance page" in output, phase
    assert "restarting the background services" not in output, phase
    assert "starting everything again" not in output, phase


def test_a_normal_run_still_restarts_what_it_stopped(tmp_path):
    """With web up when the run began, an abandoned backup restarts the rest."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, backup_fail=True)
    assert "web is not running" not in output
    assert "restarting the background services" in output
    assert any(" up --detach --wait worker" in call for call in calls)


@pytest.mark.parametrize(
    ("fragment", "what", "limit", "status"),
    [
        (
            "--users-upgrade-base",
            "the migration to the users upgrade's base",
            1800,
            124,
        ),
        ("database-grants", "database-grants", 600, 124),
        (" upgrade-users ", "the users upgrade preview", 600, 124),
        ("--operation-id", "the users upgrade", 900, 1),
    ],
)
def test_each_step_4_command_runs_under_a_logged_time_limit(
    tmp_path, fragment, what, limit, status
):
    """A killed step-4 command is logged with its limit; the page stays up."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=status,
        schema_change="1",
        env=USERS,
        timeout=fragment,
    )
    killed = next(call for call in calls if call.startswith("timeout "))
    assert killed.startswith(f"timeout --kill-after=30 {limit} docker compose ")
    assert f"TIMEOUT: {what} killed after " in output
    assert f"(limit {limit}s)" in output
    # The one-off container itself is killed and removed by its name.
    name = one_off_name(killed)
    assert calls.index(f"kill {name}") < calls.index(f"rm --force {name}")
    assert f"one-off container {name} killed and removed (time limit)" in output
    assert "caddy serves the maintenance page" in output
    assert not any(" up --detach " in call for call in calls)
    # The kill is in the run's persistent log as well.
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert f"TIMEOUT: {what} killed after " in log


def test_a_failed_step_4_command_prints_and_logs_its_full_output(tmp_path):
    """Every output line of a failed command is shown, not just its last.

    The fake grants end their output with an empty line, as a traceback
    does, so the last line alone would hide the cause.
    """
    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        env=USERS,
        grants_fail=True,
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    for text in (output, log):
        assert "FAILED: database-grants exited with status 1; its full output:" in text
        assert "    | grants refused\n    | UndefinedFunction: no such" in text
        assert "caddy serves the maintenance page" in text
    # A command that succeeds still prints only its last line.
    assert "    | migrated to the base" not in output
    assert "migrated to the base" in output


def test_a_failed_migration_withholds_database_values_from_the_log(tmp_path):
    """A traceback's frames, message and DETAIL never reach the output or log.

    Each traceback is reduced to its exception's class; a DETAIL line
    outside one is withheld too. The lines around them are kept.
    """
    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        migrate_fail=True,
        env={"FAKE_MIGRATE_TRACEBACK": "1"},
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    for text in (output, log):
        assert "secret" not in text and "duplicate key" not in text
        assert (
            "    | Migration 0052 refused\n"
            "    | Traceback withheld (it may carry database values)\n"
            "    | Exception class: psycopg.errors.UniqueViolation\n"
            "    | The above exception was the direct cause"
            " of the following exception:\n"
            "    | \n"
            "    | Traceback withheld (it may carry database values)\n"
            "    | Exception class: django.db.utils.IntegrityError\n"
            "    | ERROR: IntegrityError (SQLSTATE 23505)\n"
            "    | DETAIL withheld\n"
        ) in text


def test_the_final_migration_runs_under_a_logged_time_limit(tmp_path):
    """The full migration of every schema-change run is limited the same way."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        timeout="-T migration",
    )
    assert any(call.startswith("timeout --kill-after=30 1800 ") for call in calls)
    assert "TIMEOUT: the migration killed after " in output
    assert "caddy serves the maintenance page" in output


def one_off_name(call):
    """The --name a recorded `docker compose run` call gave its container."""
    return re.search(r" run --name (\S+) --rm -T ", call).group(1)


def test_each_step_4_one_off_container_has_its_own_name(tmp_path):
    """Named after the project, mode, run and step; no two alike."""
    calls, _, _ = run_host(tmp_path, "upgrade", noop="f", schema_change="1", env=USERS)
    names = [one_off_name(call) for call in calls if " run --name " in call]
    # The sixth and seventh: the grants, then the report email conversion
    # (#1786).
    assert len(names) == 7 == len(set(names))
    assert names[-1].endswith("-reports-upgrade")
    assert all(
        re.fullmatch(r"stewardship-upgrade-\d{8}T\d{6}Z-\d+-[a-z-]+", n) for n in names
    )
    assert [name.rsplit("-", 1)[-1] for name in names[:1]] == ["migration"]


@pytest.mark.parametrize(
    ("signum", "name"), [(signal.SIGTERM, "TERM"), (signal.SIGINT, "INT")]
)
def test_a_signal_kills_and_removes_the_running_one_off_container(
    tmp_path, signum, name
):
    """Ctrl-C or kill during a step-4 command reaches its container.

    The client is waited for in the background, so the trap runs at once;
    it kills and removes the named container, logs both, and the run ends
    with the maintenance page up.
    """
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=128 + signum,
        schema_change="1",
        env=USERS,
        hang="--users-upgrade-base",
        interrupt=("--users-upgrade-base", signum),
    )
    hung = next(call for call in calls if "--users-upgrade-base" in call)
    container = one_off_name(hung)
    assert calls.index(f"kill {container}") < calls.index(f"rm --force {container}")
    assert not any(" up --detach " in call for call in calls)
    assert not any("database-grants" in call for call in calls)
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert f"INTERRUPTED: SIG{name} received in phase offline" in log
    assert f"one-off container {container} killed and removed (SIG{name})" in log
    assert "caddy serves the maintenance page" in log


@pytest.mark.parametrize(
    ("fake", "reused"),
    [(dict(base_fail=True), True), (dict(grants_fail=True), False)],
    ids=["base migration stopped partway", "grants failed after it"],
)
def test_a_repeat_finishes_after_the_base_migration_or_its_grants_failed(
    tmp_path, fake, reused
):
    """Both failure points of the base step are resumed by repeating the run.

    A base migration stopped partway leaves migrations outside the swap
    pending, which a plain backup refuses: the repeat (same digest, web
    down, the users inputs) asks the backup to resume, which reports the
    first run's backup as the restore point; the run says so, records the
    line in stewardship-backup.log, and converts and migrates. After a
    grants failure only the swap is pending, so the same request takes a
    fresh backup. Repeating without the users inputs refuses at the backup
    and says to pass them.
    """
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        env=USERS,
        **fake,
    )
    assert "caddy serves the maintenance page" in output
    assert not any("upgrade-users" in call for call in calls)
    if reused:
        calls, output, _ = run_host(
            tmp_path, "upgrade", noop="f", status=1, schema_change="1", again=True
        )
        assert any(call.endswith(" run --rm -T backup-worker") for call in calls)
        assert "repeat it with the" in output and "STEWARDSHIP_USERS_*" in output
        assert not any(" up --detach " in call for call in calls)
    calls, output, _ = run_host(
        tmp_path, "upgrade", noop="f", schema_change="1", env=USERS, again=True
    )
    backup = first(calls, "backup-worker")
    assert calls[backup].endswith(
        f"run --rm -T backup-worker backup --config "
        f"{tmp_path}/root/config/services/backup-worker.yaml --users-upgrade-resume"
    )
    assert ("REUSED BACKUP" in output) is reused
    recorded = (tmp_path / "logs" / "stewardship-backup.log").read_text()
    assert ('"backup_reused": true' in recorded) is reused
    if reused:
        assert "2026-10-10T04:00:00+00:00 (manifest " + "e" * 64 in output
    base = first(calls, "--users-upgrade-base")
    convert = first(calls, "--operation-id ")
    full = next(i for i, call in enumerate(calls) if call.endswith("--rm -T migration"))
    assert backup < base < convert < full < first(calls, " up --detach ")
    assert f"Upgraded to {TARGET} in" in output


def test_a_first_run_never_asks_the_backup_to_resume(tmp_path):
    """The resume option is only for a repeat whose documents name this image."""
    calls, _, _ = run_host(tmp_path, "upgrade", noop="f", schema_change="1", env=USERS)
    assert any(call.endswith(" run --rm -T backup-worker") for call in calls)
    assert not any("--users-upgrade-resume" in call for call in calls)


# The database network layout (#2111): the release that adds PostgreSQL's
# source rules renders fixed backend addresses, a dynamic ip_range and a
# one-shot network per profile; the release before it renders none of them.
def _network(key, subnet, gateway, ip_range=None):
    """One network as `docker network inspect` reports it (Compose's labels)."""
    config = {"Subnet": subnet, "Gateway": gateway}
    if ip_range:
        config["IPRange"] = ip_range
    return {
        "Id": f"net-{key}",
        "Name": f"stewardship_{key}",
        "Internal": True,
        "Labels": {"com.docker.compose.network": key},
        "IPAM": {"Config": [config]},
    }


def _rendered(new):
    """The Compose file's database networks, as either release renders them."""
    backend = {"subnet": "172.29.240.0/24"}
    networks = {
        "backend": {"internal": True, "ipam": {"config": [backend]}},
        "proxy": {
            "internal": True,
            "ipam": {"config": [{"subnet": "172.29.241.0/24"}]},
        },
    }
    postgres = {"backend": {}}
    if new:
        backend.update(gateway="172.29.240.1", ip_range="172.29.240.128/25")
        networks["offline-migration"] = {
            "internal": True,
            "ipam": {
                "config": [
                    {
                        "subnet": "172.29.242.16/28",
                        "gateway": "172.29.242.17",
                        "ip_range": "172.29.242.24/29",
                    }
                ]
            },
        }
        postgres = {
            "backend": {"ipv4_address": "172.29.240.2"},
            "offline-migration": {"ipv4_address": "172.29.242.18"},
        }
    return {
        "services": {"web": {}, "postgres": {"networks": postgres}},
        "networks": networks,
    }


LAYOUT = {
    "networks_old": json.dumps(
        [
            _network("backend", "172.29.240.0/24", "172.29.240.1"),
            _network("proxy", "172.29.241.0/24", "172.29.241.1"),
        ]
    ),
    "networks_new": json.dumps(
        [
            _network("backend", "172.29.240.0/24", "172.29.240.1", "172.29.240.128/25"),
            _network(
                "offline-migration",
                "172.29.242.16/28",
                "172.29.242.17",
                "172.29.242.24/29",
            ),
            _network("proxy", "172.29.241.0/24", "172.29.241.1"),
        ]
    ),
    "postgres_old": json.dumps({"stewardship_backend": {}}),
    "postgres_new": json.dumps(
        {"stewardship_backend": {}, "stewardship_offline-migration": {}}
    ),
    "compose_old": json.dumps(_rendered(False)),
    "compose_new": json.dumps(_rendered(True)),
}


@pytest.mark.parametrize(
    ("mode", "running", "rendered"),
    [("upgrade", "old", "new"), ("rollback", "new", "old")],
    ids=["rollout", "rollback to a release before the rules"],
)
def test_a_database_network_change_fully_stops_after_the_retarget(
    tmp_path, mode, running, rendered
):
    """Both directions remove everything but caddy and restart the stores first.

    After the retarget (and before step 4 needs PostgreSQL), postgres and
    valkey stop through Compose, every container but caddy is removed, every
    network caddy is not on is removed, and postgres and valkey start again
    on the rendered networks, which then match. caddy is never stopped.
    """
    calls, output, _ = run_host(
        tmp_path,
        mode,
        layout_running=running,
        render_layout=rendered,
        **LAYOUT,
    )
    retarget = first(calls, " retarget-image ")
    stop = first(calls, " stop postgres valkey")
    remove = first(calls, "rm id-")
    start = first(calls, " up --detach --wait postgres valkey")
    assert retarget < stop < remove < start < first(calls, " up --detach --wait web")
    assert calls[remove] == "rm id-postgres id-valkey id-web"
    removed = [c for c in calls if c.startswith("network rm ")]
    assert "network rm net-proxy" not in removed and "network rm net-backend" in removed
    assert not any(" stop " in c and "caddy" in c for c in calls)
    if mode == "upgrade":
        # Step 4's decisive check runs on the recreated PostgreSQL.
        checks = [
            i for i, c in enumerate(calls) if "PGOPTIONS" in c and "0041" not in c
        ]
        assert start < checks[1]
    assert "3. Full stop: the database networks change" in output
    assert "network backend differs from the rendered one (ip_range)" in output
    assert "postgres and valkey run on the rendered networks" in output
    done = "Upgraded" if mode == "upgrade" else "Rolled back"
    assert f"{done} to {TARGET} in" in output


def test_an_unchanged_database_layout_needs_no_full_stop(tmp_path):
    """A later upgrade finds the rendered networks running and stops nothing more."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", layout_running="new", render_layout="new", **LAYOUT
    )
    assert not any(" stop postgres valkey" in c for c in calls)
    assert not any(c.startswith(("rm ", "network rm ")) for c in calls)
    assert "Full stop" not in output
    assert f"Upgraded to {TARGET} in" in output


def test_a_running_one_off_refuses_the_full_stop(tmp_path):
    """A cron backup is never killed mid-copy: nothing stops, the page stays up."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        layout_running="old",
        render_layout="new",
        oneoff="1",
        **LAYOUT,
    )
    assert (
        "One-off containers of stewardship are still running (backup-worker)" in output
    )
    assert "The full stop did not finish; caddy serves the maintenance page" in output
    assert "Failed with web stopped: caddy serves the maintenance page." in output
    assert not any(" stop postgres valkey" in c or c.startswith("rm ") for c in calls)
    assert not any(" up --detach " in c for c in calls)
    # Repeated once the backup finished, the full stop runs.
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        again=True,
        layout_running="old",
        render_layout="new",
        **LAYOUT,
    )
    assert any(" up --detach --wait postgres valkey" in c for c in calls)
    assert f"Upgraded to {TARGET} in" in output


def test_networks_that_still_differ_after_the_full_stop_stop_the_run(tmp_path):
    """The comparison after the restart must find nothing; else step 4 never runs."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        layout_running="old",
        render_layout="new",
        layout_stuck="1",
        **LAYOUT,
    )
    assert "After the full stop the database networks still differ" in output
    assert "caddy serves the maintenance page" in output
    assert not any(" up --detach --wait web" in c for c in calls)


def test_an_expired_restart_of_the_stores_is_logged(tmp_path):
    """A hung `up postgres valkey` is killed, logged with its limit; the run stops."""
    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        layout_running="old",
        render_layout="new",
        timeout="up --detach --wait postgres valkey",
        **LAYOUT,
    )
    assert re.search(
        r"TIMEOUT: starting postgres and valkey killed after \d+s \(limit 600s\)",
        output,
    )
    kept = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: starting postgres and valkey" in kept


@pytest.mark.parametrize(
    ("mode", "running", "rendered"),
    [("upgrade", "old", "new"), ("rollback", "new", "old")],
    ids=["rollout", "rollback"],
)
def test_a_repeat_finishes_a_failed_full_stop_before_step_one(
    tmp_path, mode, running, rendered
):
    """A full stop that failed after postgres stopped is finished first.

    Step 1's check and backup need PostgreSQL. The repeat finds web down
    and postgres gone, finishes the full stop under the file the earlier
    run retargeted, and only then checks: a rollback is never sent to a
    database restore it does not need.
    """
    common = dict(layout_running=running, render_layout=rendered, **LAYOUT)
    _, output, _ = run_host(
        tmp_path,
        mode,
        status=1,
        timeout="up --detach --wait postgres valkey",
        **common,
    )
    assert "The full stop did not finish" in output
    calls, output, _ = run_host(tmp_path, mode, again=True, **common)
    finish = first(calls, " up --detach --wait postgres valkey")
    assert finish < first(calls, "PGOPTIONS") < first(calls, " retarget-image ")
    assert "Finishing the full stop an earlier run began" in output
    assert "postgres is not running" in output
    assert "database-restore" not in output
    done = "Upgraded" if mode == "upgrade" else "Rolled back"
    assert f"{done} to {TARGET} in" in output


def test_caddy_keeps_its_networks_in_the_full_stop(tmp_path):
    """Only the networks caddy is not on are removed; caddy itself stays."""
    calls, _, _ = run_host(
        tmp_path, "upgrade", layout_running="old", render_layout="new", **LAYOUT
    )
    assert "inspect --format" in next(c for c in calls if c.endswith(" id-caddy"))
    removed = [c for c in calls if c.startswith("network rm ")]
    assert removed == ["network rm net-backend"]


def test_the_source_rules_are_reloaded_and_checked_before_step_4(tmp_path):
    """After the retarget: render, reload, wait for the load, then migrate (#2111)."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        hba_rules=1,
        hba="hba_reload_pending hba_reload_pending t",
    )
    retarget = first(calls, " retarget-image ")
    render = first(calls, " hba-check --config /run/operator.yaml")
    reload = first(calls, "SELECT pg_reload_conf()")
    polls = [i for i, call in enumerate(calls) if " -v since=" in call]
    decisive = [
        i
        for i, call in enumerate(calls)
        if "PGOPTIONS" in call and "0041" not in call and "since=" not in call
    ][1]
    # One check before the reload (the file must already parse), then polls.
    assert retarget < render < polls[0] < reload < polls[1] < decisive
    assert len(polls) == 4
    # The file alone first; then the load must follow PostgreSQL's own
    # clock_timestamp() from just before the signal.
    assert "-v since=infinity" in calls[polls[0]]
    assert "SELECT clock_timestamp()" in calls[reload]
    for poll in polls[1:]:
        assert "-v since=2026-10-25 04:00:00.000001+00" in calls[poll]
    # The reload is sent as the operator over the socket; the check read-only.
    assert "exec -T postgres psql -U pk_stewardship_operator" in calls[reload]
    assert "default_transaction_read_only=on" in calls[polls[0]]
    assert "--network none" in calls[render]
    assert "the rendered source rules are in force (the reload took " in output
    # Step 6: every running application service by exec, each one-shot by
    # run, after the services start; caddy and PostgreSQL never.
    start_rest = first(calls, "up --detach --wait worker scheduler mail-dispatch")
    web = first(calls, "exec -T web pk-stewardship database-sources --config ")
    worker = first(calls, "exec -T worker pk-stewardship database-sources --config ")
    migration = first(calls, "-T migration database-sources --config ")
    assert start_rest < min(web, worker, migration)
    assert " run --name " in calls[migration]
    sources = [call for call in calls if "database-sources" in call]
    assert len(sources) == 3
    assert output.count("database sources match the rendered rules") == 3
    assert f"Upgraded to {TARGET} in" in output


def test_no_rendered_rules_means_nothing_to_reload_or_probe(tmp_path):
    """A release before #2111 (a rollback to it, say) renders no hba_file."""
    calls, output, _ = run_host(tmp_path, "upgrade")
    assert "PostgreSQL source rules: none rendered for" in output
    for fragment in ("hba-check", "pg_reload_conf", "database-sources"):
        assert not any(fragment in call for call in calls), fragment


def test_a_reload_that_never_takes_effect_stops_before_step_4(tmp_path):
    """The bounded wait logs what timed out, its limit and the time waited."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, hba="hba_reload_pending"
    )
    assert "TIMEOUT: the wait for the hba reload stopped after " in output
    assert "(limit 30s)" in output
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: the wait for the hba reload stopped after " in log
    assert len([call for call in calls if " -v since=" in call]) == 1 + 30
    assert "caddy serves the maintenance page" in output
    assert not any(" up --detach --wait web" in call for call in calls)
    assert not any("Report email conversion" in line for line in output.splitlines())


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("hba_file_not_mounted", "PostgreSQL does not use the mounted rules"),
        ("hba_file_differs", "the mounted pg_hba.conf differs from the rendered one"),
        ("hba_file_invalid", "pg_hba.conf has errors"),
        ("ERROR:", "the hba check did not answer: ERROR:"),
    ],
)
def test_a_failed_hba_condition_stops_with_the_page_up(tmp_path, answer, why):
    """Each condition is named; the maintenance page stays up; step 4 never runs.

    Found before the reload, nothing is reloaded; found after it (the
    pending answer first), the run stops all the same.
    """
    for reloaded in (False, True):
        answers = {"hba": answer} if reloaded else {"hba_file": answer}
        calls, output, _ = run_host(
            tmp_path / str(reloaded), "upgrade", status=1, hba_rules=1, **answers
        )
        assert f"The hba check failed: {why}" in output
        assert "caddy serves the maintenance page" in output
        assert any("pg_reload_conf" in call for call in calls) is reloaded
        assert not any(" up --detach --wait web" in call for call in calls)


def test_an_unrenderable_hba_check_stops_before_reloading(tmp_path):
    """The target image must render the check; nothing is reloaded without it."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, hba_render_fail=1
    )
    assert "Could not render the hba check in" in output
    assert not any("pg_reload_conf" in call for call in calls)


def test_a_source_difference_fails_the_run_with_the_site_up(tmp_path):
    """The login, source and service are named; web keeps serving (decision 7)."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, sources_fail="worker"
    )
    assert (
        "MISMATCH: pk_stewardship_operator is allowed from 172.29.240.13 (worker)"
    ) in output
    assert "worker: database sources differ from the rendered rules" in output
    assert f"Upgraded to {TARGET}, but NOT healthy:" in output
    assert f"Failed after web started healthy: {TARGET} is serving" in output
    assert "caddy serves the maintenance page" not in output
    # The other services were still checked, and web was started.
    assert any("exec -T web pk-stewardship database-sources" in c for c in calls)
    assert any("-T migration database-sources" in c for c in calls)
    assert "web was down for" in output


def test_a_rendering_that_hangs_is_logged_and_stops_before_reloading(tmp_path):
    """Rendering the hba check runs under a time limit; its kill is logged."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, timeout="hba-check"
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: rendering the hba check killed after " in log
    assert "(limit 300s)" in log
    assert "Could not render the hba check in" in output
    assert not any("pg_reload_conf" in call for call in calls)


def test_a_reload_that_hangs_is_logged_and_stops_before_step_4(tmp_path):
    """The reload runs under a time limit like every other step."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, timeout="pg_reload_conf"
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: the PostgreSQL configuration reload killed after " in log
    assert "(limit 30s)" in log
    assert "Could not reload PostgreSQL's configuration" in output
    assert "caddy serves the maintenance page" in output
    assert not any(" up --detach --wait web" in call for call in calls)


@pytest.mark.parametrize(
    ("fragment", "service", "limit"),
    [
        ("exec -T worker pk-stewardship database-sources", "worker", 60),
        ("-T migration database-sources", "migration", 120),
    ],
)
def test_a_source_check_that_hangs_is_logged_and_named(
    tmp_path, fragment, service, limit
):
    """A killed check is logged and named as unfinished, never as a difference."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, hba_rules=1, timeout=fragment
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert f"TIMEOUT: the source check from {service} killed after " in log
    assert f"(limit {limit}s). Web keeps serving." in log
    assert f"{service}: the source check did not finish within {limit}s" in output
    assert f"{service}: database sources differ" not in output
    assert "Failed after web started healthy" in output
    # The rest of the list was still checked.
    assert len([call for call in calls if "database-sources --config" in call]) == 3


def test_a_rollback_to_a_release_with_rules_reloads_and_checks_them(tmp_path):
    """A rollback whose target renders hba_file goes through the same checks."""
    calls, output, _ = run_host(tmp_path, "rollback", hba_rules=1)
    retarget = first(calls, " retarget-image ")
    assert retarget < first(calls, "SELECT pg_reload_conf()")
    assert "the rendered source rules are in force" in output
    assert len([call for call in calls if "database-sources --config" in call]) == 3
    assert f"Rolled back to {TARGET} in" in output


def test_fake_clock_local_mode_checks_the_rules_under_both_compose_files(tmp_path):
    """The reload, the check and every probe run under the faketime override."""
    calls, output, root = run_host(
        tmp_path, "upgrade", profile="local", clock="fake", hba_rules=1
    )
    override = str(root / "config" / "services" / "compose.faketime.json")
    for fragment in ("SELECT pg_reload_conf()", " -v since=", "database-sources"):
        matched = [call for call in calls if fragment in call]
        assert matched and all(override in call for call in matched), fragment
    assert "the rendered source rules are in force" in output


def test_a_service_that_is_not_running_is_not_probed(tmp_path):
    """Only running online services are probed by exec; one-shots always run."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        hba_rules=1,
        services_after="postgres valkey web scheduler mail-dispatch caddy",
    )
    assert not any("exec -T worker pk-stewardship database-sources" in c for c in calls)
    assert any("exec -T web pk-stewardship database-sources" in c for c in calls)
    assert any("-T migration database-sources" in c for c in calls)
    assert f"Upgraded to {TARGET} in" in output


# ---------------------------------------------------------------------------
# The off-site uploader's preflights (#235): the command-list probe of both
# images, the read-only password check and the subnet-overlap check, all
# before step 1 stops anything.

UPLOADER_HELP = (
    "usage: pk-stewardship {config-check,retarget-image,smoke,uploader-password-check}"
)


def before_the_stop(calls, fragment):
    """The fragment's first call comes before any service is stopped."""
    stop = next((i for i, call in enumerate(calls) if " stop " in call), len(calls))
    return first(calls, fragment) < stop


def test_both_images_are_probed_and_no_marker_means_no_uploader_checks(tmp_path):
    """Without uploader-password-check the image has no uploader: nothing runs.

    Every image lists smoke, so smoke is not the marker.
    """
    calls, output, _ = run_host(tmp_path, "upgrade")
    helps = [call for call in calls if call.endswith(" --help")]
    assert [call.split()[-2] for call in helps] == [TARGET, CURRENT]
    assert all("--network none" in call for call in helps)
    assert not any(
        "pg_authid" in call or "uploader-password-check" in call for call in calls
    )
    assert "off-site uploader" not in output


@pytest.mark.parametrize("which", ["target", "running"])
def test_an_image_that_does_not_list_its_commands_refuses_first(tmp_path, which):
    """A probe failure stops the run before anything stops."""
    image = TARGET if which == "target" else CURRENT
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, help_fail=image)
    assert "Could not list the commands of" in output
    assert not any(" stop " in call for call in calls)


def test_a_command_list_without_retarget_image_is_not_an_answer(tmp_path):
    """Output that is not a command list refuses rather than reading as none."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, help="Error: nope")
    assert "Could not list the commands of" in output
    assert not any(" stop " in call for call in calls)


def test_no_uploader_login_yet_needs_no_file_check(tmp_path):
    """The expand upgrade: the count is 0, step 4 creates the login."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", help=UPLOADER_HELP, uploader_logins=0, routes=""
    )
    assert "no uploader login yet" in output
    assert before_the_stop(calls, "count(*) FROM pg_authid")
    assert "default_transaction_read_only=on" in calls[first(calls, "pg_authid")]
    assert not any("uploader-password-check" in call for call in calls)


def test_an_existing_login_needs_its_file_to_match(tmp_path):
    """A row: the new image's check answers match, in its isolated container."""
    calls, output, root = run_host(
        tmp_path, "upgrade", help=UPLOADER_HELP, uploader_logins=1, routes=""
    )
    assert "the uploader's password file matches its login" in output
    check = calls[first(calls, "uploader-password-check")]
    assert check.startswith("run -i --rm --init --network none")
    assert f"source={root},target={root},readonly" in check
    assert "target=/run/operator.yaml,readonly" in check
    assert check.endswith(
        f"{TARGET} uploader-password-check --config /run/operator.yaml"
    )
    assert before_the_stop(calls, "uploader-password-check")
    # The verifier reached the check on stdin, and never the log.
    assert (
        (tmp_path / "state" / "verifier_seen").read_text().startswith("SCRAM-SHA-256$")
    )
    assert "SCRAM-SHA-256$" not in output
    assert not any(
        "SCRAM-SHA-256$" in path.read_text() for path in (tmp_path / "logs").iterdir()
    )


@pytest.mark.parametrize(
    "fake",
    [
        {"uploader_answer": "mismatch"},
        {"uploader_check_fail": "1"},
        {"uploader_answer": "matched"},
    ],
)
def test_a_missing_or_mismatched_file_refuses_before_anything_stops(tmp_path, fake):
    """Any answer but match names the lost-file recovery; nothing stops."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        help=UPLOADER_HELP,
        uploader_logins=1,
        routes="",
        **fake,
    )
    assert "uploader-lost-file-recovery" in output
    assert "Nothing was" in output
    assert not any(" stop " in call for call in calls)


def test_a_killed_password_check_is_logged_removed_and_refuses(tmp_path):
    """The 120 s limit: the kill is logged, the container removed by name."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        help=UPLOADER_HELP,
        uploader_logins=1,
        routes="",
        timeout=" uploader-password-check ",
    )
    killed = next(call for call in calls if call.startswith("timeout "))
    assert killed.startswith("timeout --kill-after=10 120 docker run ")
    name = killed.split("--name ", 1)[1].split()[0]
    assert name.endswith("-uploader-password-check")
    assert calls.index(f"kill {name}") < calls.index(f"rm --force {name}")
    assert "TIMEOUT: the off-site uploader's password check killed after " in output
    assert "(limit 120s)" in output
    assert f"one-off container {name} killed and removed (time limit)" in output
    assert "answer: none" in output
    assert not any(" stop " in call for call in calls)
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: the off-site uploader's password check killed after " in log


@pytest.mark.parametrize(
    "fake, message",
    [
        ({"uploader_query_fail": "1"}, "Could not ask the database"),
        ({"uploader_logins": "t"}, "Unexpected answer"),
        ({"uploader_logins": ""}, "Unexpected answer"),
        ({"uploader_logins": "2"}, "Unexpected answer"),
    ],
)
def test_a_failed_login_query_is_never_read_as_no_login(tmp_path, fake, message):
    """The query's own failure, or an answer other than 0 or 1, refuses."""
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, help=UPLOADER_HELP, routes="", **fake
    )
    assert message in output
    assert not any(" stop " in call for call in calls)
    assert not any("uploader-password-check" in call for call in calls)


def test_only_the_new_image_decides_the_password_check(tmp_path):
    """A running image with the uploader and a new one without: no check."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        help_running=UPLOADER_HELP,
        running_image=CURRENT,
    )
    assert "the running image has the off-site uploader" in output
    assert not any("pg_authid" in call for call in calls)


def test_the_uploaders_subnet_is_checked_against_networks_and_routes(tmp_path):
    """Slice 5 of the default offline block, before anything stops."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        help=UPLOADER_HELP,
        subnets="bridge 172.17.0.0/16\nstewardship_offline-bootstrap 172.29.242.0/28",
        routes="default via 10.0.0.1 dev eth0\n172.17.0.0/16 dev docker0 scope link",
    )
    assert "172.29.242.80/28 overlaps no Docker network or host route" in output
    assert before_the_stop(calls, "network ls --quiet --no-trunc")
    assert before_the_stop(calls, "ip -4 route show")


@pytest.mark.parametrize(
    "fake, named",
    [
        ({"subnets": "vpn 172.29.242.64/26", "routes": ""}, "Docker network vpn"),
        ({"subnets": "", "routes": "172.16.0.0/12 via 10.0.0.1"}, "host route 172.16"),
        ({"subnets": "", "routes": "172.29.242.90 via 10.0.0.1"}, "host route 172.29"),
        (
            {"subnets": "", "routes": "blackhole 172.29.242.80/28"},
            "host route 172.29.242.80/28",
        ),
    ],
)
def test_an_overlapping_network_or_route_refuses_before_anything_stops(
    tmp_path, fake, named
):
    """It names what overlaps; the remedy is to move that, not the setting.

    retarget-image refuses any change to runtime_network.offline on an
    existing deployment, so the message must not send the operator there.
    """
    calls, output, _ = run_host(
        tmp_path, "upgrade", status=1, help=UPLOADER_HELP, **fake
    )
    assert named in output
    assert "Move or remove the" in output
    assert "conflicting network or route" in output
    assert "Do not change" in output
    assert "Choose another" not in output
    assert "Nothing was stopped or changed" in output
    assert not any(" stop " in call for call in calls)


def test_a_chosen_offline_block_moves_the_uploaders_slice(tmp_path):
    """The /28 follows runtime_network.offline from the rendered documents."""

    def prepare(root):
        """Render a non-default offline block, as the record would carry it."""
        web = root / "config" / "services" / "web.yaml"
        document = json.loads(web.read_text())
        document["deployment"]["runtime_network"] = {"offline": "10.44.7.0/25"}
        web.write_text(json.dumps(document))

    _, output, _ = run_host(
        tmp_path,
        "upgrade",
        status=1,
        prepare=prepare,
        help=UPLOADER_HELP,
        subnets="",
        routes="10.44.7.80/28 dev eth9",
    )
    assert "10.44.7.80/28 (slice 5 of runtime_network.offline" in output


def test_the_uploaders_existing_network_needs_no_overlap_check(tmp_path):
    """Its own bridge route would overlap itself; Docker already made it."""
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        help=UPLOADER_HELP,
        uploader_network="172.29.242.80/28",
        routes="172.29.242.80/28 dev br-1234 scope link",
    )
    assert "the uploader's network 172.29.242.80/28 already exists" in output
    assert not any(call.startswith("ip ") for call in calls)


# ---------------------------------------------------------------------------
# The off-site uploader's copy (#235, E2): step 1 keyed on the running
# image's `upload` command and the running topology's service, the host's
# copy lock for the offline work, and the expand rollback's cron preflight.

COPY_HELP = (
    "usage: pk-stewardship {config-check,retarget-image,smoke,upload,"
    "uploader-password-check}"
)
STEP_1_DIGEST = "f" * 64


def split_topology(root):
    """The running Compose file has the backup-uploader service (expand)."""
    compose = root / "config" / "services" / "compose.json"
    document = json.loads(compose.read_text())
    document["services"]["backup-uploader"] = {
        "profiles": ["backup-uploader"],
        "command": ["upload", "--config", "x"],
    }
    compose.write_text(json.dumps(document))


def copied_by_uploader(tmp_path, *, status=0, profile=None, **fake):
    """Upgrade from a running image with `upload` on a split topology."""
    return run_host(
        tmp_path,
        "upgrade",
        status=status,
        profile=profile,
        prepare=split_topology,
        help_running=COPY_HELP,
        running_image=LOCAL_CURRENT if profile == "local" else CURRENT,
        routes="",
        **fake,
    )


def test_step_1_runs_the_uploader_for_the_step_1_set(tmp_path):
    """It waits for a running copy and names the backup line's digest.

    The backup's own copy reporting busy (the uploader held the lock) does
    not abandon: the uploader's answer about the row decides.
    """
    calls, output, _ = copied_by_uploader(tmp_path, offsite="busy")
    upload = calls[first(calls, " backup-uploader upload ")]
    assert upload.endswith("--wait-lock 1800 --require-digest " + STEP_1_DIGEST), upload
    assert (
        "--config " + str(tmp_path / "root/config/services/backup-uploader.yaml")
        in upload
    )
    assert first(calls, "backup-worker") < first(calls, " backup-uploader upload ")
    assert first(calls, " backup-uploader upload ") < first(calls, " stop web")
    assert f"Upgraded to {TARGET} in" in output
    log = (tmp_path / "logs" / "stewardship-backup.log").read_text()
    assert '"required_copy": "uploaded"' in log


def test_the_reused_backup_of_a_users_resume_is_the_one_copied(tmp_path):
    """A repeated users upgrade copies the earlier run's backup (#952).

    The repeat's backup line reports the reused backup instead of a new
    one; the uploader is asked for that set's digest.
    """
    run_host(
        tmp_path,
        "upgrade",
        noop="f",
        status=1,
        schema_change="1",
        env=USERS,
        base_fail=True,
        help=COPY_HELP,
        routes="",
    )
    split_topology(tmp_path / "root")
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        noop="f",
        schema_change="1",
        env=USERS,
        again=True,
        help=COPY_HELP,
        routes="",
    )
    assert "REUSED BACKUP" in output
    upload = calls[first(calls, " backup-uploader upload ")]
    assert upload.endswith("--require-digest " + "e" * 64)
    assert f"Upgraded to {TARGET} in" in output


@pytest.mark.parametrize(
    "answer, message",
    [
        ('{"required_copy": "not_uploaded", "sets": 0, "state": "failed"}', None),
        ('{"state": "busy"}', "An off-site copy is still running"),
        ("ERROR: off-site upload refused or incomplete", None),
    ],
)
def test_a_step_1_copy_without_the_row_abandons_before_web_stops(
    tmp_path, answer, message
):
    """Anything but an uploaded row for the digest abandons the upgrade."""
    calls, output, _ = copied_by_uploader(tmp_path, status=1, upload=answer)
    assert "did not complete with its off-host copy; abandoning the upgrade" in output
    if message:
        assert message in output
    assert "restarting the background services" in output
    assert not any(" stop web" in call for call in calls)
    assert not any(" retarget-image " in call for call in calls)


def test_a_failed_uploader_run_abandons_and_shows_its_output(tmp_path):
    """A refusal (exit 2) is a failure, its output shown less any values."""
    calls, output, _ = copied_by_uploader(tmp_path, status=1, upload_fail=1)
    assert "FAILED: the off-site uploader exited with status 2" in output
    assert not any(" stop web" in call for call in calls)


def test_an_uploader_killed_at_its_limit_is_logged_and_abandons(tmp_path):
    """The one-off's limit is the lock wait, the copy budget and a margin."""
    calls, output, _ = copied_by_uploader(
        tmp_path, status=1, timeout=" backup-uploader upload "
    )
    killed = next(call for call in calls if call.startswith("timeout "))
    assert killed.startswith(f"timeout --kill-after=30 {1800 + 4 * 3600 + 600} ")
    assert "TIMEOUT: the off-site uploader killed after" in output
    assert not any(" stop web" in call for call in calls)


def test_local_accepts_an_uploader_that_is_not_configured(tmp_path):
    """LOCAL has no off-host target, as for the backup's own line."""
    _, output, _ = copied_by_uploader(
        tmp_path,
        profile="local",
        upload='{"required_copy": "not_configured", "state": "not_configured"}',
    )
    assert "Upgraded to" in output
    _, output, _ = copied_by_uploader(
        tmp_path / "production",
        status=1,
        upload='{"required_copy": "not_configured", "state": "not_configured"}',
    )
    assert "abandoning the upgrade" in output


@pytest.mark.parametrize("case", ["no_upload_command", "no_service"])
def test_without_both_keys_step_1_keeps_the_backup_lines_rule(tmp_path, case):
    """An image between the expand parts, or a pre-split topology: no uploader."""
    fake = {"help_running": UPLOADER_HELP if case == "no_upload_command" else COPY_HELP}
    calls, output, _ = run_host(
        tmp_path,
        "upgrade",
        prepare=split_topology if case == "no_upload_command" else None,
        running_image=CURRENT,
        routes="",
        **fake,
    )
    assert not any(" backup-uploader upload " in call for call in calls)
    assert f"Upgraded to {TARGET} in" in output
    _, output, _ = run_host(
        tmp_path / "busy",
        "upgrade",
        status=1,
        prepare=split_topology if case == "no_upload_command" else None,
        running_image=CURRENT,
        routes="",
        offsite="busy",
        **fake,
    )
    assert "abandoning the upgrade" in output


def flock_calls(tmp_path):
    """The flock stand-in's recorded calls, one per line."""
    path = tmp_path / "state" / "flock"
    return path.read_text().splitlines() if path.exists() else []


def assert_lock_waits(calls, limit):
    """The directory lock alone (since the contract release, #235), bounded."""
    assert calls == [f"-E 75 -w {limit} 9"], calls


def test_the_host_holds_the_copy_lock_for_the_offline_work(tmp_path):
    """The directory lock, bounded, after step 1's copy; nothing written there."""
    calls, output, root = run_host(tmp_path, "upgrade")
    assert_lock_waits(flock_calls(tmp_path), 1800)
    assert "1. Holding the off-site copy lock for the offline work" in output
    assert output.index("Holding the off-site copy lock") < output.index(
        "2. Stopping web"
    )
    # The expand release's legacy lock file is neither created nor locked.
    assert list((root / "backups").iterdir()) == []
    assert not (tmp_path / "state" / "chown").exists()


def test_a_leftover_legacy_lock_file_is_left_alone(tmp_path):
    """A leftover .offsite-copy.lock is never opened, replaced or removed."""

    def prepare(root):
        (root / "backups" / ".offsite-copy.lock").write_text("kept")

    _, _, root = run_host(tmp_path, "upgrade", prepare=prepare)
    assert (root / "backups" / ".offsite-copy.lock").read_text() == "kept"
    assert_lock_waits(flock_calls(tmp_path), 1800)


@pytest.mark.parametrize("plant", ["link", "file"])
def test_a_planted_backups_directory_refuses_before_web_stops(tmp_path, plant):
    """A link or a file at the backups name is never opened or locked."""

    def prepare(root):
        backups = root / "backups"
        backups.rmdir()
        if plant == "link":
            (root / "elsewhere").mkdir()
            backups.symlink_to(root / "elsewhere")
        else:
            backups.write_text("")

    calls, output, _ = run_host(tmp_path, "upgrade", status=1, prepare=prepare)
    assert "is not a directory" in output
    assert "abandoning the upgrade" in output
    assert not any(" stop web" in call for call in calls)
    assert flock_calls(tmp_path) == []


def test_an_expired_copy_lock_wait_is_logged_and_abandons(tmp_path):
    """A copy started since step 1 and still running: no web stop."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, flock_fail=75)
    assert "TIMEOUT: waiting for the off-site copy lock on" in output
    assert "(limit 1800s)" in output
    assert "holds the copy lock; abandoning the upgrade" in output
    assert "restarting the background services" in output
    assert not any(" stop web" in call for call in calls)
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: waiting for the off-site copy lock" in log


def test_a_failed_copy_lock_call_is_not_logged_as_a_timeout(tmp_path):
    """Only flock's 75 is an expired wait; another failure says it failed."""
    calls, output, _ = run_host(tmp_path, "upgrade", status=1, flock_fail=1)
    assert "FAILED: flock on the off-site copy lock on" in output
    assert "exited with status 1" in output
    assert "TIMEOUT" not in output
    assert "abandoning the upgrade" in output
    assert not any(" stop web" in call for call in calls)


def test_one_off_containers_do_not_inherit_the_copy_lock(tmp_path):
    """A client left by a killed run cannot keep the lock (#235, B-L4)."""
    calls, _, _ = run_host(tmp_path, "upgrade")
    assert flock_calls(tmp_path) and any(" run --name " in call for call in calls)
    assert not (tmp_path / "state" / "held-fds").exists()


def test_flock_is_checked_before_step_1():
    """A host without util-linux flock refuses before the backup (#235, B-L3)."""
    script = HOST.read_text()
    preflight = script.index("flock is not installed (util-linux); refusing.")
    line = script[script.rindex("\n", 0, preflight) + 1 : preflight]
    assert line.startswith("command -v flock >/dev/null || ")
    assert preflight > script.index("# END off-site copy lock (#235).")
    assert preflight < script.index('step "1. ')


def test_a_rollback_holds_the_copy_lock_too(tmp_path):
    """Every offline phase holds it, whichever direction."""
    run_host(tmp_path, "rollback")
    assert_lock_waits(flock_calls(tmp_path), 1800)


# The uploader's command as the runbook gives it, and with docker by path,
# its `upload` command and a redirection. A user crontab's line has no user
# field; /etc/crontab's and cron.d's have one.
UPLOADER_COMMANDS = {
    "compose": (
        "docker compose -f /opt/parishkit/config/services/"
        "compose.json -p stewardship run --rm -T backup-uploader"
    ),
    "path": (
        "/usr/bin/docker compose -p stewardship run --rm -T backup-uploader "
        "upload >>/var/log/up 2>&1"
    ),
}
UPLOADER_LINE = "*/5 * * * * " + UPLOADER_COMMANDS["compose"]


@pytest.mark.parametrize("form", sorted(UPLOADER_COMMANDS))
@pytest.mark.parametrize("where", ["crontab", "system", "cron.d"])
def test_an_image_without_upload_refuses_while_the_uploader_line_is_installed(
    tmp_path, where, form
):
    """The expand rollback's preflight, through the shared crontab block."""
    user = "" if where == "crontab" else "root "
    line = f"*/5 * * * * {user}{UPLOADER_COMMANDS[form]}"
    fake = {}
    if where == "crontab":
        fake["crontab"] = line
    else:

        def install(root):
            if where == "system":
                (tmp_path / "crontab").write_text(line + "\n")
            else:
                (tmp_path / "cron.d").mkdir()
                (tmp_path / "cron.d" / "stewardship").write_text(line + "\n")

        fake["prepare"] = install
    calls, output, _ = run_host(tmp_path, "rollback", status=1, **fake)
    assert "has no off-site uploader, but this host's cron still runs it" in output
    assert f"    | {line}" in output
    assert "#rolling-back-the-expand-release" in output
    assert "Nothing was stopped or changed" in output
    assert not any(" stop " in call for call in calls)


def test_a_commented_uploader_line_or_an_image_with_upload_passes(tmp_path):
    """Only an uncommented line refuses, and only for an image without upload."""
    _, output, _ = run_host(tmp_path, "rollback", crontab="# " + UPLOADER_LINE)
    assert "Rolled back to" in output
    _, output, _ = run_host(
        tmp_path / "with-upload",
        "upgrade",
        crontab=UPLOADER_LINE,
        help=COPY_HELP,
        uploader_logins=0,
        routes="",
    )
    assert f"Upgraded to {TARGET} in" in output


def test_another_backup_line_is_not_the_uploaders(tmp_path):
    """The backup's own cron line names backup-worker, not the uploader."""
    line = UPLOADER_LINE.replace("backup-uploader", "backup-worker")
    _, output, _ = run_host(tmp_path, "rollback", crontab=line)
    assert "Rolled back to" in output


# The contract release's route-out preflight (#235, operations specification,
# "No route out"): keyed on the new image listing egress-probe, before
# anything stops; Production refuses, LOCAL only reports.

CONTRACT_HELP = (
    "usage: pk-stewardship {config-check,retarget-image,smoke,upload,egress-probe}"
)


def contract(tmp_path, *, status=0, profile=None, **fake):
    """Run an upgrade to a contract image whose host meets every condition."""
    values = {"help": CONTRACT_HELP, "crontab": UPLOADER_LINE, **fake}
    return run_host(tmp_path, "upgrade", status=status, profile=profile, **values)


def test_the_contract_preflight_probes_the_backup_network_first(tmp_path):
    """Every condition met: the probe runs from the backup network, then the run."""
    calls, output, _ = contract(tmp_path)
    assert f"Upgraded to {TARGET} in" in output
    assert before_the_stop(calls, " egress-probe --gateway ")
    probe = (tmp_path / "state" / "probe").read_text().split()
    assert probe[probe.index("--network") + 1] == "stewardship_offline-backup-worker"
    for option, value in (
        ("--gateway", "172.29.242.65"),
        ("--google-address", "142.250.1.95"),
        ("--listen", "tcp:22,tcp:443,tcp:9999,udp:68"),
        ("--engine-version", "27.3.1"),
        ("--user", "10001:10001"),
    ):
        assert probe[probe.index(option) + 1] == value
    assert {"--read-only", "--cap-drop", "--rm"} <= set(probe)
    assert "iptables -w 5 -S INPUT" in calls
    assert (
        "systemctl is-enabled --quiet stewardship-offline-input-drop.service" in calls
    )
    assert "ahead of any rule that could admit them" in output
    assert "the backup worker's network reaches nothing outside" in output
    log = (tmp_path / "logs" / "stewardship-backup.log").read_text()
    assert "egress probe: " in log and '"closed": true' in log


def test_an_image_without_egress_probe_runs_no_route_out_check(tmp_path):
    """The expand release and earlier: no probe, no firewall or Engine question."""
    calls, _, _ = run_host(tmp_path, "upgrade", help=COPY_HELP, routes="")
    assert not any(
        "egress-probe" in call
        or call.startswith("iptables")
        or "Server.Version" in call
        for call in calls
    )


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (
            {"help_running": UPLOADER_HELP, "running_image": CURRENT},
            "has no off-site uploader: deploy the expand release",
        ),
        ({"crontab": "# " + UPLOADER_LINE}, "runs no off-site uploader line"),
        ({"crontab": ""}, "runs no off-site uploader line"),
        ({"engine": "24.0.7"}, "Docker Engine '24.0.7' lacks the embedded-DNS fix"),
        ({"engine": "25.0.4"}, "Docker Engine '25.0.4' lacks"),
        ({"engine": "23.0.10"}, "Docker Engine '23.0.10' lacks"),
        ({"engine": "20.10.24+dfsg1"}, "Docker Engine '20.10.24+dfsg1' lacks"),
        ({"engine": "26.0.0-rc2"}, "Docker Engine '26.0.0-rc2' lacks"),
        ({"engine": "26.1.0-RC1"}, "Docker Engine '26.1.0-RC1' lacks"),
        ({"engine": "27.0.0-beta.1"}, "Docker Engine '27.0.0-beta.1' lacks"),
        ({"engine": "25.0.5~rc1"}, "Docker Engine '25.0.5~rc1' lacks"),
        ({"engine": "27.0.0~beta"}, "Docker Engine '27.0.0~beta' lacks"),
        ({"engine": "not a version"}, "lacks the embedded-DNS fix"),
        ({"engine_fail": "1"}, "Docker Engine 'unknown' lacks"),
        (
            {"input": "-P INPUT ACCEPT\n-A INPUT -i lo -j ACCEPT"},
            "The host has no INPUT drop for the one-shot networks (172.29.242.0/24)",
        ),
        (
            {
                "input": INPUT_CHAIN.replace(
                    "-A INPUT -s 172.29.242.0/24 -j DROP\n", ""
                )
                + "\n-A INPUT -s 172.29.242.0/24 -j DROP"
            },
            "comes after a rule that could admit them: "
            "-A INPUT -j ufw-before-logging-input;-A INPUT -j ufw-before-input",
        ),
        (
            {"input": "-A INPUT -s 172.29.0.0/16 -j ACCEPT\n" + INPUT_CHAIN},
            "admit them: -A INPUT -s 172.29.0.0/16 -j ACCEPT. Put it first",
        ),
        ({"iptables_fail": "1"}, "Could not list the host's INPUT chain"),
        ({"unit_disabled": "1"}, "is not persisted"),
        ({"unit": runbook_unit("172.29.243.0/24")}, "is not persisted"),
        (
            {"unit": runbook_unit().replace("ExecStart=", "#ExecStart=")},
            "is not persisted",
        ),
        ({"probe_internal": "false"}, "Could not read the backup worker's internal"),
        ({"probe_gateway": ""}, "Could not read the backup worker's internal"),
        ({"no_google": "1"}, "could not resolve www.googleapis.com"),
        ({"ss_fail": "1"}, "Could not list the host's listening ports"),
        (
            {
                "probe": '{"closed": false, "reached": ["172.29.242.65:443 (gw)"]}',
                "probe_rc": "3",
            },
            "The egress probe reached 172.29.242.65:443 (gw) from",
        ),
        ({"probe": "", "probe_rc": "2"}, "The egress probe did not answer (exit 2)"),
        ({"probe": '{"closed": false}', "probe_rc": "0"}, "The egress probe reached"),
        ({"probe": "garbage", "probe_rc": "0"}, "The egress probe did not answer"),
    ],
)
def test_production_refuses_the_contract_before_anything_stops(tmp_path, fake, message):
    """Each missing condition, and each failure to ask, refuses on Production."""
    calls, output, _ = contract(tmp_path, status=1, **fake)
    assert message in output
    assert "Nothing was stopped or changed; refusing." in output
    assert not any(" stop " in call for call in calls)


def test_an_expired_probe_is_killed_logged_and_refuses(tmp_path):
    """A probe past its limit: what, the limit and the elapsed time are logged."""
    calls, output, _ = contract(tmp_path, status=1, timeout=" egress-probe ")
    assert "TIMEOUT: the egress probe killed after" in output
    assert "(limit 120s)" in output
    assert "The egress probe did not answer (exit 124)" in output
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "TIMEOUT: the egress probe killed after" in log
    assert not any(" stop " in call for call in calls)


@pytest.mark.parametrize(
    "engine", ["26.0.0", "25.0.5", "25.0.10", "23.0.11", "28.1.1+dfsg1", "29.8.1"]
)
def test_every_fixed_engine_passes(tmp_path, engine):
    """The CVE-2024-29018 floor: 26.0.0, or 25.0.5 / 23.0.11 and later."""
    _, output, _ = contract(tmp_path, engine=engine)
    assert f"Docker Engine {engine} has the embedded-DNS fix" in output


def test_local_mode_only_reports_the_contract_checks(tmp_path):
    """pk-local has no off-site target or host firewall rule: report, continue."""
    _, output, _ = contract(
        tmp_path,
        profile="local",
        crontab="",
        input="",
        probe='{"closed": false, "reached": ["172.29.242.65:22 (gateway)"]}',
        probe_rc="3",
    )
    assert "runs no off-site uploader line" in output
    assert "no INPUT drop for the one-shot networks" in output
    assert "The egress probe reached 172.29.242.65:22 (gateway)" in output
    assert "LOCAL: the checks above only report here; continuing" in output
    assert "Upgraded to " in output


@pytest.mark.parametrize(
    "chain",
    [
        # Rules that cannot admit the block may come first.
        "-A INPUT -i lo -j ACCEPT\n-A INPUT -s 10.0.0.0/8 -j ACCEPT\n"
        "-A INPUT -s 172.29.243.0/24 -j ACCEPT\n-A INPUT -p udp -j REJECT\n"
        '-A INPUT -m comment --comment "count"\n' + INPUT_CHAIN,
    ],
)
def test_rules_that_cannot_admit_the_block_may_precede_the_drop(tmp_path, chain):
    """Loopback, disjoint sources, drops and counters ahead of the drop: accepted."""
    _, output, _ = contract(tmp_path, input=chain)
    assert "ahead of any rule that could admit them" in output


@pytest.mark.parametrize(
    "earlier",
    [
        "-A INPUT -s 172.29.242.72/29 -j ACCEPT",
        "-A INPUT ! -s 10.0.0.0/8 -j ACCEPT",
        "-A INPUT ! -i lo -j ACCEPT",
        '-A INPUT -m comment --comment "-i lo" -j ACCEPT',
        "-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        "-A INPUT -j RETURN",
    ],
)
def test_a_rule_that_could_admit_the_block_ahead_of_the_drop_refuses(tmp_path, earlier):
    """An overlapping, negated or unknown match ahead of the drop refuses."""
    _, output, _ = contract(tmp_path, status=1, input=earlier + "\n" + INPUT_CHAIN)
    assert f"comes after a rule that could admit them: {earlier}." in output


@pytest.mark.parametrize(
    ("fragment", "what", "message"),
    [
        (
            "Server.Version",
            "asking Docker Engine for its version",
            "Docker Engine 'unknown' lacks",
        ),
        (
            "-S INPUT",
            "listing the host's INPUT chain",
            "Could not list the host's INPUT",
        ),
        ("is-enabled", "asking systemd whether", "is not persisted"),
        (" cat stewardship-offline", "reading stewardship-offline", "is not persisted"),
        (
            "{{.Internal}}",
            "inspecting stewardship_offline",
            "Could not read the backup",
        ),
        ("{{.Gateway}}", "reading the gateway of", "Could not read the backup"),
        ("ahostsv4", "resolving www.googleapis.com", "could not resolve"),
        (
            "ss -Htlnu",
            "listing the host's listening ports",
            "Could not list the host's",
        ),
    ],
)
def test_every_host_question_is_bounded_and_its_kill_logged(
    tmp_path, fragment, what, message
):
    """A preflight question past its limit: logged (what, limit, elapsed), refused."""
    _, output, _ = contract(tmp_path, status=1, timeout=fragment)
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert re.search(
        f"TIMEOUT: {re.escape(what)}.* killed after [0-9]+s \\(limit 30s\\)", log
    )
    assert message in output


def test_the_probes_error_output_is_kept_in_the_log(tmp_path):
    """A refused argument's message reaches the log, not only its exit status."""
    _, output, _ = contract(
        tmp_path,
        status=1,
        probe="",
        probe_rc="2",
        probe_err="ERROR: egress-probe --listen takes tcp:PORT and udp:PORT items.",
    )
    log = next((tmp_path / "logs").glob("stewardship-upgrade-*.log")).read_text()
    assert "    | ERROR: egress-probe --listen takes tcp:PORT" in log
    assert "The egress probe did not answer (exit 2)" in output
