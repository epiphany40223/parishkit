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
#   - the off-site uploader's contract checks (#235: the backup profile's
#     route out, the Engine version, the INPUT drop, the uploader's cron
#     line, a running release with `upload`) only report, never refuse;
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
#     the variable;
#   - census publication (#1884) is rendered on while pk-local reads the
#     fake ParishSoft, the only one it may write to, and off with the real
#     API (the loader refuses it there). A rollback never renders it: a
#     release before #1884 refuses it in the local profile;
#   - the caller may set the forced mail redirect (#1399):
#     PARISHKIT_LOCAL_MAIL_REDIRECT=ADDRESS|off, unset or "keep" carrying the
#     current one over. A rollback never renders it: the previous release
#     may not know it, and without it a real Workspace key is refused, so
#     the rollback fails closed. Production mode never reads the variable.
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
            online)
                echo "==> Failed after web started healthy: $image is serving."
                if [ "$mode" = rollback ]; then
                    echo "    Fix forward with a corrected release (runbook, Rollback)."
                else
                    echo "    Fix forward or roll back (runbook, Rollback)."
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
    # The copy lock's descriptors stay with this shell: a client left
    # behind by a killed shell would otherwise hold the lock until it exits.
    timeout --kill-after=30 "$limit" "${dc[@]}" run --name "$oneoff_name" --rm -T "$@" \
        </dev/null >"$work/oneoff.out" 2>&1 8<&- 9<&- &
    oneoff_pid=$!
    wait "$oneoff_pid" || rc=$?
    oneoff_pid=""
    elapsed=$(( $(date -u +%s) - started ))
    if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ "$elapsed" -ge "$limit" ]; }; then
        if [ "$phase" = online ]; then
            echo "TIMEOUT: $what killed after ${elapsed}s (limit ${limit}s). Web keeps serving." >&3
        else
            echo "TIMEOUT: $what killed after ${elapsed}s (limit ${limit}s). The maintenance page" >&3
            echo "    stays up." >&3
        fi
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

# BEGIN database network layout (#2111). This block is kept identical in
# tools/stewardship-upgrade-host.sh and tools/stewardship-ops/pk-restore
# (tests/stewardship/test_database_layout.py compares the two copies and
# runs it on real Docker). It reads `project`, `compose`, `dc` and `work`.
layout_limited() {
    # layout_limited SECONDS WHAT COMMAND...: run COMMAND under a time limit
    # and print its standard output. On a failure its last error lines are
    # printed too; an expired limit is logged with what ran, the limit and
    # the elapsed time. Returns the command's status.
    local limit=$1 what=$2 started rc=0
    shift 2
    started=$(date -u +%s)
    timeout --kill-after=30 "$limit" "$@" 2>"$work/layout.err" || rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        echo "TIMEOUT: $what killed after $(( $(date -u +%s) - started ))s (limit ${limit}s)" >&2
    elif [ "$rc" -ne 0 ]; then
        echo "FAILED: $what (exit $rc):" >&2
        tail -5 "$work/layout.err" | sed 's/^/    | /' >&2
    fi
    return "$rc"
}
layout_changes() {
    # Print one line per difference between the database networks the
    # Compose file renders and what Docker runs; nothing when they agree.
    # Compared: every rendered network that exists (its internal flag and,
    # when its address block is rendered, its subnet and ip_range, and its
    # gateway when one is rendered: Docker reports the subnet and gateway it
    # chose for a network that renders none), and the set of networks the
    # running postgres container is on. A missing or stopped postgres
    # differs too, so a run that stopped partway repeats the full stop, and
    # so do several postgres containers. A file without a postgres service
    # compares nothing. The inspections go to files, not jq's arguments,
    # where any local user could read them.
    local ids id
    jq -e '.services.postgres' "$compose" >/dev/null 2>&1 || return 0
    echo '[]' >"$work/layout-networks.json"
    echo '[]' >"$work/layout-postgres.json"
    ids=$(layout_limited 60 "listing the project's networks" docker network ls --quiet \
        --filter "label=com.docker.compose.project=$project") || return 1
    if [ -n "$ids" ]; then
        # shellcheck disable=SC2086 # one network id per word
        layout_limited 60 "inspecting the project's networks" docker network inspect $ids \
            >"$work/layout-networks.json" || return 1
    fi
    id=$(layout_limited 60 "finding postgres" docker ps --all --quiet \
        --filter "label=com.docker.compose.project=$project" \
        --filter "label=com.docker.compose.service=postgres" \
        --filter "label=com.docker.compose.oneoff=False") || return 1
    if [ -n "$id" ]; then
        # shellcheck disable=SC2086 # one container id per word
        layout_limited 60 "inspecting postgres" docker inspect $id >"$work/layout-postgres.json" ||
            return 1
    fi
    jq -r --slurpfile have "$work/layout-networks.json" --slurpfile postgres "$work/layout-postgres.json" '
        ($have[0]) as $have | ($postgres[0]) as $postgres
        | ($have | map({key: .Labels["com.docker.compose.network"], value: .}) | from_entries) as $by_key
        | ($have | map({key: .Name, value: .Labels["com.docker.compose.network"]}) | from_entries) as $key_of
        | ((.networks // {}) | to_entries[] | select($by_key[.key] != null)
            | .key as $k | ((.value.ipam.config // [])[0] // {}) as $w
            | ($by_key[$k]) as $got | (($got.IPAM.Config // [])[0] // {}) as $g
            | [ (if $w.subnet != null and $w.subnet != ($g.Subnet // "") then "subnet" else empty end),
                (if $w.subnet != null and ($w.ip_range // "") != ($g.IPRange // "") then "ip_range" else empty end),
                (if $w.gateway != null and $w.gateway != ($g.Gateway // "") then "gateway" else empty end),
                (if (.value.internal // false) != ($got.Internal // false) then "internal" else empty end) ]
            | select(length > 0) | "network \($k) differs from the rendered one (\(join(", ")))"),
          (((.services.postgres.networks // {}) | keys | sort) as $wanted
            | if ($postgres | length) > 1 then
                "postgres has \($postgres | length) containers"
              elif ($postgres | length) == 0 or ($postgres[0].State.Running | not) then
                "postgres is not running"
              else
                (($postgres[0].NetworkSettings.Networks // {}) | keys | map($key_of[.] // .) | sort) as $on
                | if $on != $wanted then
                    "postgres is on \($on | join(" ")) but rendered on \($wanted | join(" "))"
                  else empty end
              end)
    ' "$compose"
}
layout_full_stop() {
    # The full stop for a database network change (operations
    # specification, "Rollout" and "Rollback"). Compose can neither recreate
    # a network a running container is on ("active endpoints") nor attach a
    # stopped container to the recreated one, so every container of the
    # project but caddy is removed (never a volume: every data path is a
    # bind mount), and every network caddy is not on with them. postgres and
    # valkey then start on the rendered networks, which must then match.
    # caddy, only on ingress and proxy, keeps serving the maintenance page.
    # A one-off container still running (a cron backup) is never killed
    # mid-copy: the full stop refuses before stopping anything, and again
    # before the networks go if one started meanwhile. Returns non-zero,
    # having said why, on any failure; repeating it is safe (and the
    # upgrade repeats it before its step 1 when postgres is not running).
    local rows busy ids caddy caddy_networks net name changes
    layout_containers() {
        # The project's containers: id, state, service, one-off flag.
        layout_limited 60 "listing the project's containers" docker ps --all \
            --filter "label=com.docker.compose.project=$project" \
            --format '{{.ID}} {{.State}} {{.Label "com.docker.compose.service"}} {{.Label "com.docker.compose.oneoff"}}'
    }
    layout_busy() {
        # Refuse while a one-off is not finished (running, paused, restarting).
        busy=$(awk '$4 == "True" && $2 != "exited" && $2 != "created" && $2 != "dead" { print $3 }' <<<"$1" |
            sort -u | tr '\n' ' ')
        [ -z "$busy" ] && return 0
        echo "One-off containers of $project are still running (${busy% }); $2 Repeat once they finish." >&2
        return 1
    }
    rows=$(layout_containers) || return 1
    layout_busy "$rows" "nothing was stopped." || return 1
    # Through Compose, for their own stop grace periods (PostgreSQL's
    # shutdown checkpoint); the rest stopped already or stop in a minute.
    layout_limited 600 "stopping postgres and valkey" "${dc[@]}" stop postgres valkey >/dev/null || return 1
    ids=$(awk '$3 != "caddy" { print $1 }' <<<"$rows")
    if [ -n "$ids" ]; then
        # The online services stopped already (the upgrade's steps 1 and
        # 2, pk-restore's step 1), so their own longer grace periods do
        # not matter here; this stops only what is left.
        # shellcheck disable=SC2086 # one container id per word
        layout_limited 300 "stopping the other containers" docker stop --time 60 $ids >/dev/null || return 1
        # shellcheck disable=SC2086
        layout_limited 120 "removing the containers" docker rm $ids >/dev/null || return 1
    fi
    rows=$(layout_containers) || return 1
    layout_busy "$rows" "postgres and valkey are stopped." || return 1
    # caddy's own networks stay, read from caddy itself so a stopped caddy
    # (pk-restore stops it) keeps them too; Compose recreates the others.
    caddy_networks=" "
    caddy=$(awk '$3 == "caddy" { print $1 }' <<<"$rows")
    for caddy in $caddy; do
        # shellcheck disable=SC2016 # a Go template, not a shell expansion
        caddy_networks+=$(layout_limited 60 "inspecting caddy" docker inspect \
            --format '{{range $name, $n := .NetworkSettings.Networks}}{{$name}} {{end}}' "$caddy") ||
            return 1
    done
    ids=$(layout_limited 60 "listing the project's networks" docker network ls --quiet \
        --filter "label=com.docker.compose.project=$project") || return 1
    for net in $ids; do
        name=$(layout_limited 60 "inspecting network $net" docker network inspect --format '{{.Name}}' "$net") ||
            return 1
        [[ $caddy_networks == *" $name "* ]] && continue
        layout_limited 60 "removing network $name" docker network rm "$net" >/dev/null || return 1
    done
    layout_limited 600 "starting postgres and valkey" "${dc[@]}" up --detach --wait postgres valkey >/dev/null ||
        return 1
    changes=$(layout_changes) || return 1
    if [ -n "$changes" ]; then
        echo "After the full stop the database networks still differ from the rendered ones:" >&2
        printf '%s\n' "$changes" | sed 's/^/    /' >&2
        return 1
    fi
}
# END database network layout (#2111).

# BEGIN PostgreSQL source rules check (#2111). This block is kept identical
# in tools/stewardship-upgrade-host.sh and tools/stewardship-ops/pk-restore
# (tests/stewardship/test_database_layout.py compares the two copies and
# runs it on real Docker). It reads `compose`, `dc`, `database`, `isolated`,
# `image`, `root`, `yaml` and `work`, prints what failed on stderr, and
# leaves what to say about the site to its caller. Operations
# specification, "When PostgreSQL reads the rules": PostgreSQL reads its
# hba_file only when it starts or reloads, so after a retarget wrote the
# rendered file the rules are reloaded as the operator over the socket,
# and the hba check (rendered by the target image, run read-only) must
# answer t: the mounted path is in force, the mounted file is the rendered
# one, it parses, and it was loaded after it was written.
hba_rendered() {
    # The hba_file setting the Compose file starts PostgreSQL with, or
    # nothing when it renders none (a release before the source rules).
    jq -r '.services.postgres.command // [] | map(select(startswith("hba_file="))) | .[0] // empty' "$compose"
}
hba_sql() {
    # hba_sql SECONDS WHAT MODE [PSQL ARGS...]: psql as the operator in the
    # running postgres service under a time limit, in a read-only session
    # when MODE is read-only, with this shell's stdin as its input unless
    # the arguments give commands; prints the output. An expired limit is
    # logged on stderr (what, limit, elapsed) and fails.
    local limit=$1 what=$2 started rc=0 out
    local options=()
    [ "$3" != read-only ] || options=(-e PGOPTIONS='-c default_transaction_read_only=on')
    shift 3
    started=$(date -u +%s)
    out=$(timeout --kill-after=10 "$limit" "${dc[@]}" exec -T ${options[@]+"${options[@]}"} postgres \
        psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 "$@" 2>&1) || rc=$?
    if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ $(( $(date -u +%s) - started )) -ge "$limit" ]; }; then
        echo "TIMEOUT: $what killed after $(( $(date -u +%s) - started ))s (limit ${limit}s)" >&2
    fi
    printf '%s\n' "$out"
    return "$rc"
}
hba_render() {
    # Render the hba check's query with the target image into
    # $work/hba-check.sql: offline, read-only, the root mounted read-only.
    # A time limit like every other step; an expiry is logged.
    local started rc=0
    started=$(date -u +%s)
    timeout --kill-after=10 300 "${isolated[@]}" \
        --mount "type=bind,source=$root,target=$root,readonly" \
        --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
        "$image" hba-check --config /run/operator.yaml >"$work/hba-check.sql" || rc=$?
    if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ $(( $(date -u +%s) - started )) -ge 300 ]; }; then
        echo "TIMEOUT: rendering the hba check killed after $(( $(date -u +%s) - started ))s (limit 300s)" >&2
    fi
    return "$rc"
}
hba_answer() {
    # hba_answer SINCE: the check's answer, read-only; SINCE is the
    # PostgreSQL time the load must be later than.
    hba_sql 10 "the hba check" read-only -v since="$1" <"$work/hba-check.sql" || true
}
hba_why() {
    # hba_why ANSWER: the failed condition, in plain words.
    case "$1" in
        hba_file_not_mounted) echo "PostgreSQL does not use the mounted rules (it was not recreated with the new command)" ;;
        hba_file_differs) echo "the mounted pg_hba.conf differs from the rendered one" ;;
        hba_file_invalid) echo "pg_hba.conf has errors, so PostgreSQL keeps its previous rules" ;;
        hba_reload_pending) echo "PostgreSQL has not loaded the rules since they were last written" ;;
        *) echo "the hba check did not answer: $(head -3 <<<"$1")" ;;
    esac
}
hba_reload() {
    # Reload the rendered rules and wait until the check answers t. Sets
    # hba_since, PostgreSQL's time just before the reload, so a later
    # `hba_answer "$hba_since"` proves the same rules are still in force.
    # Prints what failed on stderr and returns 1 otherwise.
    local answer reload started elapsed=0 attempt
    # Before the reload, the file must already be the rendered one and
    # parse (pg_hba_file_rules reads the file on disk): only the load may
    # be pending, which it always is against `infinity`. A reload of a bad
    # file would keep the old rules anyway.
    answer=$(hba_answer infinity)
    if [ "$answer" != hba_reload_pending ]; then
        echo "The hba check failed: $(hba_why "$answer")." >&2
        return 1
    fi
    # The moment the load must follow, read from PostgreSQL's own clock just
    # before the signal (a LOCAL fake clock shifts both alike); the file was
    # written before it, by the retarget.
    reload=$(hba_sql 30 "the PostgreSQL configuration reload" write \
        -c "SELECT clock_timestamp()" -c "SELECT pg_reload_conf()") ||
        { echo "Could not reload PostgreSQL's configuration." >&2; return 1; }
    hba_since=$(head -1 <<<"$reload")
    # pg_reload_conf() only signals the postmaster: wait, bounded, for the
    # load to take effect, polling about once a second for at most 30
    # seconds (and at most 30 polls).
    started=$(date -u +%s)
    for attempt in $(seq 1 30); do
        answer=$(hba_answer "$hba_since")
        elapsed=$(( $(date -u +%s) - started ))
        if [ "$answer" != hba_reload_pending ] || [ "$attempt" -ge 30 ] || [ "$elapsed" -ge 30 ]; then
            break
        fi
        sleep 1
    done
    case "$answer" in
        t) echo "    the rendered source rules are in force (the reload took ${elapsed}s)" ;;
        hba_reload_pending)
            echo "TIMEOUT: the wait for the hba reload stopped after ${elapsed}s (limit 30s)" >&2
            return 1 ;;
        *)
            echo "The hba check failed: $(hba_why "$answer")." >&2
            return 1 ;;
    esac
}
# END PostgreSQL source rules check (#2111).
# BEGIN host crontab collection (#235). This block is kept identical in
# tools/stewardship-upgrade-host.sh and tools/stewardship-ops/pk-restore
# (tests/stewardship/test_database_layout.py compares the two copies).
cron_tagged() {
    # Every uncommented line of the crontabs this host's jobs live in, each
    # after `u ` (root's and SUDO_USER's crontabs: no user field) or `s `
    # (/etc/crontab and the /etc/cron.d files cron reads: a user field);
    # PK_RESTORE_CRONTAB and PK_RESTORE_CRON_DIR for tests. cron skips a
    # cron.d file whose name has anything but letters, digits, `_` and `-`
    # (a dot, as in `stewardship.bak` or `.placeholder`), so this does too.
    # Scripts in /etc/cron.{hourly,...}, anacron, `at` jobs and other
    # users' crontabs are not read. awk ends every file's last line.
    local file
    {
        {
            crontab -l -u root 2>/dev/null || true
            [ -z "${SUDO_USER:-}" ] || crontab -l -u "$SUDO_USER" 2>/dev/null || true
        } | awk '{ print "u " $0 }'
        awk '{ print "s " $0 }' "${PK_RESTORE_CRONTAB:-/etc/crontab}" 2>/dev/null || true
        for file in "${PK_RESTORE_CRON_DIR:-/etc/cron.d}"/*; do
            case ${file##*/} in *[!A-Za-z0-9_-]*) continue ;; esac
            [ ! -f "$file" ] || awk '{ print "s " $0 }' "$file" 2>/dev/null || true
        done
    } | grep -v '^. [[:space:]]*#' || true
}
cron_lines() {
    # cron_tagged's lines as the crontabs hold them.
    cron_tagged | cut -c3-
}
uploader_cron_lines() {
    # The cron lines (of cron_tagged) that run the off-site uploader, as the
    # backup runbook's "The uploader's cron line" gives it: the schedule
    # (five fields or an @keyword), the user field in /etc/crontab and
    # cron.d, then `docker compose` (docker by name or by path), compose's
    # own options, `run`, run's options, the service `backup-uploader`, and
    # nothing after it but its `upload` command and its arguments, up to a
    # redirection or the shell's `;`, `&` or `|`. A `#` starting a word
    # ends the command (the shell's comment), so a name in a trailing
    # comment is not a match; nor is any other command, wrapper or service
    # that merely contains the name, nor `--entrypoint` (another command in
    # the uploader's image). Also never a match, since the uploader would not
    # run (#235): a line with a `%` anywhere (cron ends the command there and
    # feeds the rest as input), compose's `--dry-run`, and a `run` option
    # this does not know, so that an option's value (`--network
    # backup-uploader`) is never taken for the service; a known option that
    # takes a value consumes the next word, so a service must still follow.
    # Lines are printed as the crontabs hold them.
    cron_tagged | awk '
        function docker(word) { return word == "docker" || word ~ /\/docker$/ }
        {
            line = substr($0, 3)
            if (index(line, "%")) next
            command = line
            sub(/^[ \t]+/, "", command)
            sub(/[ \t]#.*/, "", command)
            n = split(command, word, /[ \t]+/)
            at = (word[1] ~ /^@/) ? 2 : 6
            if (substr($0, 1, 1) == "s") at++
            if (!docker(word[at]) || word[at + 1] != "compose") next
            for (i = at + 2; i <= n && word[i] ~ /^-/; i++) {
                if (word[i] ~ /^--dry-run(=|$)/) next
                if (word[i] ~ /^(-f|-p|--file|--project-name|--project-directory|--env-file|--profile|--ansi|--progress|--parallel)$/) i++
            }
            if (word[i] != "run") next
            for (i++; i <= n && word[i] ~ /^-/; i++) {
                if (word[i] ~ /^(--entrypoint|--dry-run)/) next
                if (word[i] ~ /^(-e|--env|--env-from-file|--name|-u|--user|-w|--workdir|-l|--label|-v|--volume|-p|--publish|--cap-add|--cap-drop|--pull)$/) i++
                else if (word[i] !~ /^(--rm|-T|--no-TTY|-d|--detach|-i|--interactive|--no-deps|--build|-P|--service-ports|--use-aliases|--remove-orphans|--quiet|--quiet-build|--quiet-pull|--(env|env-from-file|name|user|workdir|label|volume|publish|cap-add|cap-drop|pull)=.*)$/) next
            }
            if (word[i] != "backup-uploader") next
            i++
            if (i <= n && word[i] != "upload" && word[i] !~ /^([0-9]*[<>]|&>|[;&|])/) next
            print line
        }' || true
}
# END host crontab collection (#235).
# BEGIN off-site copy lock (#235). This block is kept identical in
# tools/stewardship-upgrade-host.sh and tools/stewardship-ops/pk-restore
# (tests/stewardship/test_database_layout.py compares the two copies).
copy_lock_same() {
    # copy_lock_same FD PATH: whether open descriptor FD is PATH itself, by
    # device and inode, PATH's final component not followed (GNU stat).
    local open named
    open=$(stat -L -c %d:%i "/dev/fd/$1" 2>/dev/null) &&
        named=$(stat -c %d:%i -- "$2" 2>/dev/null) &&
        [ -n "$open" ] && [ "$open" = "$named" ]
}
copy_lock_take() {
    # copy_lock_take DIR SECONDS: hold this host's off-site copy lock on the
    # backups directory DIR for the rest of the run (operations
    # specification, "Offline work"), so an upload still running finishes
    # first and a cron uploader exits at the lock. Since the contract
    # release every copier takes the directory lock alone (the expand
    # release's legacy .offsite-copy.lock file is no longer locked, and a
    # leftover one is ignored), so this takes it with `flock -w` on a
    # descriptor (9) this shell keeps open until it exits.
    #
    # The name may still change between the check and the open, so the
    # open descriptor must be the directory at that name, by device and
    # inode: a link or anything else swapped in meanwhile refuses instead
    # of being locked.
    #
    # A wait longer than SECONDS is logged with what, the limit and the
    # elapsed time (flock's exit 75); any other flock failure is logged as a
    # failure with its status. Either returns non-zero, as does every other
    # refusal.
    local dir=$1 limit=$2 started rc
    command -v flock >/dev/null || { echo "flock is not installed; cannot take the off-site copy lock." >&2; return 1; }
    { [ -d "$dir" ] && [ ! -L "$dir" ]; } ||
        { echo "The backups directory $dir is not a directory." >&2; return 1; }
    if ! exec 9<"$dir"; then
        echo "Could not open the copy lock on $dir." >&2
        return 1
    fi
    if ! { [ -d /dev/fd/9 ] && copy_lock_same 9 "$dir"; }; then
        exec 9<&-
        echo "The backups directory $dir is not a directory." >&2
        return 1
    fi
    started=$(date -u +%s)
    rc=0
    flock -E 75 -w "$limit" 9 || rc=$?
    if [ "$rc" -eq 75 ]; then
        echo "TIMEOUT: waiting for the off-site copy lock on $dir gave up after $(($(date -u +%s) - started))s (limit ${limit}s)" >&2
        return 1
    elif [ "$rc" -ne 0 ]; then
        echo "FAILED: flock on the off-site copy lock on $dir exited with status $rc after $(($(date -u +%s) - started))s" >&2
        return 1
    fi
}
# END off-site copy lock (#235).

# The offline phase holds the off-site copy lock (after step 1, below) with
# util-linux flock; refuse now, before step 1's backup and copy, as
# pk-restore does, rather than find it missing hours later.
command -v flock >/dev/null || { echo "flock is not installed (util-linux); refusing." >&2; exit 1; }

# The project keeps whichever Compose file it already runs under; an upgrade
# or a rollback changes images and the documents rendered for them (once,
# #2111, the database networks too: the full stop after step 3).
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
if [ "$resumed" = 1 ]; then
    # A run whose full stop (after step 3, below) failed partway left
    # postgres stopped or removed, or on networks the current file does not
    # render; step 1's check and backup need it. The file already names
    # that run's target, so finishing its full stop first is what that run
    # would have done (phase stopped: web is down, and on a failure
    # nothing is started).
    phase=stopped
    changes=$(layout_changes) ||
        { echo "Could not compare the database networks with what Docker runs; refusing." >&2; exit 1; }
    if [ -n "$changes" ]; then
        step "Finishing the full stop an earlier run began (the database networks change)"
        printf '%s\n' "$changes" | sed 's/^/    /'
        layout_full_stop ||
            { echo "The full stop did not finish; caddy serves the maintenance page. Fix the cause and repeat." >&2; exit 1; }
    fi
fi

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
    # Census publication (#1884): Update ParishSoft writes to the fake only.
    # With the real API it stays off, and the loader would refuse it anyway.
    publication=false
    if [ -z "$parishsoft" ]; then
        if [ "$mode" = rollback ]; then
            echo "    Update ParishSoft is not carried into a rollback (the previous release may refuse it); the next deploy turns it on again"
        else
            publication=true
            switches+=(-e "PARISHKIT_STEWARDSHIP_PARISHSOFT_PUBLICATION=1")
            echo "    Update ParishSoft writes to the fake ParishSoft only"
        fi
    fi
    # The forced mail redirect (#1399): one bare address, the developer's
    # Testing recipient. With it, a real Workspace key may send, but every
    # message goes only to this address. Checked here with a plain pattern,
    # before anything stops; the loader applies the full rule at the
    # retarget.
    redirect=$(jq -r '.deployment.mail_redirect // empty' "$services/worker.yaml")
    case "${PARISHKIT_LOCAL_MAIL_REDIRECT:-keep}" in
        keep) ;;
        off) redirect="" ;;
        *) redirect=$PARISHKIT_LOCAL_MAIL_REDIRECT ;;
    esac
    if [ -n "$redirect" ] && ! [[ $redirect =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]]; then
        echo "The mail redirect must be one plain email address; refusing." >&2
        exit 1
    fi
    if [ "$mode" = rollback ] && [ -n "$redirect" ]; then
        echo "    the mail redirect is not carried into a rollback (a real Workspace key is then refused); deploy --mail-redirect sets it again"
        redirect=""
    fi
    if [ -n "$redirect" ]; then
        switches+=(-e "PARISHKIT_STEWARDSHIP_MAIL_REDIRECT=$redirect")
        echo "    every message goes only to the mail redirect $redirect"
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
# An inspection login that owns objects answers a sentence naming them and
# their clean-up (#2291) instead of f. database-grants would refuse it in
# step 4, after everything stopped, so refuse now, whatever the override.
case "$advisory" in
    "The inspection login owns objects"*)
        echo "The inspection login owns objects; clean them up before upgrading:" >&2
        echo "see $runbook#clean-up-when-the-inspection-login-owns-objects." >&2
        exit 1
        ;;
esac
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
        # parameter privilege (#845)) or an excess grant (CREATE among
        # them). The services' identity check would refuse that. It also
        # answers f for an operator outside pg_catalog that is not pg_trgm's
        # (#2166). An inspection login that owns objects was refused above.
        echo "Or the database has drifted from its provisioned grants or login isolation" >&2
        echo "(a member role, an owned object, foreign-data USAGE, a parameter privilege," >&2
        echo "an excess grant), which the services' identity check or database-grants" >&2
        echo "would refuse, or has an operator outside pg_catalog that is not pg_trgm's:" >&2
        echo "find and repair that drift before upgrading; see $runbook#upgrade." >&2
        if [ "$profile" = local ]; then
            echo "If this pull request changes the schema or a grant, re-run as 'deploy --schema-change'." >&2
        else
            echo "Read the release notes; set STEWARDSHIP_SCHEMA_CHANGE=1 only if they announce it." >&2
        fi
        exit 1
    fi
fi

# The digest retirement (#1834, frozen file 0171) needs the report email
# conversion done first: its migration (stewardship_reports.0041, frozen
# file 0161) applied and `upgrade-reports` run, so that no schedule
# definition is still a digest. On a database without it, step 4 would
# commit 0161 through 0170 and then stop at 0171, leaving a restore as the
# only way back, so refuse here instead, before anything stops. The image
# answers whether it has the file (test exits 0 or 1; anything else is a
# failure to ask); the database, in a read-only session, whether it is
# converted.
if [ "$mode" = upgrade ]; then
    retirement=/app/src/parishkit/stewardship/schema/migrations/0171_retire_digests.sql
    rc=0
    "${isolated[@]}" --entrypoint test "$image" -f "$retirement" || rc=$?
    if [ "$rc" -gt 1 ]; then
        echo "Could not ask $image whether it retires the digests (exit $rc); refusing." >&2
        exit 1
    fi
    if [ "$rc" -eq 0 ]; then
        converted=$("${dc[@]}" exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres \
            psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 -c \
            "SELECT EXISTS (SELECT 1 FROM public.django_migrations WHERE app='stewardship_reports' AND name='0041_report_conversion') AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_definition WHERE kind IN ('daily_digest','weekly_digest'))" \
            2>&1 || true)
        if [ "$converted" != t ]; then
            echo "This release retires the Daily and Weekly digests (migration 0171), but the" >&2
            echo "database has not finished the report email conversion: the conversion's" >&2
            echo "migration is not applied or a schedule definition is still a digest (answer:" >&2
            echo "$(head -3 <<<"$converted"))." >&2
            echo "Nothing was stopped or changed. Deploy the release that converts the digests" >&2
            echo "first (its run includes pk-stewardship upgrade-reports), then this one; see" >&2
            echo "$runbook (Report email conversion)." >&2
            if [ "$profile" = local ]; then
                # pk-local has no releases, only commits to deploy.
                echo "Locally: deploy a commit that converts the digests but does not retire them" >&2
                echo "(it has frozen file 0161 but not 0171), then this one again." >&2
            fi
            exit 1
        fi
    fi
fi

# The command-list probe (#235): one bounded `--help` capture per image, read
# by has_command() as pk-restore reads its own. The new image's list says
# whether it has the off-site uploader (it lists uploader-password-check,
# which no release before the uploader has; `smoke` cannot be the marker, as
# every image lists it). The running image's list is what a later release's
# step 1 keys its uploader run on. An image that does not answer (gone, or
# no list within the limit) refuses before anything stops. That holds for
# the running image too, although in this release its list only drives a
# message: the design (operations specification, "Scripted upgrades", *The
# command probe*) requires the refusal, since a later release's step 1
# runs that image's uploader. A pruned or unpullable running image
# therefore refuses the upgrade; pull it again, or restore it, and repeat.
command_list() {
    # Prints $1's command list; fails when it does not answer with one.
    local out rc=0 started
    started=$(date -u +%s)
    out=$(timeout --kill-after=10 60 "${isolated[@]}" "$1" --help 2>/dev/null) || rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        echo "TIMEOUT: listing the commands of $1 killed after $(( $(date -u +%s) - started ))s (limit 60s)" >&3
    fi
    [ "$rc" -eq 0 ] || return 1
    # Every release lists retarget-image; anything else is not a command list.
    grep -qE '[{,]retarget-image[,}]' <<<"$out" || return 1
    printf '%s\n' "$out"
}
has_command() { grep -qE "[{,]$2[,}]" <<<"$1"; }
new_commands=$(command_list "$image") ||
    { echo "Could not list the commands of $image; refusing." >&2; exit 1; }
running_commands=""
if [ -n "$previous" ] && [ "$previous" != "$image" ]; then
    running_commands=$(command_list "$previous") ||
        { echo "Could not list the commands of the running image $previous; refusing." >&2; exit 1; }
else
    running_commands=$new_commands
fi
if has_command "$running_commands" uploader-password-check; then
    echo "    the running image has the off-site uploader"
fi
# The off-site uploader's cron line (#235) runs the backup-uploader service
# every five minutes. An image without the `upload` command (a rollback past
# the expand release, or an image between its two parts) renders no such
# service or cannot run it, so the line would fail every five minutes and
# the backup profile's own copy is the copier again: it must be commented
# out first. Refused here, before anything stops (backup runbook, "Rolling
# back the expand release").
if ! has_command "$new_commands" upload; then
    uploader_cron=$(uploader_cron_lines)
    if [ -n "$uploader_cron" ]; then
        echo "$image has no off-site uploader, but this host's cron still runs it:" >&2
        while read -r line; do echo "    | $line"; done <<<"$uploader_cron" >&2
        echo "Nothing was stopped or changed. Comment out the uploader's cron line first" >&2
        echo "(docs/guides/stewardship-backup-runbook.md#rolling-back-the-expand-release)," >&2
        echo "then repeat." >&2
        exit 1
    fi
fi

# The backup profile's route out (#235, operations specification, "No route
# out"). From the contract release on (the new image lists egress-probe) the
# backup worker, which reads every tree and all database data, is on its own
# internal network alone and the off-site uploader alone copies. Before
# anything stops, every such upgrade or rollback checks on Production:
# - the running image has the uploader's `upload` (the expand release), so
#   no copier still running holds only the expand release's legacy file
#   lock, which this release no longer takes;
# - this host's cron runs the uploader: the shared crontab block's
#   uploader_cron_lines prints at least one line that runs the
#   `backup-uploader` service through `docker compose ... run` (its strict
#   match: no wrapper, comment, other command or file cron skips counts),
#   or copies would stop with this release. The lines are logged as
#   evidence;
# - Docker Engine has the embedded-DNS fix for internal networks
#   (CVE-2024-29018: 26.0.0, 25.0.5 or 23.0.11 and later; any 24.x refused);
# - the host INPUT drop for runtime_network.offline is in place AHEAD of
#   every rule that could admit that block (iptables -S INPUT), and its
#   systemd unit (the backup runbook's one persistence procedure) is
#   enabled for that block: the gateway of every one-shot network is the
#   host, and Caddy's published 80/443 listen on its wildcard address, as
#   may sshd and any UDP service, whose silence alone proves nothing;
# - the new image's egress-probe, in a container joined only to the
#   existing backup-worker network, reaches nothing: external TCP, DNS
#   through the embedded resolver and straight to a public one, every
#   port the host listens on at a wildcard address or that gateway, and
#   TCP 80 and 443 there always.
# Every failure to ask refuses too. LOCAL (pk-local) has no off-site target
# and no host firewall rule, so there the same checks only report.
engine_fixed() {
    # Whether Docker Engine version $1 has the CVE-2024-29018 fix: 26.0.0
    # or later, 25.0.5 or a later 25, 23.0.11 or a later 23. Only a `+`
    # suffix (build metadata, such as a distribution's `+dfsg1`) may follow
    # the three numbers: a `-` suffix is a pre-release in semantic
    # versioning (-rc1, -RC1, -beta.1, -dev) and `~` is how Debian packages
    # spell one (~rc1, ~beta), so either refuses, whatever its case.
    local major minor patch
    [[ $1 =~ ^([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})(\+[0-9A-Za-z.+]*)?$ ]] || return 1
    major=$((10#${BASH_REMATCH[1]})) minor=$((10#${BASH_REMATCH[2]})) patch=$((10#${BASH_REMATCH[3]}))
    if [ "$major" -ge 26 ]; then return 0; fi
    case "$major" in
        25) [ "$minor" -gt 0 ] || [ "$patch" -ge 5 ] ;;
        23) [ "$minor" -gt 0 ] || [ "$patch" -ge 11 ] ;;
        *) return 1 ;;
    esac
}
input_drop_unit=stewardship-offline-input-drop.service
input_drop_first() {
    # input_drop_first BLOCK: whether `iptables -S INPUT` on stdin has the
    # one-shot networks' drop, exactly `-A INPUT -s BLOCK -j DROP`, ahead of
    # every rule that could admit traffic from BLOCK. A drop in the filter
    # table's INPUT chain is final for that packet whatever other tables or
    # nftables chains do, so only the rules before it matter. An earlier
    # rule cannot admit BLOCK when it drops or rejects, has no target (it
    # only counts), matches loopback only (-i lo) or a source that does not
    # overlap BLOCK. Anything else before it (an ACCEPT, a jump to ufw's or
    # another chain, a RETURN, a negated match) is printed and fails, since
    # proving what such a rule admits would mean following chains: insert
    # the drop first instead (the runbook's unit does). Quoted strings (rule
    # comments) are blanked first, so a comment cannot look like a match.
    awk -v block="$1" '
        function ip(a,   p) { split(a, p, "."); return ((p[1] * 256 + p[2]) * 256 + p[3]) * 256 + p[4] }
        function overlaps(cidr,   c, len, size) {
            split(cidr, c, "/")
            len = (c[2] == "" ? 32 : c[2] + 0)
            if (len > blen) len = blen
            size = 2 ^ (32 - len)
            return int(ip(c[1]) / size) == int(start / size)
        }
        BEGIN { split(block, b, "/"); blen = b[2] + 0; start = ip(b[1]) }
        $1 != "-A" || $2 != "INPUT" { next }
        $0 == "-A INPUT -s " block " -j DROP" { found = 1; exit }
        {
            rule = $0
            gsub(/"[^"]*"/, "\"\"")
            target = ""
            harmless = 0
            for (i = 3; i <= NF; i++) {
                negated = ($(i - 1) == "!")
                if ($i == "-j" || $i == "-g") target = $(i + 1)
                if ($i == "-i" && $(i + 1) == "lo" && !negated) harmless = 1
                if ($i == "-s" && !negated && !overlaps($(i + 1))) harmless = 1
            }
            if (target == "" || target == "DROP" || target == "REJECT") harmless = 1
            if (!harmless) { print rule; admits = 1 }
        }
        END { exit !(found && !admits) }
    '
}
input_drop_persisted() {
    # Whether the runbook's unit is enabled and its ExecStart inserts the
    # drop for BLOCK ($1, already checked to be digits, dots and a prefix
    # length) first, so the rule comes back ahead of ufw's at every boot.
    local unit
    layout_limited 30 "asking systemd whether $input_drop_unit is enabled" \
        systemctl is-enabled --quiet "$input_drop_unit" || return 1
    unit=$(layout_limited 30 "reading $input_drop_unit" systemctl cat "$input_drop_unit") || return 1
    grep -qE "^ExecStart=[^#]* -I INPUT 1 -s ${1//./\\.} -j DROP\$" <<<"$unit"
}
gateway_listeners() {
    # gateway_listeners GATEWAY: the host's listening TCP and UDP ports at a
    # wildcard address or at GATEWAY, from `ss -Htlnu` on stdin, as one
    # comma-separated tcp:PORT,udp:PORT list. TCP 80 and 443 are always in
    # it: Caddy's ports are published there, and with Docker's userland
    # proxy turned off a published port has no listener for `ss` to show
    # (Docker forwards it by NAT instead, which the INPUT drop does not see).
    awk -v gw="$1" '
        BEGIN { seen["tcp:80"] = 1; seen["tcp:443"] = 1 }
        {
            listening = $5
            n = split(listening, part, ":")
            port = part[n]
            address = substr(listening, 1, length(listening) - length(port) - 1)
            sub(/%.*/, "", address)
            if (port !~ /^[0-9]+$/) next
            if (address == "0.0.0.0" || address == "*" || address == "[::]" || address == gw)
                seen[$1 ":" port] = 1
        }
        END { for (item in seen) print item }
    ' | sort -u | paste -sd, -
}
if has_command "$new_commands" egress-probe; then
    step "1. Checking that the backup profile has no route out"
    contract_problems=()
    if ! has_command "$running_commands" upload; then
        contract_problems+=("The running image ${previous:-unknown} has no off-site uploader: deploy the expand release (the one that adds 'upload') first, prove the uploader and install its cron line (docs/guides/stewardship-backup-runbook.md#the-uploaders-cron-line), then this one.")
    fi
    uploader_cron=$(uploader_cron_lines)
    if [ -z "$uploader_cron" ]; then
        contract_problems+=("This host's cron runs no off-site uploader line, so off-site copies would stop with this release: install it first, in the form the runbook gives (docs/guides/stewardship-backup-runbook.md#the-uploaders-cron-line).")
    else
        echo "    this host's cron runs the off-site uploader:"
        while read -r line; do echo "    | $line"; done <<<"$uploader_cron"
    fi
    # Every question below is bounded and an expired limit logged (what,
    # the limit, the elapsed time) by layout_limited; a failure to answer
    # refuses like a wrong answer.
    engine=$(layout_limited 30 "asking Docker Engine for its version" \
        docker version --format '{{.Server.Version}}') || engine=""
    if engine_fixed "$engine"; then
        echo "    Docker Engine ${engine} has the embedded-DNS fix for internal networks"
    else
        contract_problems+=("Docker Engine '${engine:-unknown}' lacks the embedded-DNS fix for internal networks (CVE-2024-29018): upgrade it to 26.0.0 or later (or 25.0.5 / 23.0.11), with the Administrator's approval (docs/guides/stewardship-backup-runbook.md#upgrading-docker-engine).")
    fi
    offline=$(jq -r '.deployment.runtime_network.offline // "172.29.242.0/24"' "$services/web.yaml")
    [[ $offline =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/(24|25)$ ]] ||
        { echo "Could not read runtime_network.offline ($offline); refusing." >&2; exit 1; }
    drop_help="install it with the runbook's unit, with the Administrator's approval (docs/guides/stewardship-backup-runbook.md#the-hosts-input-drop)"
    if ! input_chain=$(layout_limited 30 "listing the host's INPUT chain" iptables -w 5 -S INPUT); then
        contract_problems+=("Could not list the host's INPUT chain (iptables -S INPUT) to find the one-shot networks' drop ($offline).")
    elif ! admitting=$(input_drop_first "$offline" <<<"$input_chain"); then
        if [ -n "$admitting" ]; then
            contract_problems+=("The host's INPUT drop for the one-shot networks ($offline) is missing or comes after a rule that could admit them: $(paste -sd';' - <<<"$admitting"). Put it first: sudo systemctl restart $input_drop_unit, or $drop_help.")
        else
            contract_problems+=("The host has no INPUT drop for the one-shot networks ($offline): $drop_help.")
        fi
    elif ! input_drop_persisted "$offline"; then
        contract_problems+=("The host's INPUT drop for the one-shot networks ($offline) is not persisted: $input_drop_unit is not enabled or does not insert that block first; $drop_help.")
    else
        echo "    the host drops traffic from the one-shot networks ($offline) to itself, ahead of any rule that could admit them, and $input_drop_unit keeps it"
    fi
    probe_network="${project}_offline-backup-worker"
    internal=$(layout_limited 30 "inspecting $probe_network" \
        docker network inspect --format '{{.Internal}}' "$probe_network") || internal=""
    gateway=$(layout_limited 30 "reading the gateway of $probe_network" docker network inspect \
        --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' "$probe_network") || gateway=""
    google=$(layout_limited 30 "resolving www.googleapis.com" getent ahostsv4 www.googleapis.com |
        awk 'NR == 1 { print $1 }') || google=""
    if sockets=$(layout_limited 30 "listing the host's listening ports" ss -Htlnu); then
        listeners=$(gateway_listeners "$gateway" <<<"$sockets")
    else
        listeners="unreadable"
    fi
    if [ "$internal" != true ] || ! [[ $gateway =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        contract_problems+=("Could not read the backup worker's internal network $probe_network (internal: ${internal:-unknown}, gateway: ${gateway:-unknown}); run one backup on the current release, then repeat.")
    elif ! [[ $google =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        contract_problems+=("The host could not resolve www.googleapis.com for the egress probe.")
    elif [ "$listeners" = unreadable ]; then
        contract_problems+=("Could not list the host's listening ports (ss -Htlnu) for the egress probe.")
    else
        oneoff_name=$oneoff_prefix-egress-probe
        started=$(date -u +%s)
        probe_rc=0
        # shellcheck disable=SC2054 # the tmpfs options are one comma-separated word
        probed=$(timeout --kill-after=10 120 docker run --rm --init --name "$oneoff_name" \
            --network "$probe_network" --user 10001:10001 --read-only --cap-drop ALL \
            --security-opt no-new-privileges:true \
            --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
            "$image" egress-probe --gateway "$gateway" --google-address "$google" \
            --listen "$listeners" --engine-version "${engine:-0.0.0}" </dev/null \
            2>"$work/egress-probe.err") || probe_rc=$?
        if [ "$probe_rc" -eq 124 ] || [ "$probe_rc" -eq 137 ]; then
            echo "TIMEOUT: the egress probe killed after $(( $(date -u +%s) - started ))s (limit 120s)" >&3
            stop_oneoff "time limit" >&3
        fi
        oneoff_name=""
        # Its error output (a refused argument, Docker's own complaint) goes
        # into the log: the probe prints nothing secret, only its arguments.
        if [ -s "$work/egress-probe.err" ]; then
            echo "    egress probe's error output:" >&2
            sed 's/^/    | /' "$work/egress-probe.err" >&2
        fi
        echo "    egress probe: ${probed:-no result}"
        echo "$(date -u +%FT%TZ) egress probe: ${probed:-no result}" >>"$logdir/stewardship-backup.log"
        closed=$(jq -r '.closed' <<<"$probed" 2>/dev/null || true)
        if [ "$probe_rc" -eq 3 ] || [ "$closed" = false ]; then
            contract_problems+=("The egress probe reached $(jq -r '.reached | join(", ")' <<<"$probed" 2>/dev/null || echo 'something') from the backup worker's network: close that route (docs/guides/stewardship-backup-runbook.md#no-route-out-for-the-backup-profile).")
        elif [ "$probe_rc" -ne 0 ] || [ "$closed" != true ]; then
            contract_problems+=("The egress probe did not answer (exit $probe_rc).")
        else
            echo "    the backup worker's network reaches nothing outside"
        fi
    fi
    if [ "${#contract_problems[@]}" -gt 0 ]; then
        for problem in "${contract_problems[@]}"; do echo "$problem" >&2; done
        if [ "$profile" = production ]; then
            echo "Nothing was stopped or changed; refusing." >&2
            exit 1
        fi
        echo "    LOCAL: the checks above only report here; continuing" >&2
    fi
fi

uploader_login=pk_stewardship_backup_uploader
uploader_recovery="docs/guides/stewardship-backup-runbook.md#uploader-lost-file-recovery"
if has_command "$new_commands" uploader-password-check; then
    # The off-site uploader's password (#235, operations specification,
    # "Uploader password"): step 4's database-grants refuses a login whose
    # file is missing or does not match the cluster's stored verifier, but
    # only after the full stop. So, read-only and before anything stops: no
    # login yet is nothing to check (step 4 creates it); a login needs its
    # file and the new image's `match`. The query's own failure refuses too
    # and is never read as "no login" (the exit status, and exactly 0 or 1).
    step "1. Checking the off-site uploader's password file"
    read_only=(-e PGOPTIONS='-c default_transaction_read_only=on')
    count=$("${dc[@]}" exec -T "${read_only[@]}" postgres \
        psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 -c \
        "SELECT count(*) FROM pg_authid WHERE rolname='$uploader_login'" </dev/null 2>/dev/null) ||
        { echo "Could not ask the database whether the off-site uploader's login exists; refusing." >&2; exit 1; }
    case "$count" in
        0) echo "    no uploader login yet: step 4's database-grants creates it" ;;
        1)
            # The verifier goes from psql to the check through a file in
            # this run's owner-only work directory, never through the log.
            "${dc[@]}" exec -T "${read_only[@]}" postgres \
                psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 -c \
                "SELECT coalesce(rolpassword,'') FROM pg_authid WHERE rolname='$uploader_login'" \
                </dev/null >"$work/uploader.verifier" 2>/dev/null ||
                { echo "Could not read the off-site uploader's stored password; refusing." >&2; exit 1; }
            # Named like a one-off, so a kill (or a signal's trap, through
            # stop_oneoff) removes the container itself: killing only the
            # docker run client leaves it running. A kill is logged with
            # what, the limit and the elapsed time, and still refuses below
            # (answer: none).
            oneoff_name=$oneoff_prefix-uploader-password-check
            started=$(date -u +%s)
            check_rc=0
            answer=$(timeout --kill-after=10 120 docker run -i --rm --init --network none \
                --name "$oneoff_name" \
                --user 10001:10001 --read-only --cap-drop ALL \
                --security-opt no-new-privileges:true \
                --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
                --mount "type=bind,source=$root,target=$root,readonly" \
                --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
                "$image" uploader-password-check --config /run/operator.yaml \
                <"$work/uploader.verifier" 2>/dev/null) || check_rc=$?
            rm -f "$work/uploader.verifier"
            if [ "$check_rc" -eq 124 ] || [ "$check_rc" -eq 137 ]; then
                echo "TIMEOUT: the off-site uploader's password check killed after $(( $(date -u +%s) - started ))s (limit 120s)" >&3
                stop_oneoff "time limit" >&3
                answer=""
            fi
            oneoff_name=""
            if [ "$answer" != match ]; then
                echo "The off-site uploader's password file is missing or does not match the" >&2
                echo "database's login ${uploader_login} (answer: ${answer:-none}). Nothing was" >&2
                echo "stopped or changed. Follow the uploader lost-file recovery in" >&2
                echo "$uploader_recovery, then repeat." >&2
                exit 1
            fi
            echo "    the uploader's password file matches its login" ;;
        *) echo "Unexpected answer about the off-site uploader's login ($(head -1 <<<"$count")); refusing." >&2
           exit 1 ;;
    esac

    # Docker refuses a network whose subnet overlaps another, but only at
    # the full stop's `up`, after PostgreSQL has stopped. So the uploader's
    # one-shot network, new with this release, is compared now with every
    # other Docker network and with the host's routes. Its /28 is slice 5
    # of runtime_network.offline (tests tie the index to OFFLINE_PROFILES).
    # Once Docker runs the project's own network with that subnet there is
    # nothing to create, so later upgrades skip the comparison.
    offline=$(jq -r '.deployment.runtime_network.offline // "172.29.242.0/24"' "$services/web.yaml")
    [[ $offline =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/(24|25)$ ]] ||
        { echo "Could not read runtime_network.offline ($offline); refusing." >&2; exit 1; }
    ip_number() {
        # A dotted IPv4 address as one number.
        local a b c d
        IFS=. read -r a b c d <<<"$1"
        echo $(( (a << 24) | (b << 16) | (c << 8) | d ))
    }
    ip_text() {
        echo "$(( ($1 >> 24) & 255 )).$(( ($1 >> 16) & 255 )).$(( ($1 >> 8) & 255 )).$(( $1 & 255 ))"
    }
    overlaps() {
        # Whether the IPv4 CIDR $1 overlaps the uploader's /28.
        local address=${1%/*} bits=28 mask
        [[ $1 == */* ]] && bits=${1#*/}
        [[ $address =~ ^[0-9]+(\.[0-9]+){3}$ ]] && [[ $bits =~ ^[0-9]+$ ]] || return 1
        [ "$bits" -le 28 ] || bits=28
        mask=$(( (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
        [ $(( $(ip_number "$address") & mask )) -eq $(( uploader_base & mask )) ]
    }
    uploader_base=$(( $(ip_number "${offline%/*}") + 5 * 16 ))
    uploader_subnet="$(ip_text "$uploader_base")/28"
    own="${project}_offline-backup-uploader"
    ours=$(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$own" 2>/dev/null || true)
    if [ "${ours% }" = "$uploader_subnet" ]; then
        echo "    the uploader's network $uploader_subnet already exists"
    else
        command -v ip >/dev/null ||
            { echo "Cannot read the host's routes (no ip command) to check $uploader_subnet; refusing." >&2; exit 1; }
        clashes=()
        ids=$(docker network ls --quiet --no-trunc) ||
            { echo "Could not list Docker's networks to check $uploader_subnet; refusing." >&2; exit 1; }
        if [ -n "$ids" ]; then
            # shellcheck disable=SC2086 # one network id per word
            subnets=$(docker network inspect --format '{{.Name}}{{range .IPAM.Config}} {{.Subnet}}{{end}}' $ids) ||
                { echo "Could not read Docker's network subnets to check $uploader_subnet; refusing." >&2; exit 1; }
            while read -r name ranges; do
                # The project's own offline networks are kept apart by the
                # renderer; the uploader's own is handled above.
                case "$name" in "${project}_offline-"*) continue ;; esac
                for range in $ranges; do
                    if overlaps "$range"; then clashes+=("Docker network $name ($range)"); fi
                done
            done <<<"$subnets"
        fi
        routes=$(ip -4 route show) ||
            { echo "Could not read the host's routes to check $uploader_subnet; refusing." >&2; exit 1; }
        while read -r first second _; do
            case "$first" in
                "" | default) continue ;;
                blackhole | unreachable | prohibit | throw | unicast | local | broadcast | multicast | anycast) destination=$second ;;
                *) destination=$first ;;
            esac
            [ "$destination" != default ] || continue
            if overlaps "$destination"; then clashes+=("host route $destination"); fi
        done <<<"$routes"
        if [ "${#clashes[@]}" -gt 0 ]; then
            echo "The off-site uploader's network $uploader_subnet (slice 5 of runtime_network.offline" >&2
            echo "$offline) overlaps: ${clashes[*]}. Docker would refuse it at the full stop," >&2
            echo "after PostgreSQL stopped. Nothing was stopped or changed. Move or remove the" >&2
            echo "conflicting network or route named above, then repeat. Do not change" >&2
            echo "runtime_network.offline: on an existing deployment retarget-image refuses any" >&2
            echo "change to it (only the image may change)." >&2
            exit 1
        fi
        echo "    the uploader's network $uploader_subnet overlaps no Docker network or host route"
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
# The off-site uploader (#235, operations specification, "Scripted
# upgrades"): when the running image has its `upload` command and the
# running Compose file has its service, it copies the step-1 set, whatever
# the backup's own line said (on an expand topology the backup's copy can
# report busy because the uploader holds the lock). It waits at most 1800 s
# for a copy already running (an expiry is its own lock_timeout entry) and
# reports whether an uploaded row exists for that set's digest and the
# current folder, whichever run wrote it. Keyed on the command as well as
# the service, so an image between the two expand parts keeps the rule on
# the backup line above.
if [ "$recorded" = true ] && has_command "$running_commands" upload &&
    jq -e '.services["backup-uploader"]' "$compose" >/dev/null 2>&1; then
    digest=$(jq -r '.manifest_digest // .reused_backup.manifest_digest // empty' <<<"$backup" 2>/dev/null || true)
    offsite_ok=0
    if [[ $digest =~ ^[0-9a-f]{64}$ ]]; then
        step "1. Off-site copy by the uploader (waiting up to 1800s for a running copy)"
        # The lock wait, the four-hour copy budget and a margin.
        uploaded=$(oneoff $((1800 + 4 * 3600 + 600)) "the off-site uploader" uploader \
            backup-uploader upload --config "$services/backup-uploader.yaml" \
            --wait-lock 1800 --require-digest "$digest") || uploaded=""
        echo "    ${uploaded:-the uploader printed no result}"
        echo "$(date -u +%FT%TZ) uploader: ${uploaded:-no result}" >>"$logdir/stewardship-backup.log"
        required=$(jq -r '.required_copy // empty' <<<"$uploaded" 2>/dev/null || true)
        case "$profile:$required" in
            production:uploaded | local:uploaded | local:not_configured) offsite_ok=1 ;;
        esac
        if [ "$offsite_ok" -ne 1 ] && [ "$(jq -r '.state // empty' <<<"$uploaded" 2>/dev/null || true)" = busy ]; then
            echo "An off-site copy is still running (or offline work holds the interlock); try" >&2
            echo "again later. A whole catch-up can take up to four hours." >&2
        fi
    else
        echo "The backup line names no manifest digest for the uploader to copy." >&2
    fi
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

# The off-site copy lock (#235, "Offline work"), held from here to the end
# of the run on the backups directory, so a cron uploader exits at the lock
# rather than reaching the interlock, and no copy writes outcome rows while
# the database is offline. It waits, bounded and logged, for a copy that
# started since the step-1 copy; an expiry abandons the run like a busy
# backup (the EXIT trap restarts the background services).
step "1. Holding the off-site copy lock for the offline work"
copy_backups=$(jq -er '.deployment.paths.backups' "$services/web.yaml" 2>/dev/null) ||
    { echo "Could not read the backups directory from $services/web.yaml; abandoning the $mode." >&2; exit 1; }
if ! copy_lock_take "$copy_backups" 1800; then
    echo "An off-site copy is still running and holds the copy lock; abandoning the $mode." >&2
    echo "Try again later (a whole catch-up can take up to four hours)." >&2
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

# The one-time full stop for PostgreSQL's connection sources (#2111;
# operations specification, "Rollout" and "Rollback"): when the retargeted
# Compose file's database networks differ from what Docker runs (the
# release that adds the source rules, or a rollback to a release before
# it), every container but caddy is removed and postgres and valkey start
# again on the rendered networks, before anything else needs them. Later
# upgrades find nothing to change. A failure leaves caddy's maintenance
# page up (phase offline), and the run is safe to repeat.
changes=$(layout_changes) ||
    { echo "Could not compare the database networks with what Docker runs; the maintenance page stays up." >&2; exit 1; }
if [ -n "$changes" ]; then
    step "3. Full stop: the database networks change"
    printf '%s\n' "$changes" | sed 's/^/    /'
    if ! layout_full_stop; then
        echo "The full stop did not finish; caddy serves the maintenance page. Fix the cause" >&2
        echo "and repeat the $mode with the same digest ($runbook, Database connection sources)." >&2
        exit 1
    fi
    echo "    postgres and valkey run on the rendered networks"
fi

# PostgreSQL's per-login source rules (#2111): with the full stop above
# done, the retargeted rules are reloaded and checked before step 4 (the
# shared block, "PostgreSQL source rules check"). A retarget whose Compose
# file renders no hba_file (a rollback to a release before #2111) has no
# rules to reload.
hba_rules=$(hba_rendered)
if [ -z "$hba_rules" ]; then
    step "3. PostgreSQL source rules: none rendered for $image; nothing to reload"
else
    step "3. Reloading PostgreSQL's source rules"
    hba_render ||
        { echo "Could not render the hba check in $image; the maintenance page stays up." >&2; exit 1; }
    if ! hba_reload; then
        echo "The maintenance page stays up; fix forward or roll back ($runbook#rollback)." >&2
        exit 1
    fi
fi

if [ "$mode" = upgrade ]; then
    step "4. Migration"
    answer=$(check)
    if [ "$answer" = t ]; then
        echo "    skipped: the upgrade check answered t (schema and grants already match)"
    else
        echo "    running migration and grants: the check answered: $(head -3 <<<"$answer")"
        # The System logs search's indexes (#765, migration file 0117) need
        # pg_trgm, and creating an extension needs CREATE on the database,
        # which only the operator superuser holds (runbook, Text search
        # extension). IF NOT EXISTS leaves an installed one alone, so this
        # runs before every migration; it takes well under a second. It runs
        # in the running postgres service (not a one-off), so `timeout`
        # alone limits it: an expired limit is logged with what ran, the
        # limit and the elapsed time, and the run stops with the page up.
        step "4. Text search extension (pg_trgm)"
        started=$(date -u +%s)
        rc=0
        extension=$(timeout --kill-after=10 60 "${dc[@]}" exec -T postgres psql \
            -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 \
            -c "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public" 2>&1) || rc=$?
        if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
            echo "TIMEOUT: installing the pg_trgm extension killed after $(( $(date -u +%s) - started ))s (limit 60s). The maintenance page" >&3
            echo "    stays up." >&3
            exit 1
        elif [ "$rc" -ne 0 ]; then
            echo "Could not install the pg_trgm extension: $(tail -3 <<<"$extension")" >&2
            exit 1
        fi
        echo "    pg_trgm is installed"
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
            # Report emails' migration refuses while digest work is open.
            echo "If it refused because a Daily or Weekly digest send is unfinished, see the" >&2
            echo "operations specification, Report email conversion." >&2
            # The digest retirement (#1834) refuses a database not yet
            # converted; step 1 checks for that, so this is the fallback.
            echo "If it refused because schedule definitions are still Daily or Weekly digests," >&2
            echo "the migrations before it (the conversion's 0161 and any later one up to the one" >&2
            echo "that refused) may already have committed: do not run the conversion on this" >&2
            echo "database. Restore the pre-upgrade backup with the previous" >&2
            echo "release first ($runbook#rollback), then upgrade through the release that" >&2
            echo "converts the digests (the same section)." >&2
            exit 1
        fi
        grants grants
    fi
    # Report emails (#1786): the offline conversion of the Daily and Weekly
    # digests, after the migrations and grants of the release that brings
    # them. It takes no inputs, does nothing once nothing is left to
    # convert, and resumes an interrupted run, so it runs on every upgrade,
    # even one whose check answered t. Until it succeeds the services
    # refuse to start, so a failure stops here with the maintenance page up.
    step "4. Report email conversion"
    if ! oneoff 900 "the report email conversion" reports-upgrade admin-recovery upgrade-reports \
        --config "$services/admin-recovery.yaml" --confirm-deployment "$uuid"; then
        echo "The report email conversion did not complete; the services stay stopped." >&2
        echo "Fix the cause and repeat the run (operations specification, Report email" >&2
        echo "conversion)." >&2
        exit 1
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
# From here a failure leaves the new release serving, not the maintenance
# page (decision 7 on #2138: a source-check failure keeps the site up).
[ "$healthy" -eq 0 ] || phase=online
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
    # Both halves of publication: web gates Check ParishSoft, worker runs it.
    for role in web worker; do
        rendered=$(jq -r '.deployment.parishsoft_publication // false' "$services/$role.yaml")
        [ "$rendered" = "$publication" ] || problems+=("$role: ParishSoft publication is $rendered, not $publication")
    done
    # Every role's document is rendered from the same inputs; the two mail
    # senders are checked.
    for role in worker mail-dispatch; do
        rendered=$(jq -r '.deployment.mail_redirect // empty' "$services/$role.yaml")
        [ "$rendered" = "$redirect" ] || problems+=("$role: mail redirect is '${rendered:-unset}', not '${redirect:-unset}'")
    done
fi
# The source check (operations specification, "Source check"): with the
# rules in force, `database-sources` proves the whole matrix without any
# password, from every application service (each Compose service whose
# command is `<command> --config <document>`): by `exec` in each running
# online service (a stopped one is already a problem above) and by `run` in
# each one-shot profile. A difference names the login, the source address
# and the service, and fails the run; the site stays up if web is healthy,
# and the operator chooses a fix-forward release or the rollback.
if [ -n "$hba_rules" ]; then
    step "6. Checking the database sources from every service"
    running=$("${dc[@]}" ps --services --status running || true)
    while read -r service config oneshot; do
        rc=0 limit=60
        started=$(date -u +%s)
        if [ "$oneshot" = true ]; then
            limit=120
            oneoff "$limit" "the source check from $service" "sources-$service" "$service" \
                database-sources --config "$config" || rc=$?
        elif grep -qxF "$service" <<<"$running"; then
            # </dev/null: `compose exec` would otherwise read this loop's list.
            timeout --kill-after=10 "$limit" "${dc[@]}" exec -T "$service" \
                pk-stewardship database-sources --config "$config" </dev/null >"$work/sources.out" 2>&1 || rc=$?
            elapsed=$(( $(date -u +%s) - started ))
            if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ "$elapsed" -ge "$limit" ]; }; then
                if [ "$phase" = online ]; then serving="Web keeps serving."; else serving="Web is not healthy."; fi
                echo "TIMEOUT: the source check from $service killed after ${elapsed}s (limit ${limit}s). $serving" >&3
            fi
            if [ "$rc" -eq 0 ]; then
                tail -1 "$work/sources.out"
            else
                sed 's/^/    | /' "$work/sources.out" >&2
            fi
        else
            # Not running: the state check above already names it.
            continue
        fi
        # Only exit 1 is a found difference (named above with its login and
        # source); a kill or a refusal to run means nothing was proved.
        elapsed=$(( $(date -u +%s) - started ))
        case "$rc" in
            0) ;;
            1) problems+=("$service: database sources differ from the rendered rules (see above)") ;;
            *) if [ "$rc" -eq 124 ] || { [ "$rc" -eq 137 ] && [ "$elapsed" -ge "$limit" ]; }; then
                   problems+=("$service: the source check did not finish within ${limit}s")
               else
                   problems+=("$service: the source check could not run (status $rc)")
               fi ;;
        esac
    done < <(jq -r '.services | to_entries[] | select(.value.command[1]? == "--config")
        | "\(.key) \(.value.command[2]) \(.value.profiles != null)"' "$compose")
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
