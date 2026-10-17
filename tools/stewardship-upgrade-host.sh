#!/usr/bin/env bash
# The host half of tools/stewardship-upgrade.sh: the deployment runbook's
# Upgrade steps 1-6 (docs/guides/stewardship-deployment-runbook.md#upgrade)
# and its Rollback, run on the host that runs the deployment. Nothing in it
# is meant to be run by hand. tools/stewardship-upgrade.sh uploads it over
# ssh and runs it in `production` mode on a deployment in Testing or in
# Production; tools/stewardship-local.sh (through its VM half) runs it in
# `local` mode on the local laptop environment's deployment (#476), so a
# local deploy follows the same steps as a Production upgrade.
#
# Usage: stewardship-upgrade-host.sh REPO IMAGE ROOT PROJECT YAML UUID SCHEMA_CHANGE MODE [local BUILD]
#
#   MODE is upgrade or rollback. Without the trailing arguments the profile
#   is production; `local BUILD` selects local mode, BUILD being the packed
#   checkout in the VM (for deploy/stewardship/Dockerfile.faketime).
#
# Production mode must stay byte-identical: a run with eight arguments behaves
# and prints exactly as the heredoc it was extracted from. Local mode differs in
# exactly these ways (local-environment specification, "Operator script"):
#   - it refuses unless ROOT carries the local marker and YAML says
#     `profile: local`, so it can never run on another deployment;
#   - IMAGE is the LOCAL image tag built inside the VM, which must exist
#     already; nothing is pulled and no registry digest is checked;
#   - the backup is still required, but LOCAL has no off-host target, so
#     an off-site state of not_configured is accepted alongside uploaded;
#   - the services keep the deployment's clock mode (the faketime override
#     is added when the marker says fake, and the derived fake-clock image
#     of the new application image is built after the retarget);
#   - the services keep the deployment's debug-logging setting: the caller's
#     PARISHKIT_DEBUG_LOGGING (the VM half exports the deployment record's)
#     instead of Production's unconditional 0;
#   - the public origin is probed through Caddy's own CA, by the Server
#     header, since before the wizard the application answers 503;
#   - for BG-12's rehearsal, the caller may choose the bulk Family send
#     (PARISHKIT_LOCAL_BULK_FAMILY_SEND=on|off) and the modeled SMTP latency
#     (PARISHKIT_LOCAL_SMTP_LATENCY_MS, milliseconds) the retarget renders;
#     unset or "keep" carries the current value over, as Production does
#     for the bulk switch. A rollback never renders the latency (the
#     previous release may not know the setting, and its loader would refuse
#     it after web had stopped). Production mode never reads either variable;
#   - the caller may choose which ParishSoft pk-local reads (#1399):
#     PARISHKIT_LOCAL_PARISHSOFT=real|fake, unset or "keep" carrying the
#     current choice over. Like the latency, a rollback never renders the
#     real API (it falls back to the fake), and Production mode never reads
#     the variable.
#
# In both modes the export download window (#1398) is carried over, and
# PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES (15-240) changes it.
# A rollback never renders it, for the same reason as the latency: it is
# 60 minutes there until the next upgrade sets it again.
#
# In both modes, the release that converts sign-in rules to users (#952)
# needs the offline conversion between two migrations (operations
# specification, "Users upgrade from sign-in rules"). The operator supplies
# it through four variables, all or none, on an upgrade only:
# STEWARDSHIP_USERS_OPERATION (a new UUID; the same one resumes an
# interrupted run), STEWARDSHIP_USERS_OPERATOR and STEWARDSHIP_USERS_REASON
# (who and why, recorded on the conversion) and
# STEWARDSHIP_USERS_ADMINISTRATORS (the comma-separated Administrators the
# preview lists, which the conversion must keep). With them, step 4 migrates
# to the users upgrade's base, applies the grants, prints the preview,
# converts, and only then migrates the rest and applies the grants again.
# Without them, a release that needs the conversion stops at its migration
# (the swap refuses an unconverted configuration) with the maintenance page
# up, and the run is repeated with them. A repeat after a base migration
# that stopped partway reuses the first run's backup (step 1). No address
# is ever written here.

set -euo pipefail
# shellcheck disable=SC2034 # REPO is validated by the laptop half; kept for the fixed argument order
repo=$1 image=$2 root=$3 project=$4 yaml=$5 uuid=$6 schema_change=$7 mode=$8
profile=${9:-production}
build=${10-}
case "$profile" in
    production) ;;
    local) [ -n "$build" ] || { echo "local mode needs the packed checkout directory; refusing." >&2; exit 2; } ;;
    *) echo "usage: $0 REPO IMAGE ROOT PROJECT YAML UUID SCHEMA_CHANGE MODE [local BUILD]" >&2; exit 2 ;;
esac
# STEWARDSHIP_LOG_DIR lets the tests run this half without /var/log. ssh
# does not forward environment variables by default, so a real run logs
# under /var/log unless the operator deliberately set it on the host.
logdir=${STEWARDSHIP_LOG_DIR:-/var/log}
log=$logdir/stewardship-$mode-$(date -u +%Y%m%dT%H%M%SZ).log
exec > >(tee -a "$log") 2>&1
tee_pid=$!
# The log, reachable from inside a pipeline whose output is filtered.
exec 3>&2
echo "Log: $log"

# What a failure means depends on how far the run got: before web stops,
# the current release is put back as it was; with web stopped but nothing
# retargeted yet, every service is simply started again; after the retarget,
# the maintenance page stays up until the run is repeated or the other
# direction is taken (runbook, step 6 and Rollback).
phase=prepare
resumed=0
work=$(mktemp -d)
finish() {
    rc=$?
    rm -rf "$work"
    if [ "$rc" -ne 0 ]; then
        case "$phase" in
            prepare) echo "==> Failed before anything stopped; nothing changed." ;;
            background | stopped) if [ "$resumed" = 1 ]; then
                # Web was already down when this run began (below): the
                # database may be in a state the services refuse, so nothing
                # is started and the maintenance page stays up.
                echo "==> Failed before the retarget, with web already stopped when this run began:"
                echo "    nothing is started; caddy serves the maintenance page. Re-run with the same"
                echo "    digest (with the STEWARDSHIP_USERS_* variables if the release notes announce"
                echo "    the users upgrade), or roll back (runbook, Rollback)."
            elif [ "$phase" = background ]; then
                echo "==> Failed before web stopped; restarting the background services."
                restart_background
            else
                # Nothing was retargeted, so the current release's documents
                # are intact: start every online service of the file again,
                # including any that restarted meanwhile and was stopped too.
                echo "==> Failed with web stopped but nothing retargeted; starting everything again."
                "${dc[@]}" up --detach --wait "${wanted[@]}" 2>&1 | quiet || true
            fi ;;
            offline)
                echo "==> Failed with web stopped: caddy serves the maintenance page."
                if [ "$mode" = rollback ]; then
                    echo "    Re-run the rollback with the same digest, or upgrade again; if retarget-image"
                    echo "    refused, see the runbook's Rollback section (a field the newer release added)."
                else
                    echo "    Re-run with the same digest, or roll back (runbook, Rollback)."
                fi ;;
        esac
    fi
    # Let tee flush the last lines before ssh exits (fd 3 is its copy of
    # stderr, which `oneoff` writes to).
    exec >&- 2>&- 3>&-
    wait "$tee_pid" 2>/dev/null || true
    exit "$rc"
}
trap finish EXIT
# A signal (Ctrl-C or kill on the host; SIGPIPE once a dropped ssh session
# has taken tee's output away) ends the run like a failure, but first kills
# and removes the one-off container a step-4 command left running (`oneoff`
# below): stopping the `docker compose run` client alone leaves it running.
# Its lines go straight to the log file, since the terminal may be gone.
interrupted() {
    # interrupted NAME NUMBER: the trap of one signal; exits 128+NUMBER.
    trap '' INT TERM HUP PIPE
    {
        echo "INTERRUPTED: SIG$1 received in phase $phase"
        stop_oneoff "SIG$1"
    } >>"$log" 2>&1
    exit $((128 + $2))
}
trap 'interrupted INT 2' INT
trap 'interrupted TERM 15' TERM
trap 'interrupted HUP 1' HUP
trap 'interrupted PIPE 13' PIPE

# The generated Compose files pass this through to every application service.
# Production starts every service with debug logging off; local mode keeps
# the deployment's own setting, which the VM half exported before running
# this script. `debug` is what the final checks expect in every container.
if [ "$profile" = local ]; then
    export PARISHKIT_DEBUG_LOGGING=${PARISHKIT_DEBUG_LOGGING:-0}
else
    export PARISHKIT_DEBUG_LOGGING=0
fi
debug=$PARISHKIT_DEBUG_LOGGING
services="$root/config/services"
clock_dir="$root/run/local/clock"
clock=real
if [ "$profile" = local ]; then
    # Local mode acts only on the local environment's deployment: the marker
    # the operator script writes after provisioning, the LOCAL profile in the
    # deployment YAML, and an image tag of the LOCAL pattern that was built
    # inside the VM already (specification, "Operator script" and "Fake
    # clock"). All of it is refused before anything is read or stopped.
    [ -f "$root/.parishkit-local" ] ||
        { echo "$root does not carry the local marker $root/.parishkit-local; refusing local mode." >&2; exit 1; }
    grep -qE '^[[:space:]]*profile:[[:space:]]*local[[:space:]]*$' "$yaml" 2>/dev/null ||
        { echo "$yaml does not say 'profile: local'; refusing local mode." >&2; exit 1; }
    [[ $image =~ ^parishkit-stewardship-local:[0-9a-f]{40}(-dirty)?-[0-9]+$ ]] ||
        { echo "$image is not a LOCAL image tag (parishkit-stewardship-local:<commit>[-dirty]-<epoch>); refusing." >&2; exit 1; }
    docker image inspect "$image" >/dev/null 2>&1 ||
        { echo "The image $image is not in the VM; build it first (the operator script does). Refusing." >&2; exit 1; }
    # The persisted clock mode, as every command of the VM half reads it: a
    # missing or unknown marker is an error, not a default.
    clock=$(cat "$clock_dir/mode" 2>/dev/null || echo missing)
    case "$clock" in
        fake|normal) ;;
        *) echo "The clock-mode marker $clock_dir/mode says '$clock'; expected fake or normal. Refusing." >&2; exit 1 ;;
    esac
fi
# shellcheck disable=SC2054 # the tmpfs options are one comma-separated word
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)
runbook=docs/guides/stewardship-deployment-runbook.md

began=$(date -u +%s)
last=$began
step() {
    # Timestamp each step and say how long the previous one took.
    now=$(date -u +%s)
    echo "==> $(date -u +%H:%M:%S) (+$((now - last))s) $*"
    last=$now
}
quiet() {
    # Compose progress lines add nothing to an upgrade log.
    grep -vE ' (Creat|Start|Stop|Wait|Running|Healthy|Recreat)' || true
}
# Each one-off container `oneoff` starts is named after this run and its
# step, so a kill can reach the container itself by name.
oneoff_prefix="$project-$mode-$(date -u +%Y%m%dT%H%M%SZ)-$$"
oneoff_name=""
oneoff_pid=""
stop_oneoff() {
    # stop_oneoff WHY: kill and remove the running one-off container (and
    # its `docker compose run` client), if there is one, saying so.
    local name=$oneoff_name
    [ -n "$name" ] || return 0
    oneoff_name=""
    [ -z "$oneoff_pid" ] || kill "$oneoff_pid" 2>/dev/null || true
    docker kill "$name" >/dev/null 2>&1 || true
    if docker rm --force "$name" >/dev/null 2>&1; then
        echo "    one-off container $name killed and removed ($1)"
    else
        echo "    one-off container $name was already gone ($1)"
    fi
}
withhold_values() {
    # Copy a failed command's output, withholding what may carry database
    # values such as addresses: a PostgreSQL DETAIL line, and each Python
    # traceback's frames and message, of which only the exception's class
    # is kept (up to a following chained traceback or ERROR: line). The
    # operator commands print only a reviewed line, an exception class and
    # a SQLSTATE; this guards whatever escapes them.
    awk '
        /^Traceback \(most recent call last\):/ {
            tb = 1; print "Traceback withheld (it may carry database values)"; next
        }
        tb == 1 && /^[ \t]/ { next }
        tb == 1 { sub(/:.*/, ""); print "Exception class: " $0; tb = 2; next }
        tb == 2 && /^(The above exception|During handling of the above exception|ERROR: )/ { tb = 0 }
        tb == 2 { next }
        /^[ \t]*DETAIL:/ { print "DETAIL withheld"; next }
        { print }
    '
}
oneoff() {
    # oneoff SECONDS WHAT STEP SERVICE [ARGS...]: `docker compose run` one
    # SERVICE container named after this run and STEP, killing it (TERM,
    # then KILL 30 s later) once SECONDS have passed. Prints the command's
    # last output line, or on a failure all of its output (into the run's
    # log too) less what may carry database values (`withhold_values`),
    # since the last line alone may be empty or a generic refusal that
    # hides the cause. The client runs in the background and
    # the shell waits for it, so a signal's trap runs at once rather than
    # after the command. On an expired limit (124, or 137 at the limit) the kill is
    # logged on fd 3, the run's log, with what ran, the limit and the
    # elapsed time, and the container is killed and removed by name, since
    # killing the client leaves it running. Returns the command's status
    # (124 or 137 after a kill), so the caller stops.
    local limit=$1 what=$2 started rc=0 elapsed
    oneoff_name=$oneoff_prefix-$3
    shift 3
    started=$(date -u +%s)
    timeout --kill-after=30 "$limit" "${dc[@]}" run --name "$oneoff_name" --rm -T "$@" \
        </dev/null >"$work/oneoff.out" 2>&1 &
    oneoff_pid=$!
    wait "$oneoff_pid" || rc=$?
    oneoff_pid=""
    elapsed=$(( $(date -u +%s) - started ))
    if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ "$elapsed" -ge "$limit" ]; }; then
        echo "TIMEOUT: $what killed after ${elapsed}s (limit ${limit}s). The maintenance page" >&3
        echo "    stays up." >&3
        stop_oneoff "time limit" >&3
    fi
    oneoff_name=""
    if [ "$rc" -eq 0 ]; then
        tail -1 "$work/oneoff.out"
    else
        echo "FAILED: $what exited with status $rc; its full output:" >&2
        withhold_values <"$work/oneoff.out" | sed 's/^/    | /' >&2
    fi
    return "$rc"
}

# The project keeps whichever Compose file it already runs under; an upgrade
# or a rollback changes images, not topology.
compose=$(docker compose ls --all --format json |
    jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles | split(",")[0]')
if [ -z "$compose" ] || [ ! -f "$compose" ]; then
    echo "Project $project not found; refusing." >&2
    exit 1
fi
dc=(docker compose -f "$compose" -p "$project")
if [ "$clock" = fake ]; then
    # The one deliberate overlay of the local environment: in fake-clock
    # mode every service runs under the faketime override, which the
    # retarget re-renders beside the Compose file with the derived image
    # names (specification, "Fake clock").
    override="$services/compose.faketime.json"
    [ -f "$override" ] || { echo "Clock mode is fake but $override is missing; refusing." >&2; exit 1; }
    dc=(docker compose -f "$compose" -f "$override" -p "$project")
fi
previous=$(jq -r '.services.web.image // empty' "$compose")
case "$mode" in
    upgrade) step "Upgrading $project ($(basename "$compose")) from ${previous:-unknown} to $image" ;;
    rollback) step "Rolling $project ($(basename "$compose")) back from ${previous:-unknown} to $image" ;;
esac
[ "$previous" != "$image" ] || echo "    (same digest: re-running the $mode)"
[ "$clock" = real ] || echo "    local deployment in $clock clock mode (debug logging $debug)"
# What names the kept static trees: the digest's hex in Production, the
# tag's <commit>[-dirty]-<epoch> part for a LOCAL image.
if [ "$profile" = local ]; then image_id=${image##*:}; else image_id=${image##*sha256:}; fi
# The current file's online services: what the stopped-phase trap starts
# again. Step 6 re-reads the list from the re-rendered file, since the
# target release may add or remove a service. grep's `|| true` on an empty
# list is caught by the non-empty check that follows.
# shellcheck disable=SC2207 # service names hold no whitespace
wanted=($("${dc[@]}" config --services | grep -vxE 'postgres|valkey' || true))
[ "${#wanted[@]}" -gt 0 ] || { echo "$(basename "$compose") lists no online services; refusing." >&2; exit 1; }
# A run that finds web stopped repeats one that ended with the maintenance
# page up, most often a users upgrade stopped in step 4 (#952) with only the
# users swap pending, which every online service refuses. Such a run never
# starts a service before its own step 6: a failure before the retarget
# leaves the maintenance page up rather than starting services into a state
# they refuse (or restarting what was down for another reason).
running=$("${dc[@]}" ps --services --status running || true)
grep -qxF web <<<"$running" || resumed=1
[ "$resumed" = 0 ] ||
    echo "    web is not running: repeating a run that left the maintenance page up; a failure before the retarget starts nothing"

database=$(jq -er '.deployment.postgres.name' "$services/web.yaml")
origin=$(jq -er '.deployment.public_origin' "$services/web.yaml")

# Runtime fallbacks set by a prefixed `up` (per-message transport, one mail
# consumer, bulk send quick-stop) would be lost when the services are
# recreated below; the runbook says to repeat them by hand, so refuse and
# let the operator decide rather than silently undo one.
for role in mail-dispatch worker scheduler; do
    id=$("${dc[@]}" ps -q "$role" 2>/dev/null || true)
    [ -n "$id" ] || continue
    if docker inspect --format '{{join .Config.Env "\n"}}' "$id" |
        grep -E '^PARISHKIT_STEWARDSHIP_(FAMILY_MAIL_TRANSPORT|MAIL_CONSUMERS|BULK_FAMILY_SEND)=.' ; then
        echo "$role runs with a runtime fallback (above); $mode by hand per the runbook." >&2
        exit 1
    fi
done

# Carry the current bulk Family send switch over (runbook step 6 note): a
# retarget-image without the variable renders the documents with it off.
[ -r "$services/worker.yaml" ] || { echo "Cannot read $services/worker.yaml; refusing." >&2; exit 1; }
# Plain -r: -e would fail on the legitimate answer false.
bulk=$(jq -r '.deployment.bulk_family_send // false' "$services/worker.yaml")
if [ "$profile" = local ]; then
    case "${PARISHKIT_LOCAL_BULK_FAMILY_SEND:-keep}" in
        keep) ;;
        on) bulk=true ;;
        off) bulk=false ;;
        *) echo "PARISHKIT_LOCAL_BULK_FAMILY_SEND must be on, off or keep; refusing." >&2; exit 1 ;;
    esac
fi
switches=()
if [ "$bulk" = true ]; then
    batch=$(jq -er '.deployment.bulk_send_batch // 20' "$services/worker.yaml")
    switches=(-e PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1 -e "PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=$batch")
    echo "    bulk Family send is on (batch $batch); keeping it on"
fi
# Carry the export download window over too (#1398): a retarget-image
# without the variable renders the documents with the 60-minute default.
# An operator changes it with PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES.
window=${PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES:-$(jq -er '.deployment.export_download_window_minutes // 60' "$services/worker.yaml")}
# Bounded here, before anything stops, as the SMTP latency is: the loader's
# own refusal would come only at the retarget, with web already down.
if ! [[ $window =~ ^[0-9]{1,3}$ ]] || [ "$((10#$window))" -lt 15 ] || [ "$((10#$window))" -gt 240 ]; then
    echo "The export download window must be 15-240 minutes; refusing." >&2
    exit 1
fi
# Base ten, so 060 is 60 (the default, which renders nothing).
window=$((10#$window))
if [ "$mode" = rollback ] && [ "$window" != 60 ]; then
    # The previous release may not know the setting; its loader refuses an
    # unknown variable, after web had stopped. It renders the default.
    echo "    the export download window (${window} minutes) is not carried into a rollback; it is 60 minutes until the next upgrade sets it again"
    window=60
fi
if [ "$window" != 60 ]; then
    switches+=(-e "PARISHKIT_STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES=$window")
    echo "    export download window is $window minutes; keeping it"
fi
if [ "$profile" = local ]; then
    # The rehearsal's modeled Gmail latency (BG-12), which the deployment
    # loader admits only in the local profile; carried over like the switch.
    latency=$(jq -r '.deployment.local_smtp_latency_ms // 0' "$services/worker.yaml")
    case "${PARISHKIT_LOCAL_SMTP_LATENCY_MS:-keep}" in
        keep) ;;
        *[!0-9]*) echo "PARISHKIT_LOCAL_SMTP_LATENCY_MS must be milliseconds or keep; refusing." >&2; exit 1 ;;
        *) latency=$PARISHKIT_LOCAL_SMTP_LATENCY_MS ;;
    esac
    # Checked here, before anything stops: the loader's own refusal would
    # come only at the retarget, with web already down. Base 10 whatever the
    # leading zeros.
    if ! [[ $latency =~ ^[0-9]{1,5}$ ]] || [ "$((10#$latency))" -gt 5000 ]; then
        echo "The SMTP latency must be 0-5000 ms; refusing." >&2
        exit 1
    fi
    latency=$((10#$latency))
    if [ "$mode" = rollback ] && [ "$latency" != 0 ]; then
        echo "    the modeled SMTP latency (${latency} ms) is not carried into a rollback; deploy --smtp-latency-ms sets it again"
        latency=0
    fi
    if [ "$latency" != 0 ]; then
        switches+=(-e "PARISHKIT_STEWARDSHIP_LOCAL_SMTP_LATENCY_MS=$latency")
        echo "    modeled SMTP latency ${latency} ms per message"
    fi
    # Which ParishSoft pk-local reads (#1399): the fake (the default, not
    # rendered) or the real API (parishkit.parishsoft.DEFAULT_API_BASE_URL;
    # a test keeps the two equal). The deployment never writes to either.
    parishsoft_api=https://ps-fs-external-api-prod.azurewebsites.net/api/v2
    parishsoft=$(jq -r '.deployment.parishsoft_base_url // empty' "$services/worker.yaml")
    case "${PARISHKIT_LOCAL_PARISHSOFT:-keep}" in
        keep) ;;
        real) parishsoft=$parishsoft_api ;;
        fake) parishsoft="" ;;
        *) echo "PARISHKIT_LOCAL_PARISHSOFT must be real, fake or keep; refusing." >&2; exit 1 ;;
    esac
    if [ -n "$parishsoft" ] && [ "$parishsoft" != "$parishsoft_api" ]; then
        echo "The rendered ParishSoft base URL is not the real API; refusing." >&2
        exit 1
    fi
    if [ "$mode" = rollback ] && [ -n "$parishsoft" ]; then
        echo "    the real ParishSoft API is not carried into a rollback (it reads the fake); deploy --parishsoft real sets it again"
        parishsoft=""
    fi
    if [ -n "$parishsoft" ]; then
        switches+=(-e "PARISHKIT_STEWARDSHIP_PARISHSOFT_BASE_URL=$parishsoft")
        echo "    reading the real ParishSoft API (never writing)"
        # A faked process checks ParishSoft's certificate against a past
        # date: it may refuse a renewed one and accept a recently expired one.
        [ "$clock" != fake ] ||
            echo "    WARNING: the real ParishSoft API under the fake clock; prefer a normal-clock deployment" >&2
    fi
fi

# The users conversion's inputs (#952; header above), checked before anything
# stops. Production passes them through the laptop half's environment
# prefix, local mode through the VM half's.
users_upgrade=0
if [ -n "${STEWARDSHIP_USERS_OPERATION-}${STEWARDSHIP_USERS_OPERATOR-}${STEWARDSHIP_USERS_REASON-}${STEWARDSHIP_USERS_ADMINISTRATORS-}" ]; then
    [ "$mode" = upgrade ] ||
        { echo "A rollback never converts; unset the STEWARDSHIP_USERS_* variables. Refusing." >&2; exit 1; }
    if [ -z "${STEWARDSHIP_USERS_OPERATION-}" ] || [ -z "${STEWARDSHIP_USERS_OPERATOR-}" ] ||
        [ -z "${STEWARDSHIP_USERS_REASON-}" ] || [ -z "${STEWARDSHIP_USERS_ADMINISTRATORS-}" ]; then
        echo "The users upgrade needs all four STEWARDSHIP_USERS_* variables; refusing." >&2
        exit 1
    fi
    [[ $STEWARDSHIP_USERS_OPERATION =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
        { echo "STEWARDSHIP_USERS_OPERATION must be a lowercase UUID; refusing." >&2; exit 1; }
    [[ $STEWARDSHIP_USERS_ADMINISTRATORS =~ ^[^,[:space:]@]+@[^,[:space:]@]+(,[^,[:space:]@]+@[^,[:space:]@]+)*$ ]] ||
        { echo "STEWARDSHIP_USERS_ADMINISTRATORS must be comma-separated addresses, no spaces; refusing." >&2; exit 1; }
    [ "$schema_change" = 1 ] ||
        { echo "The users upgrade migrates; it needs the schema-change override as well. Refusing." >&2; exit 1; }
    users_upgrade=1
    echo "    users upgrade: operation $STEWARDSHIP_USERS_OPERATION, keeping $(tr ',' '\n' <<<"$STEWARDSHIP_USERS_ADMINISTRATORS" | wc -l | tr -d ' ') confirmed Administrators"
fi

# Step 1: everything that needs only the target image, while the site is up.
if [ "$profile" = local ]; then
    # The LOCAL image was built inside the VM a moment ago (checked above);
    # there is no registry to pull from and no digest to verify.
    step "1. Using the local image $image (built in the VM; nothing to pull)"
else
    if [ "$mode" = upgrade ]; then pulled="the release"; else pulled="the previous release"; fi
    step "1. Pulling $pulled"
    docker pull --quiet "$image" >/dev/null
    docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" | grep -xF "$image" >/dev/null ||
        { echo "The pulled image does not carry $image; refusing." >&2; exit 1; }
fi

# An upgrade collects the new release's static tree; a rollback reuses the
# tree the upgrade kept for the previous release when it is still there and
# holds files (the static storage keeps no manifest to verify against, so
# an empty or missing tree is the cue to collect again).
kept="$root/cache/static.$image_id"
if [ "$mode" = rollback ] && [ -n "$(find "$kept" -type f -print -quit 2>/dev/null)" ]; then
    step "1. Using the kept static tree $kept"
    static_src=$kept
else
    step "1. Collecting static files into cache/static.next"
    rm -rf "$root/cache/static.next"
    install -d -o 10001 -g 10001 -m 0700 "$root/cache/static.next"
    "${isolated[@]}" \
        --mount "type=bind,source=$root/cache/static.next,target=$root/cache/static.next" \
        "$image" collect-static --destination "$root/cache/static.next" | tail -1
    static_src="$root/cache/static.next"
fi

step "1. Rendering the upgrade check"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root,readonly" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" upgrade-check --config /run/operator.yaml --confirm-deployment "$uuid" >"$work/upgrade-check.sql" ||
    { echo "Could not render the upgrade check in $image; refusing." >&2; exit 1; }
check() {
    # Run the rendered upgrade check in a read-only session; prints t or f.
    "${dc[@]}" exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres \
        psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 <"$work/upgrade-check.sql" 2>&1 || true
}
refuse_rollback() {
    # An image-only rollback is possible only when the schema and every
    # runtime grant already match the previous image (runbook, Rollback).
    echo "The previous image's upgrade check answered: $(head -3 <<<"$1")" >&2
    echo "The schema or a grant differs from what $image expects, so an image-only" >&2
    echo "rollback is not possible; this script never restores a database." >&2
    echo "Follow the database-restore rollback in $runbook#rollback." >&2
    exit 1
}
advisory=$(check)
echo "    advisory answer: $(head -3 <<<"$advisory")"
if [ "$advisory" != t ]; then
    if [ "$mode" = rollback ]; then
        refuse_rollback "$advisory"
    elif [ "$schema_change" != 1 ]; then
        # Runbook step 1: for a release whose notes promise no schema or
        # grant change, anything but t is the cue to stop before anything
        # stops.
        echo "The upgrade check expects migration or grants to change something." >&2
        # The check also answers f when the live catalog has drifted from
        # what provisioning set up (#389): a login's attributes, membership,
        # isolation (a member role, an owned object, foreign-data USAGE, a
        # parameter privilege (#845)) or an excess grant. The services'
        # identity check would refuse that.
        echo "Or the database has drifted from its provisioned grants or login isolation" >&2
        echo "(a member role, an owned object, foreign-data USAGE, a parameter privilege," >&2
        echo "an excess grant), which the services' identity check or database-grants" >&2
        echo "would refuse: find and repair that drift before upgrading; see $runbook#upgrade." >&2
        if [ "$profile" = local ]; then
            echo "If this pull request changes the schema or a grant, re-run as 'deploy --schema-change'." >&2
        else
            echo "Read the release notes; set STEWARDSHIP_SCHEMA_CHANGE=1 only if they announce it." >&2
        fi
        exit 1
    fi
fi

step "1. Stopping the background services (web keeps serving)"
background=$("${dc[@]}" ps --services --status running | grep -vxE 'web|caddy|postgres|valkey' || true)
restart_background() {
    # Abandon the run before anything was retargeted: bring the services
    # that were stopped back, unless web was already down when the run
    # began (a repeated run; see `resumed`), when nothing is started.
    [ "$resumed" = 0 ] || return 0
    # shellcheck disable=SC2086
    [ -z "$background" ] || "${dc[@]}" up --detach --wait $background 2>&1 | quiet || true
}
phase=background
# shellcheck disable=SC2086 # one service name per word
[ -z "$background" ] || "${dc[@]}" stop $background 2>&1 | quiet

step "1. Backup (required) and its off-host copy"
# A repeated users upgrade (#952) whose earlier run already retargeted to
# this image may find that run's base migration stopped partway, with
# migrations outside the swap pending, which the backup refuses. Asked to
# resume, the backup then reports that run's step-1 backup (recent, the
# restore point of this upgrade) instead of taking one; in any other state
# it backs up as usual. The worker runs the image the documents name, so
# the option is passed only once they name this one.
backup_args=()
if [ "$users_upgrade" = 1 ] && [ "$resumed" = 1 ] && [ "$previous" = "$image" ]; then
    backup_args=(backup --config "$services/backup-worker.yaml" --users-upgrade-resume)
fi
backup=$("${dc[@]}" run --rm -T backup-worker ${backup_args[@]+"${backup_args[@]}"} 2>&1 |
    grep -v '"DEBUG"' | tail -1 || true)
echo "    $backup"
echo "$(date -u +%FT%TZ) $backup" >>"$logdir/stewardship-backup.log"
# LOCAL refuses every off-host target, so its backup reports the copy as
# not_configured; the backup itself is still required (specification,
# "Operator script"). Production accepts nothing but an uploaded copy.
# `|| true`: a backup line that is not JSON (the worker refused before it
# printed one) must reach the abandon message below, not end the run here.
offsite=$(jq -r '.offsite.state' <<<"$backup" 2>/dev/null || true)
offsite_ok=0
case "$profile:$offsite" in
    production:uploaded | local:uploaded | local:not_configured) offsite_ok=1 ;;
esac
recorded=$(jq -r '.backup_recorded' <<<"$backup" 2>/dev/null || true)
if [ "$recorded" != true ] && [ "${#backup_args[@]}" -gt 0 ] &&
    [ "$(jq -r '.backup_reused' <<<"$backup" 2>/dev/null || true)" = true ]; then
    # Recorded in stewardship-backup.log (above) and said here.
    echo "    REUSED BACKUP: the earlier run's base migration stopped partway; its backup of"
    echo "    $(jq -r '.reused_backup.completed_at' <<<"$backup") (manifest $(jq -r '.reused_backup.manifest_digest' <<<"$backup")) stays this upgrade's restore point"
    recorded=true
fi
if [ "$recorded" != true ] || [ "$offsite_ok" -ne 1 ]; then
    # The EXIT trap restarts the background services (phase background).
    echo "The backup did not complete with its off-host copy; abandoning the $mode." >&2
    if [ "$resumed" = 1 ] && [ "$users_upgrade" = 0 ]; then
        echo "If an earlier run of the users release stopped in its step 4, repeat it with the" >&2
        echo "STEWARDSHIP_USERS_* variables (runbook, Users upgrade)." >&2
    fi
    exit 1
fi

# Step 2: the site is down from here; caddy serves its maintenance page.
step "2. Stopping web"
stopped_at=$(date -u +%s)
phase=stopped
"${dc[@]}" stop web 2>&1 | quiet
still=$("${dc[@]}" ps --services --status running | grep -vxE 'caddy|postgres|valkey' || true)
if [ -n "$still" ]; then
    echo "Online services restarted meanwhile ($still); stopping them too."
    # shellcheck disable=SC2086
    "${dc[@]}" stop $still 2>&1 | quiet
fi

if [ "$mode" = rollback ]; then
    # The decisive run of the check, now that nothing online can change the
    # answer, comes before the retarget: a refusal here still has every
    # document pointing at the current release, so the EXIT trap simply
    # starts it again (phase stopped).
    step "2. Confirming the schema and grants match $image (a rollback never migrates)"
    answer=$(check)
    [ "$answer" = t ] || refuse_rollback "$answer"
    echo "    answer: t"
fi

step "3. Retargeting the image"
phase=offline
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    ${switches[@]+"${switches[@]}"} "$image" retarget-image --config /run/operator.yaml --image "$image"

if [ "$clock" = fake ]; then
    # The retarget re-rendered the faketime override with the derived image
    # names of the target image (the naming rule lives in the code, never
    # here); build whichever derived image is missing, as the VM half's `up`
    # does, before anything runs under the override. A rollback finds its
    # derived images already built. Under a time limit that says what ran,
    # the limit and the elapsed time if it expires.
    step "3. Building the missing fake-clock images for the retargeted override"
    dockerfile="$build/deploy/stewardship/Dockerfile.faketime"
    for service in web postgres valkey; do
        base=$(jq -r --arg s "$service" '.services[$s].image' "$compose")
        derived=$(jq -r --arg s "$service" '.services[$s].image' "$override")
        if docker image inspect "$derived" >/dev/null 2>&1; then
            echo "    $derived (already built)"
            continue
        fi
        [ -f "$dockerfile" ] || { echo "No packed Dockerfile.faketime at $dockerfile; the maintenance page stays up." >&2; exit 1; }
        started=$(date -u +%s)
        rc=0
        timeout 900 docker build --quiet --file "$dockerfile" --build-arg "BASE=$base" \
            --tag "$derived" "$build/deploy/stewardship" >/dev/null || rc=$?
        if [ "$rc" -eq 124 ]; then
            echo "TIMEOUT: building $derived killed after $(( $(date -u +%s) - started ))s (limit 900s)" >&2
            exit 1
        elif [ "$rc" -ne 0 ]; then
            echo "FAILED: building $derived (exit $rc after $(( $(date -u +%s) - started ))s, limit 900s)" >&2
            exit 1
        fi
        echo "    $derived"
    done
fi

if [ "$mode" = upgrade ]; then
    step "4. Migration"
    answer=$(check)
    if [ "$answer" = t ]; then
        echo "    skipped: the upgrade check answered t (schema and grants already match)"
    else
        echo "    running migration and grants: the check answered: $(head -3 <<<"$answer")"
        # Every step-4 command runs under a time limit in a named one-off
        # container (`oneoff`): a migration or conversion that hangs is
        # killed, container and all, the kill is logged and the run stops
        # with the maintenance page up (phase offline). The limits are far
        # above any expected run (seconds to minutes).
        grants() {
            # grants STEP: database-grants as the operator-only provisioning profile.
            oneoff 600 "database-grants" "$1" database-provision database-grants \
                --config "$services/database-provision.yaml" --confirm-deployment "$uuid"
        }
        if [ "$users_upgrade" = 1 ]; then
            # The users upgrade's fixed order (#952): every migration but the
            # swap, the grants its conversion reads with, the preview, the
            # conversion, and then the rest. The admin-recovery commands run
            # on that base, so Admin-access recovery works at every step.
            recovery=(admin-recovery upgrade-users
                --config "$services/admin-recovery.yaml" --confirm-deployment "$uuid")
            step "4. Migration to the users upgrade's base"
            oneoff 1800 "the migration to the users upgrade's base" base-migration migration migrate \
                --config "$services/migration.yaml" --users-upgrade-base
            grants base-grants
            step "4. Users upgrade preview"
            oneoff 600 "the users upgrade preview" users-preview "${recovery[@]}" --preview
            step "4. Users upgrade (operation $STEWARDSHIP_USERS_OPERATION)"
            if ! oneoff 900 "the users upgrade" users-upgrade "${recovery[@]}" \
                --operation-id "$STEWARDSHIP_USERS_OPERATION" \
                --operator-name "$STEWARDSHIP_USERS_OPERATOR" --reason "$STEWARDSHIP_USERS_REASON" \
                --confirm-administrators "$STEWARDSHIP_USERS_ADMINISTRATORS"; then
                echo "The users upgrade did not complete: the new sign-in functions were not installed" >&2
                echo "and, unless the conversion activated before it stopped, the configuration keeps" >&2
                echo "its sign-in rules. Admin-access recovery works on this schema (runbook, Users" >&2
                echo "upgrade). Repeat the run with the same operation to resume it, or with a new" >&2
                echo "one after a refusal." >&2
                exit 1
            fi
            step "4. Migration after the users upgrade"
        fi
        if ! oneoff 1800 "the migration" migration migration; then
            if [ "$users_upgrade" = 0 ]; then
                # A release whose swap refuses an unconverted configuration
                # stops here; nothing of the swap was committed.
                echo "If the release notes announce the users upgrade, repeat the run with the" >&2
                echo "STEWARDSHIP_USERS_* variables (runbook, Users upgrade)." >&2
            fi
            exit 1
        fi
        grants grants
    fi
else
    step "4. Migration: not repeated by a rollback"
fi

step "5. Refreshing the static files in place"
if [ "$profile" = local ]; then
    aside="$root/cache/static.${previous##*:}"
else
    aside="$root/cache/static.${previous:+${previous##*sha256:}}"
fi
[ -n "$previous" ] || aside="$root/cache/static.previous"
# Copy to a temporary name first, so an interrupted copy never leaves a
# half-kept tree under the digest's name for a later rollback to trust.
if ! [ -e "$aside" ]; then
    rm -rf "$aside.tmp"
    cp -a "$root/cache/static" "$aside.tmp"
    mv "$aside.tmp" "$aside"
fi
trap 'echo "Static refresh failed partway. Restore with: find $root/cache/static -mindepth 1 -delete && cp -a $aside/. $root/cache/static/" >&2' ERR
find "$root/cache/static" -mindepth 1 -delete
# BSD find only warns when a delete fails; GNU find exits nonzero. Check the
# tree is really empty before copying over whatever is left.
[ -z "$(find "$root/cache/static" -mindepth 1 -print -quit)" ]
cp -a "$static_src/." "$root/cache/static/"
trap - ERR
rm -rf "$root/cache/static.next"
echo "    replaced tree kept at $aside"

step "6. Starting web"
web_ok=0
"${dc[@]}" up --detach --wait web 2>&1 | quiet && web_ok=1
loaded=$("${dc[@]}" exec -T caddy sha256sum /etc/caddy/Caddyfile 2>/dev/null | cut -d' ' -f1 || true)
if [ "$loaded" != "$(sha256sum "$services/Caddyfile" | cut -d' ' -f1)" ]; then
    echo "    Caddyfile changed: recreating caddy"
    "${dc[@]}" up --detach --wait --force-recreate caddy 2>&1 | quiet || true
else
    "${dc[@]}" up --detach --wait caddy 2>&1 | quiet || true
fi
echo "    web was down for $(( $(date -u +%s) - stopped_at ))s (healthy: $web_ok)"

step "6. Starting the other online services"
# Re-read the list from the re-rendered file: the target release may have
# added or removed a service, and `up` would fail wholesale for a name the
# file no longer has. Checked for emptiness like the first list.
# shellcheck disable=SC2207 # service names hold no whitespace
online=($("${dc[@]}" config --services | grep -vxE 'postgres|valkey' || true))
[ "${#online[@]}" -gt 0 ] || { echo "$(basename "$compose") now lists no online services." >&2; exit 1; }
# shellcheck disable=SC2207
rest=($(printf '%s\n' "${online[@]}" | grep -vxE 'web|caddy' || true))
[ "${#rest[@]}" -eq 0 ] || "${dc[@]}" up --detach --wait "${rest[@]}" 2>&1 | quiet || true

step "6. Checking"
healthy=0
for attempt in $(seq 1 12); do
    if "${dc[@]}" exec -T web pk-stewardship health --config "$services/web.yaml" >/dev/null; then
        healthy=1; break
    fi
    [ "$attempt" -eq 12 ] || sleep 5
done
problems=()
[ "$healthy" -eq 1 ] || problems+=("web health check failing")
states=$("${dc[@]}" ps --all --format json | jq -rs 'flatten | .[] |
    "\(.Service) \(.State) \(if .Health == "" then "none" else .Health end)"')
for service in "${online[@]}"; do
    line=$(awk -v s="$service" '$1 == s' <<<"$states" | head -1)
    case "$line" in
        "$service running healthy" | "$service running none") ;;
        *) problems+=("$service: ${line:-missing}") ;;
    esac
done
# Debug logging off everywhere (launch runbook, Production activation step
# 1); in local mode, the deployment's own setting everywhere.
for id in $("${dc[@]}" ps --quiet); do
    name=$(docker inspect --format '{{.Name}}' "$id")
    value=$(docker inspect --format '{{join .Config.Env "\n"}}' "$id" | grep '^PARISHKIT_DEBUG_LOGGING=' || true)
    case "$value" in ""|"PARISHKIT_DEBUG_LOGGING=$debug") ;; *) problems+=("$name: $value") ;; esac
done
[ "$(jq -r '.services.web.image' "$compose")" = "$image" ] || problems+=("web does not name $image")
if [ "$bulk" = true ]; then
    for role in worker mail-dispatch scheduler; do
        [ "$(jq -r '.deployment.bulk_family_send // false' "$services/$role.yaml")" = true ] ||
            problems+=("$role: bulk Family send is off")
    done
    rendered=$(jq -r '.deployment.bulk_send_batch // 20' "$services/worker.yaml")
    [ "$rendered" = "$batch" ] || problems+=("worker: bulk send batch is $rendered, not $batch")
fi
# Every role's document is rendered from the same inputs; web (which
# enforces the window on pages and downloads), the worker and the
# scheduler (which publish and clean up exports) are checked.
for role in web worker scheduler; do
    rendered=$(jq -r '.deployment.export_download_window_minutes // 60' "$services/$role.yaml")
    [ "$rendered" = "$window" ] || problems+=("$role: export download window is $rendered minutes, not $window")
done
if [ "$profile" = local ]; then
    rendered=$(jq -r '.deployment.local_smtp_latency_ms // 0' "$services/mail-dispatch.yaml")
    [ "$rendered" = "$latency" ] || problems+=("mail-dispatch: SMTP latency is $rendered ms, not $latency")
    rendered=$(jq -r '.deployment.parishsoft_base_url // empty' "$services/worker.yaml")
    [ "$rendered" = "$parishsoft" ] || problems+=("worker: ParishSoft base URL is '${rendered:-the fake}', not '${parishsoft:-the fake}'")
fi
version=$("${dc[@]}" exec -T web python -c 'import parishkit; print(parishkit.__version__)' 2>/dev/null || echo unknown)
echo "    application version: $version"
# Runbook step 6: open the public origin; the in-container probe cannot see
# caddy still answering with its maintenance page. LOCAL's certificate is
# from Caddy's own CA, and before the setup wizard the application answers
# 503 (as caddy's maintenance page does), so there the Server header is
# what shows the application answered through caddy.
if [ "$profile" = local ]; then
    curl -ksSI --max-time 20 "$origin/" 2>/dev/null | grep -qi '^server: gunicorn' ||
        problems+=("public origin $origin does not answer from the application")
else
    curl -fsS -o /dev/null --max-time 20 "$origin/" || problems+=("public origin $origin does not answer")
fi
echo "    worker processes: $("${dc[@]}" top worker 2>/dev/null | grep -c 'runtime' || true)," \
    "mail-dispatch processes: $("${dc[@]}" top mail-dispatch 2>/dev/null | grep -c 'runtime' || true)"
if [ "$mode" = upgrade ]; then done_word=Upgraded; else done_word="Rolled back"; fi
if [ "${#problems[@]}" -gt 0 ]; then
    echo "==> $done_word to $image, but NOT healthy:" >&2
    printf '    %s\n' "${problems[@]}" >&2
    exit 1
fi
step "$done_word to $image in $(( $(date -u +%s) - began ))s; all ${#online[@]} online services healthy"
echo "    Record in the operators' notes. Replaced release: $previous"
