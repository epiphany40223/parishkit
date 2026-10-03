#!/usr/bin/env bash
# Build the current checkout on a pre-production stewardship host and move
# that host's deployment onto the new image, in a few minutes. With
# STEWARDSHIP_IMAGE set, it deploys that already-published image instead.
#
# Pre-launch only. A checkout build skips CI and the release workflow, so an
# image it deploys has not been validated: never use it on a deployment
# serving real Families. Go-live must run a digest from a real release, and
# setting STEWARDSHIP_IMAGE to that release's digest is the right way to put
# a release on a pre-launch host; debug logging then defaults to off. Either way, the host-side script refuses, before building,
# pulling or stopping anything, once the campaign has been activated to
# Production; from then on, deploy release digests with
# tools/stewardship-upgrade.sh, which follows the deployment runbook's
# Upgrade steps.
#
# What it does:
#   1. Packs the checkout's tracked files, including uncommitted edits, and
#      builds the image on the host (native linux/amd64, reusing its cache).
#   2. Pushes the image to GHCR, because production admits only a
#      ghcr.io/...@sha256: digest and a digest exists only after a push.
#      The host must be logged in: docker login ghcr.io (a token with
#      write:packages).
#      With STEWARDSHIP_IMAGE set, steps 1 and 2 are skipped: the host pulls
#      that digest and checks that the pulled image carries exactly that
#      reference.
#   3. Follows the deployment runbook's upgrade steps, keeping web's
#      downtime to the steps that need it (#162). While web still serves:
#      render the new image's upgrade check, collect its static tree into
#      cache/static.next, stop the background services (web serves while
#      they drain) and take the backup (best effort; it refuses before
#      setup). Then stop web, retarget-image in the new image, run migration and
#      grants unless the upgrade check proves both would change nothing,
#      swap the static tree in, start web and wait for it, then start EVERY
#      other online service of the correct topology and check health. Caddy
#      keeps running throughout and answers with its own maintenance page
#      while web is down; it is recreated only when its loaded Caddyfile
#      differs from the host file. A failed deploy leaves the maintenance
#      page up until the deploy is re-run or rolled back.
#
# The topology comes from the database, not from whatever happened to be
# running: compose.json once the setup wizard has recorded completion (kept
# as compose-slack.json if the project already runs under that equivalent
# file), else compose-initial.json. All of that topology's online services
# are started, so a deploy also repairs a deployment an interrupted run left
# half-stopped, and the run fails loudly naming any service that is not
# running and healthy at the end. Each step prints a UTC timestamp, and the
# run reports how long web was down.
#
# Configuration (environment variables):
#   STEWARDSHIP_HOST        ssh destination (required)
#   STEWARDSHIP_ROOT        runtime root (default /opt/parishkit)
#   STEWARDSHIP_PROJECT     Compose project name (default stewardship)
#   STEWARDSHIP_YAML        deployment YAML on the host
#                           (default /etc/parishkit/stewardship-deployment.yaml)
#   STEWARDSHIP_UUID        deployment UUID (required, for database-grants)
#   STEWARDSHIP_IMAGE_REPO  image repository
#                           (default ghcr.io/epiphany40223/parishkit/stewardship)
#   STEWARDSHIP_DEBUG_LOGGING  1 starts services with debug logging:
#                           messages, tracebacks and DEBUG records that normal
#                           logging drops. Pre-launch data only; 0 turns it off.
#                           Defaults to 1 for a checkout build and to 0 when
#                           STEWARDSHIP_IMAGE is set; setting it explicitly
#                           overrides either default.
#   STEWARDSHIP_IMAGE       optional published image to deploy instead of
#                           building the checkout, as the full reference
#                           ${STEWARDSHIP_IMAGE_REPO}@sha256:<64 lowercase hex>

set -euo pipefail

host=${STEWARDSHIP_HOST:?set STEWARDSHIP_HOST to the ssh destination}
uuid=${STEWARDSHIP_UUID:?set STEWARDSHIP_UUID to the deployment UUID}
root=${STEWARDSHIP_ROOT:-/opt/parishkit}
project=${STEWARDSHIP_PROJECT:-stewardship}
yaml=${STEWARDSHIP_YAML:-/etc/parishkit/stewardship-deployment.yaml}
repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}
release=${STEWARDSHIP_IMAGE:-}
# A release should run as it will in production, so it defaults to normal
# logging; a checkout build keeps the pre-launch debug default.
if [ -n "$release" ]; then
    debug=${STEWARDSHIP_DEBUG_LOGGING:-0}
else
    debug=${STEWARDSHIP_DEBUG_LOGGING:-1}
fi
build=/var/tmp/stewardship-build

if [ -n "$release" ]; then
    # Only a digest of this repository names one exact, published image; a
    # tag could move, and another repository is not what production admits.
    hex=${release#"${repo}@sha256:"}
    if [ "$hex" = "$release" ] || ! [[ $hex =~ ^[0-9a-f]{64}$ ]]; then
        echo "STEWARDSHIP_IMAGE must be ${repo}@sha256:<64 lowercase hex>; refusing." >&2
        exit 1
    fi
    # Nothing is built, so there is no checkout to send.
    tag=""
    if [ -n "${STEWARDSHIP_DEBUG_LOGGING:-}" ]; then
        echo "==> Debug logging: ${debug} (from STEWARDSHIP_DEBUG_LOGGING)"
    else
        echo "==> Debug logging: ${debug} (default for a released image)"
    fi
else
    cd "$(git rev-parse --show-toplevel)"
    dirty=$(git diff --quiet HEAD -- && echo "" || echo "-dirty")
    tag="dev-$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)${dirty}"

    echo "==> Sending the checkout to ${host} (${tag})"
    # Tracked files only, as they are on disk now; a deleted tracked file is
    # simply absent from the build.
    git ls-files -z | while IFS= read -r -d '' path; do
        [ -e "$path" ] && printf '%s\0' "$path"
    done | tar --null -T - -czf - |
        ssh "$host" "rm -rf '$build' && mkdir -p '$build' && tar -xzf - -C '$build'"
fi

# Everything else runs on the host. The script is uploaded to a file and run
# from there: fed through ssh's stdin, `docker compose run` would read the rest
# of the script as its own input. ssh joins its command into one string, so
# the positional values are shell-quoted into it.
args=$(printf '%q ' "$build" "$repo" "$tag" "$root" "$project" "$yaml" "$uuid" "$debug")
# The ninth argument, the released image, is passed only when set.
[ -z "$release" ] || args+=$(printf '%q ' "$release")
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <<'REMOTE'
set -euo pipefail
build=$1 repo=$2 tag=$3 root=$4 project=$5 yaml=$6 uuid=$7 release=${9:-}
if [ -n "$release" ]; then
    # Checked again here, before any docker call, so the host never acts on
    # a reference the local check would have refused.
    hex=${release#"${repo}@sha256:"}
    if [ "$hex" = "$release" ] || ! [[ $hex =~ ^[0-9a-f]{64}$ ]]; then
        echo "The image must be ${repo}@sha256:<64 lowercase hex>; refusing." >&2
        exit 1
    fi
fi
# The generated Compose files pass this through to every application service.
export PARISHKIT_DEBUG_LOGGING=$8
services="$root/config/services"
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)

# Refuse, before building, pulling or stopping anything, once the campaign
# has been activated to Production (#326): this path skips CI, deploys an
# untested image with debug logging on, and its best-effort backup would
# leave the migration refusing only after the services had stopped. An
# unreadable answer refuses too; nothing has changed yet.
activated=$(docker compose -f "$services/compose-initial.json" -p "$project" \
    exec -T postgres psql -U pk_stewardship_operator -d stewardship -Atc \
    "SELECT EXISTS (SELECT 1 FROM stewardship_production_request WHERE activated_at IS NOT NULL)" \
    2>/dev/null || true)
if [ "$activated" != f ]; then
    if [ "$activated" = t ]; then
        echo "This deployment's campaign is in Production; refusing a dev deploy." >&2
        echo "Deploy a release digest with tools/stewardship-upgrade.sh, which follows" >&2
        echo "the deployment runbook's Upgrade steps:" >&2
        echo "  docs/guides/stewardship-deployment-runbook.md#upgrade" >&2
    else
        # Most likely the database is simply down; that is no reason to
        # switch to the release procedure.
        echo "Cannot tell whether this deployment is in Production; refusing." >&2
        echo "Start the database first:" >&2
        echo "  docker compose -f $services/compose-initial.json -p $project up --detach --wait postgres" >&2
    fi
    exit 1
fi

# database-grants refuses until the operational log's writer guard (#293)
# is installed; say so now, before anything stops, instead of after.
guard=$(docker compose -f "$services/compose-initial.json" -p "$project" \
    exec -T postgres psql -U pk_stewardship_operator -d stewardship -Atc \
    "SELECT count(*) FROM pg_trigger WHERE tgname='stewardship_operational_log_writer_v1'" \
    2>/dev/null || true)
if [ "$guard" != 1 ]; then
    echo "The operational log writer guard is not installed; apply its in-place SQL first." >&2
    exit 1
fi

# database-grants also refuses while web still holds the INSERT on Family
# sessions that the SQL Family login function replaced (#306); say so now too.
family_login=$(docker compose -f "$services/compose-initial.json" -p "$project" \
    exec -T postgres psql -U pk_stewardship_operator -d stewardship -Atc \
    "SELECT to_regprocedure('public.stewardship_family_login_v1(character varying, uuid, uuid, jsonb, text)') IS NOT NULL AND NOT has_table_privilege('pk_stewardship_web', 'public.stewardship_family_session', 'INSERT')" \
    2>/dev/null || true)
if [ "$family_login" != t ]; then
    echo "The SQL Family login (#306) is not installed; apply its in-place SQL first." >&2
    exit 1
fi

if [ -n "$release" ]; then
    echo "==> $(date -u +%H:%M:%S) Deploying released image ${release}"
    docker pull --quiet "$release" >/dev/null
    # The pulled image must carry exactly the requested reference, so the
    # digest deployed is the one that was named.
    if ! docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$release" |
        grep -xF "$release" >/dev/null; then
        echo "The pulled image does not carry ${release}; refusing." >&2
        exit 1
    fi
    image=$release
else
    echo "==> $(date -u +%H:%M:%S) Building ${repo}:${tag}"
    docker build --quiet --file "$build/deploy/stewardship/Dockerfile" \
        --tag "${repo}:${tag}" "$build" >/dev/null
    echo "==> $(date -u +%H:%M:%S) Pushing"
    docker push --quiet "${repo}:${tag}" >/dev/null
    image=$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${repo}:${tag}" |
        grep -m1 "^${repo}@sha256:")
    echo "    ${image}"
fi

step() {
    # Timestamped progress, so downtime can be measured from the log (#162).
    echo "==> $(date -u +%H:%M:%S) $*"
}
quiet() {
    # Compose progress lines add nothing to a deploy log.
    grep -vE ' (Creat|Start|Stop|Wait|Running|Healthy|Recreat)' || true
}

# The postgres service is identical in every topology, so any rendered file
# reaches it. The setup wizard's committed completion marker decides which
# topology the online services must run under.
probe=(docker compose -f "$services/compose-initial.json" -p "$project")
completed=$("${probe[@]}" exec -T postgres psql -U pk_stewardship_operator \
    -d stewardship -Atc "SELECT EXISTS (SELECT 1 FROM stewardship_setup_completion)" \
    2>/dev/null || true)
# ConfigFiles can list several comma-separated files; the first is the one
# the project was last brought up with.
running=$(docker compose ls --all --format json |
    jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles | split(",")[0]')
case "$completed" in
    t)
        compose="$services/compose.json"
        # compose-slack.json renders the same mounts (#149); keep it rather
        # than switch files needlessly when that is what the project runs.
        [ "$(basename "$running")" = compose-slack.json ] && compose="$running" ;;
    f)
        compose="$services/compose-initial.json" ;;
    *)
        # Nothing has been stopped yet, so refusing here is always safe.
        echo "Cannot read the setup completion marker. Start the database first:" >&2
        echo "  docker compose -f $services/compose-initial.json -p $project up --detach --wait postgres" >&2
        exit 1 ;;
esac
dc=(docker compose -f "$compose" -p "$project")
# Profiled services are the one-shot offline commands; everything else is
# an online service this deploy must leave running.
services_list=$("${dc[@]}" config --services)
mapfile -t wanted < <(printf '%s\n' "$services_list" | grep -vxE 'postgres|valkey')
if [ "${#wanted[@]}" -eq 0 ] || [ -z "${wanted[0]}" ]; then
    # Nothing has been stopped yet; an empty list would stop everything and
    # start nothing.
    echo "$(basename "$compose") lists no online services; refusing to deploy." >&2
    exit 1
fi
# caddy stays up to serve its maintenance page while web is down (#162). An
# older caddy still holds the startup-interlock lease, which would make every
# offline step refuse, so that one (the first deploy of this change) stops.
online=$("${dc[@]}" ps --services --status running | grep -vxE 'postgres|valkey|caddy' || true)
legacy_caddy=0
caddy_id=$("${dc[@]}" ps -q caddy 2>/dev/null || true)
if [ -n "$caddy_id" ] &&
    grep -q startup.lock <<<"$(docker inspect -f '{{join .Config.Cmd " "}}' "$caddy_id")"; then
    legacy_caddy=1
    online="$online caddy"
fi
step "Project ${project} will run $(basename "$compose") (setup complete: ${completed})"
[ -z "$running" ] || [ "$running" = "$compose" ] ||
    echo "    switching from $(basename "$running")"

# Work that needs only the new image runs while the site is still up, so
# none of it counts as downtime (#162): the query that tells whether this
# image's migration and grants would change anything, and the new static
# tree, collected into a directory nothing serves yet.
step "Preparing the new release while web still serves"
check_sql=$(mktemp)
trap 'rm -f "$check_sql"' EXIT
if ! "${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root,readonly" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" upgrade-check --config /run/operator.yaml --confirm-deployment "$uuid" \
    >"$check_sql"; then
    # No proof means migration and grants simply run, as they always did.
    : >"$check_sql"
    echo "    upgrade check could not be rendered; migration and grants will run"
fi
# Disposable pre-launch data: keep only the previous tree.
rm -rf "$root/cache/static.previous" "$root/cache/static.next"
install -d -o 10001 -g 10001 -m 0700 "$root/cache/static.next"
"${isolated[@]}" \
    --mount "type=bind,source=$root/cache/static.next,target=$root/cache/static.next" \
    "$image" collect-static --destination "$root/cache/static.next"

# Stop the background services first: web keeps serving while they drain,
# which can take up to the worker's grace period when a task is running.
# Everything online is still stopped before retarget-image runs. A legacy
# caddy (see above) goes down with web, since it fronts web.
# shellcheck disable=SC2086 # one service name per word
background=$(printf '%s\n' $online | grep -vxE 'web|caddy' || true)
# shellcheck disable=SC2086
front=$(printf '%s\n' $online | grep -xE 'web|caddy' || true)
step "Stopping the background services (web keeps serving)"
# A failed stop ends the deploy here (set -e); its error stays visible.
# shellcheck disable=SC2086 # one service name per word
[ -z "$background" ] || "${dc[@]}" stop $background 2>&1 | quiet

# The backup comes after the drain and just before web stops, so the only
# writes a database-restore rollback could lose are those web accepts while
# the backup runs (#162). backup-worker runs beside web under the shared
# interlock.
step "Backup (best effort)"
"${dc[@]}" run --rm -T backup-worker >/dev/null 2>&1 &&
    echo "    taken" || echo "    refused or unavailable; continuing"

step "Stopping web (caddy serves the maintenance page from here)"
stopped_at=$(date -u +%s)
# Stop whatever runs now, under whichever file started it; the start below
# brings up the full target topology regardless.
# shellcheck disable=SC2086 # one service name per word
[ -z "$front" ] || "${dc[@]}" stop $front 2>&1 | quiet

step "Retargeting"
# Name the image being replaced, so a failed deploy can be rolled back to it
# without digging the digest out of the operators' notes.
previous=$(jq -r '.services.web.image // empty' "$compose" 2>/dev/null || true)
echo "    replacing ${previous:-an image this Compose file does not name}"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" retarget-image --config /run/operator.yaml --image "$image"

step "Migration and grants"
# The query runs now, with every online service stopped, so nothing can
# change the answer before the start below. It is read-only, and anything
# but a clear "t" (including an error) runs both commands.
noop="no query (the render failed)"
if [ -s "$check_sql" ]; then
    noop=$("${dc[@]}" exec -T -e PGOPTIONS='-c default_transaction_read_only=on' \
        postgres psql -U pk_stewardship_operator -d stewardship -At \
        -v ON_ERROR_STOP=1 <"$check_sql" 2>&1 || true)
fi
if [ "$noop" = t ]; then
    echo "    skipped: the upgrade check answered t (the schema and every grant"
    echo "    already match this image)"
else
    # Say why both commands run: "f" means something would change; anything
    # else is the query's own error, shown so a broken check is noticed.
    echo "    running both: the upgrade check answered: $(printf '%s' "$noop" | head -3)"
    "${dc[@]}" run --rm -T migration 2>&1 | tail -1
    "${dc[@]}" run --rm -T database-provision database-grants \
        --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1
fi

step "Static files"
# Refresh cache/static in place from the tree collected above: the running
# caddy has that directory bind-mounted, and a directory moved aside would
# stay mounted in its place, still serving the previous release's scripts.
cp -a "$root/cache/static" "$root/cache/static.previous"
# If the in-place refresh stops partway, caddy serves a half-empty tree;
# say how to put the previous one back.
trap 'echo "Static refresh failed partway. Restore with: find $root/cache/static -mindepth 1 -delete && cp -a $root/cache/static.previous/. $root/cache/static/" >&2' ERR
find "$root/cache/static" -mindepth 1 -delete
cp -a "$root/cache/static.next/." "$root/cache/static/"
trap - ERR
rm -rf "$root/cache/static.next"

# Web first, alone: every other service imports the application and runs
# health probes at the same time, which on a small host roughly quadruples
# the time web needs to turn healthy. The site is back once web is healthy
# (and caddy, when it must be recreated, is up), so that is where the
# downtime ends; the background services start afterwards.
step "Starting web"
web_healthy=0
if printf '%s\n' "${wanted[@]}" | grep -qx web &&
    "${dc[@]}" up --detach --wait web 2>&1 | quiet; then
    web_healthy=1
fi
if printf '%s\n' "${wanted[@]}" | grep -qx caddy; then
    # Caddy reads its Caddyfile only at start (admin off), so recreate it
    # when the upgrade changed that file; otherwise `up` leaves the running
    # caddy alone, or starts it if it was not running.
    # Compare what the running caddy actually loaded with the host file, so a
    # re-run after a failed deploy still recreates it.
    loaded=$("${dc[@]}" exec -T caddy sha256sum /etc/caddy/Caddyfile 2>/dev/null | cut -d' ' -f1 || true)
    wanted_sum=$(sha256sum "$services/Caddyfile" | cut -d' ' -f1)
    if [ "$loaded" != "$wanted_sum" ] || [ "$legacy_caddy" -eq 1 ]; then
        "${dc[@]}" up --detach --wait --force-recreate caddy 2>&1 | quiet || true
    else
        "${dc[@]}" up --detach --wait caddy 2>&1 | quiet || true
    fi
fi
if [ "$web_healthy" -eq 1 ]; then
    step "Web was down for $(( $(date -u +%s) - stopped_at ))s (caddy served the maintenance page)"
else
    step "Web is still not healthy after $(( $(date -u +%s) - stopped_at ))s"
fi

step "Starting the background services"
mapfile -t rest < <(printf '%s\n' "${wanted[@]}" | grep -vxE 'web|caddy' || true)
# A service that never turns healthy fails `up --wait`; keep going so the
# check below names exactly what is wrong.
if [ "${#rest[@]}" -gt 0 ]; then
    "${dc[@]}" up --detach --wait "${rest[@]}" 2>&1 | quiet || true
fi

# Just-started services can report an incomplete dependency observation for a
# few seconds; retry before calling the deploy failed.
healthy=0
for attempt in $(seq 1 12); do
    if "${dc[@]}" exec -T web pk-stewardship health --config "$services/web.yaml"; then
        healthy=1
        break
    fi
    [ "$attempt" -eq 12 ] || sleep 5
done

# Every online service of the target topology must be running, and healthy
# where it has a healthcheck. Name each one that is not.
states=$("${dc[@]}" ps --all --format json | jq -rs 'flatten | .[] |
    "\(.Service) \(.State) \(if .Health == "" then "none" else .Health end)"')
bad=()
for service in "${wanted[@]}"; do
    line=$(printf '%s\n' "$states" | awk -v s="$service" '$1 == s' | head -1)
    case "$line" in
        "$service running healthy" | "$service running none") ;;
        "") bad+=("$service: missing") ;;
        *) bad+=("${line/ /: }") ;;
    esac
done
if [ "${#bad[@]}" -gt 0 ] || [ "$healthy" -ne 1 ]; then
    echo "==> Deployed ${image}, but the deployment is NOT healthy:" >&2
    [ "$healthy" -eq 1 ] || echo "    web health check still failing" >&2
    printf '    %s\n' "${bad[@]}" >&2
    exit 1
fi
step "Deployed ${image}; all ${#wanted[@]} online services are running and healthy"
REMOTE
