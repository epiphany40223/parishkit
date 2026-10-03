#!/usr/bin/env bash
# Upgrade a stewardship deployment to a published release digest, following
# the deployment runbook's Upgrade steps 1-6 one for one
# (docs/guides/stewardship-deployment-runbook.md#upgrade). Unlike
# tools/stewardship-dev-deploy.sh it builds nothing, accepts only a release
# digest, requires a completed backup with its off-host copy before web
# stops, and runs on a deployment in Production as well as in Testing.
#
# It carries the deployment's current bulk Family send switch and batch size
# over into the re-rendered documents, so an upgrade never silently turns
# the bulk send off, and it starts every application service with debug
# logging off. Each step prints a UTC timestamp and how long the previous
# step took; the whole log is also kept on the host under /var/log.
#
# Configuration (environment variables):
#   STEWARDSHIP_HOST        ssh destination (required)
#   STEWARDSHIP_UUID        deployment UUID (required)
#   STEWARDSHIP_IMAGE       release image, ${STEWARDSHIP_IMAGE_REPO}@sha256:<hex> (required)
#   STEWARDSHIP_ROOT        runtime root (default /opt/parishkit)
#   STEWARDSHIP_PROJECT     Compose project name (default stewardship)
#   STEWARDSHIP_YAML        deployment YAML on the host
#                           (default /etc/parishkit/stewardship-deployment.yaml)
#   STEWARDSHIP_IMAGE_REPO  image repository
#                           (default ghcr.io/epiphany40223/parishkit/stewardship)
#   STEWARDSHIP_SCHEMA_CHANGE  1 when the release notes announce a schema or
#                           grant change; otherwise the script refuses, before
#                           anything stops, unless the advisory upgrade check
#                           proves migration and grants would change nothing.

set -euo pipefail

host=${STEWARDSHIP_HOST:?set STEWARDSHIP_HOST to the ssh destination}
uuid=${STEWARDSHIP_UUID:?set STEWARDSHIP_UUID to the deployment UUID}
image=${STEWARDSHIP_IMAGE:?set STEWARDSHIP_IMAGE to the release digest}
root=${STEWARDSHIP_ROOT:-/opt/parishkit}
project=${STEWARDSHIP_PROJECT:-stewardship}
yaml=${STEWARDSHIP_YAML:-/etc/parishkit/stewardship-deployment.yaml}
repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}

hex=${image#"${repo}@sha256:"}
if [ "$hex" = "$image" ] || ! [[ $hex =~ ^[0-9a-f]{64}$ ]]; then
    echo "STEWARDSHIP_IMAGE must be ${repo}@sha256:<64 lowercase hex>; refusing." >&2
    exit 1
fi

# As in the dev deploy tool: upload the host script to a file and run it from
# there, because `docker compose run` would otherwise read the rest of the
# script from ssh's stdin.
args=$(printf '%q ' "$repo" "$image" "$root" "$project" "$yaml" "$uuid" "${STEWARDSHIP_SCHEMA_CHANGE:-0}")
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <<'REMOTE'
set -euo pipefail
repo=$1 image=$2 root=$3 project=$4 yaml=$5 uuid=$6 schema_change=$7
log=/var/log/stewardship-upgrade-$(date -u +%Y%m%dT%H%M%SZ).log
exec > >(tee -a "$log") 2>&1
tee_pid=$!
echo "Log: $log"

# What a failure means depends on how far the upgrade got: before web
# stops, the old release is put back as it was; after, the maintenance page
# stays up until the upgrade is re-run or rolled back (runbook, step 6).
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
            offline)
                echo "==> Failed with web stopped: caddy serves the maintenance page."
                echo "    Re-run with the same digest, or roll back (runbook, Rollback)." ;;
        esac
    fi
    # Let tee flush the last lines before ssh exits.
    exec >&- 2>&-
    wait "$tee_pid" 2>/dev/null || true
    exit "$rc"
}
trap finish EXIT

# The generated Compose files pass this through to every application service.
export PARISHKIT_DEBUG_LOGGING=0
services="$root/config/services"
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)

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
# changes images, not topology.
compose=$(docker compose ls --all --format json |
    jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles | split(",")[0]')
[ -n "$compose" ] && [ -f "$compose" ] || { echo "Project $project not found; refusing." >&2; exit 1; }
dc=(docker compose -f "$compose" -p "$project")
previous=$(jq -r '.services.web.image // empty' "$compose")
step "Upgrading $project ($(basename "$compose")) from ${previous:-unknown} to $image"
[ "$previous" != "$image" ] || echo "    (same digest: re-running the upgrade)"

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
        echo "$role runs with a runtime fallback (above); upgrade by hand per the runbook." >&2
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

# Step 1: everything that needs only the new image, while the site is up.
step "1. Pulling the release"
docker pull --quiet "$image" >/dev/null
docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" | grep -xF "$image" >/dev/null ||
    { echo "The pulled image does not carry $image; refusing." >&2; exit 1; }

step "1. Collecting static files into cache/static.next"
rm -rf "$root/cache/static.next"
install -d -o 10001 -g 10001 -m 0700 "$root/cache/static.next"
"${isolated[@]}" \
    --mount "type=bind,source=$root/cache/static.next,target=$root/cache/static.next" \
    "$image" collect-static --destination "$root/cache/static.next" | tail -1

step "1. Rendering the upgrade check"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root,readonly" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" upgrade-check --config /run/operator.yaml --confirm-deployment "$uuid" >"$work/upgrade-check.sql"
check() {
    # Run the rendered upgrade check in a read-only session; prints t or f.
    "${dc[@]}" exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres \
        psql -U pk_stewardship_operator -d "$database" -At -v ON_ERROR_STOP=1 <"$work/upgrade-check.sql" 2>&1 || true
}
advisory=$(check)
echo "    advisory answer: $(head -3 <<<"$advisory")"
# Runbook step 1: for a release whose notes promise no schema or grant
# change, anything but t is the cue to stop before anything stops.
if [ "$advisory" != t ] && [ "$schema_change" != 1 ]; then
    echo "The upgrade check expects migration or grants to change something." >&2
    echo "Read the release notes; set STEWARDSHIP_SCHEMA_CHANGE=1 only if they announce it." >&2
    exit 1
fi

step "1. Stopping the background services (web keeps serving)"
background=$("${dc[@]}" ps --services --status running | grep -vxE 'web|caddy|postgres|valkey' || true)
restart_background() {
    # Abandon the upgrade before web stopped: bring the old services back.
    # shellcheck disable=SC2086
    [ -z "$background" ] || "${dc[@]}" up --detach --wait $background 2>&1 | quiet || true
}
phase=background
# shellcheck disable=SC2086 # one service name per word
[ -z "$background" ] || "${dc[@]}" stop $background 2>&1 | quiet

step "1. Backup (required) and its off-host copy"
backup=$("${dc[@]}" run --rm -T backup-worker 2>&1 | grep -v '"DEBUG"' | tail -1 || true)
echo "    $backup"
echo "$(date -u +%FT%TZ) $backup" >>/var/log/stewardship-backup.log
if [ "$(jq -r '.backup_recorded' <<<"$backup" 2>/dev/null)" != true ] ||
    [ "$(jq -r '.offsite.state' <<<"$backup" 2>/dev/null)" != uploaded ]; then
    # The EXIT trap restarts the background services (phase background).
    echo "The backup did not complete with its off-host copy; abandoning the upgrade." >&2
    exit 1
fi

# Step 2: the site is down from here; caddy serves its maintenance page.
step "2. Stopping web"
stopped_at=$(date -u +%s)
phase=offline
"${dc[@]}" stop web 2>&1 | quiet
still=$("${dc[@]}" ps --services --status running | grep -vxE 'caddy|postgres|valkey' || true)
if [ -n "$still" ]; then
    echo "Online services restarted meanwhile ($still); stopping them too."
    # shellcheck disable=SC2086
    "${dc[@]}" stop $still 2>&1 | quiet
fi

step "3. Retargeting the image"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "${switches[@]}" "$image" retarget-image --config /run/operator.yaml --image "$image"

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

step "5. Refreshing the static files in place"
aside="$root/cache/static.${previous:+${previous##*sha256:}}"
[ -n "$previous" ] || aside="$root/cache/static.previous"
[ -e "$aside" ] || cp -a "$root/cache/static" "$aside"
trap 'echo "Static refresh failed partway. Restore with: find $root/cache/static -mindepth 1 -delete && cp -a $aside/. $root/cache/static/" >&2' ERR
find "$root/cache/static" -mindepth 1 -delete
cp -a "$root/cache/static.next/." "$root/cache/static/"
trap - ERR
rm -rf "$root/cache/static.next"
echo "    previous tree kept at $aside"

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
mapfile -t wanted < <("${dc[@]}" config --services | grep -vxE 'postgres|valkey')
mapfile -t rest < <(printf '%s\n' "${wanted[@]}" | grep -vxE 'web|caddy' || true)
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
for service in "${wanted[@]}"; do
    line=$(awk -v s="$service" '$1 == s' <<<"$states" | head -1)
    case "$line" in
        "$service running healthy" | "$service running none") ;;
        *) problems+=("$service: ${line:-missing}") ;;
    esac
done
# Debug logging off everywhere (launch runbook, Production activation step 1).
for id in $("${dc[@]}" ps --quiet); do
    name=$(docker inspect --format '{{.Name}}' "$id")
    value=$(docker inspect --format '{{join .Config.Env "\n"}}' "$id" | grep '^PARISHKIT_DEBUG_LOGGING=' || true)
    case "$value" in ""|PARISHKIT_DEBUG_LOGGING=0) ;; *) problems+=("$name: $value") ;; esac
done
[ "$(jq -r '.services.web.image' "$compose")" = "$image" ] || problems+=("web does not name $image")
if [ "${#switches[@]}" -gt 0 ]; then
    for role in worker mail-dispatch scheduler; do
        [ "$(jq -r '.deployment.bulk_family_send // false' "$services/$role.yaml")" = true ] ||
            problems+=("$role: bulk Family send is off")
    done
fi
version=$("${dc[@]}" exec -T web python -c 'import parishkit; print(parishkit.__version__)' 2>/dev/null || echo unknown)
echo "    application version: $version"
# Runbook step 6: open the public origin; the in-container probe cannot see
# caddy still answering with its maintenance page.
curl -fsS -o /dev/null --max-time 20 "$origin/" || problems+=("public origin $origin does not answer")
echo "    worker processes: $("${dc[@]}" top worker 2>/dev/null | grep -c 'runtime' || true)," \
    "mail-dispatch processes: $("${dc[@]}" top mail-dispatch 2>/dev/null | grep -c 'runtime' || true)"
if [ "${#problems[@]}" -gt 0 ]; then
    echo "==> Upgraded to $image, but NOT healthy:" >&2
    printf '    %s\n' "${problems[@]}" >&2
    exit 1
fi
step "Upgraded to $image in $(( $(date -u +%s) - began ))s; all ${#wanted[@]} online services healthy"
echo "    Record in the operators' notes. Previous release: $previous"
REMOTE
