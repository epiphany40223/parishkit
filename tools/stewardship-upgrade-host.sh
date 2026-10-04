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
#     header, since before the wizard the application answers 503.

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
echo "Log: $log"

# What a failure means depends on how far the run got: before web stops,
# the current release is put back as it was; with web stopped but nothing
# retargeted yet, every service is simply started again; after the retarget,
# the maintenance page stays up until the run is repeated or the other
# direction is taken (runbook, step 6 and Rollback).
phase=prepare
work=$(mktemp -d)
finish() {
    rc=$?
    rm -rf "$work"
    if [ "$rc" -ne 0 ]; then
        case "$phase" in
            prepare) echo "==> Failed before anything stopped; nothing changed." ;;
            background)
                echo "==> Failed before web stopped; restarting the background services."
                restart_background ;;
            stopped)
                # Nothing was retargeted, so the current release's documents
                # are intact: start every online service of the file again,
                # including any that restarted meanwhile and was stopped too.
                echo "==> Failed with web stopped but nothing retargeted; starting everything again."
                "${dc[@]}" up --detach --wait "${wanted[@]}" 2>&1 | quiet || true ;;
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
    # Let tee flush the last lines before ssh exits.
    exec >&- 2>&-
    wait "$tee_pid" 2>/dev/null || true
    exit "$rc"
}
trap finish EXIT

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
switches=()
if [ "$bulk" = true ]; then
    batch=$(jq -er '.deployment.bulk_send_batch // 20' "$services/worker.yaml")
    switches=(-e PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1 -e "PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=$batch")
    echo "    bulk Family send is on (batch $batch); keeping it on"
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
    # that were stopped back.
    # shellcheck disable=SC2086
    [ -z "$background" ] || "${dc[@]}" up --detach --wait $background 2>&1 | quiet || true
}
phase=background
# shellcheck disable=SC2086 # one service name per word
[ -z "$background" ] || "${dc[@]}" stop $background 2>&1 | quiet

step "1. Backup (required) and its off-host copy"
backup=$("${dc[@]}" run --rm -T backup-worker 2>&1 | grep -v '"DEBUG"' | tail -1 || true)
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
if [ "$(jq -r '.backup_recorded' <<<"$backup" 2>/dev/null)" != true ] || [ "$offsite_ok" -ne 1 ]; then
    # The EXIT trap restarts the background services (phase background).
    echo "The backup did not complete with its off-host copy; abandoning the $mode." >&2
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
        "${dc[@]}" run --rm -T migration 2>&1 | tail -1
        "${dc[@]}" run --rm -T database-provision database-grants \
            --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1
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
if [ "${#switches[@]}" -gt 0 ]; then
    for role in worker mail-dispatch scheduler; do
        [ "$(jq -r '.deployment.bulk_family_send // false' "$services/$role.yaml")" = true ] ||
            problems+=("$role: bulk Family send is off")
    done
    rendered=$(jq -r '.deployment.bulk_send_batch // 20' "$services/worker.yaml")
    [ "$rendered" = "$batch" ] || problems+=("worker: bulk send batch is $rendered, not $batch")
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
