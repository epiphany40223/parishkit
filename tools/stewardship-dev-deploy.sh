#!/usr/bin/env bash
# Build the current checkout on a pre-production stewardship host and move
# that host's deployment onto the new image, in a few minutes.
#
# Pre-launch only. This skips CI and the release workflow, so an image it
# deploys has not been validated: never use it on a deployment serving real
# Families. Go-live must run a digest from a real release.
#
# What it does:
#   1. Packs the checkout's tracked files, including uncommitted edits, and
#      builds the image on the host (native linux/amd64, reusing its cache).
#   2. Pushes the image to GHCR, because production admits only a
#      ghcr.io/...@sha256: digest and a digest exists only after a push.
#      The host must be logged in: docker login ghcr.io (a token with
#      write:packages).
#   3. Follows the deployment runbook's upgrade steps: backup (best effort;
#      it refuses before setup), stop the online services, retarget-image in
#      the new image, migration and grants, a fresh static tree, then start
#      EVERY online service of the correct topology and check health.
#
# The topology comes from the database, not from whatever happened to be
# running: compose.json once the setup wizard has recorded completion (kept
# as compose-slack.json if the project already runs under that equivalent
# file), else compose-initial.json. All of that topology's online services
# are started, so a deploy also repairs a deployment an interrupted run left
# half-stopped, and the run fails loudly naming any service that is not
# running and healthy at the end. Each step prints a UTC timestamp, and the
# run reports how long the online services were down.
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
#   STEWARDSHIP_DEBUG_LOGGING  1 (default) starts services with debug logging:
#                           messages, tracebacks and DEBUG records that normal
#                           logging drops. Pre-launch data only; 0 turns it off.

set -euo pipefail

host=${STEWARDSHIP_HOST:?set STEWARDSHIP_HOST to the ssh destination}
uuid=${STEWARDSHIP_UUID:?set STEWARDSHIP_UUID to the deployment UUID}
root=${STEWARDSHIP_ROOT:-/opt/parishkit}
project=${STEWARDSHIP_PROJECT:-stewardship}
yaml=${STEWARDSHIP_YAML:-/etc/parishkit/stewardship-deployment.yaml}
repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}
debug=${STEWARDSHIP_DEBUG_LOGGING:-1}

cd "$(git rev-parse --show-toplevel)"
dirty=$(git diff --quiet HEAD -- && echo "" || echo "-dirty")
tag="dev-$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)${dirty}"
build=/var/tmp/stewardship-build

echo "==> Sending the checkout to ${host} (${tag})"
# Tracked files only, as they are on disk now; a deleted tracked file is
# simply absent from the build.
git ls-files -z | while IFS= read -r -d '' path; do
    [ -e "$path" ] && printf '%s\0' "$path"
done | tar --null -T - -czf - |
    ssh "$host" "rm -rf '$build' && mkdir -p '$build' && tar -xzf - -C '$build'"

# Everything else runs on the host. The script is uploaded to a file and run
# from there: fed through ssh's stdin, `docker compose run` would read the rest
# of the script as its own input. ssh joins its command into one string, so
# the positional values are shell-quoted into it.
args=$(printf '%q ' "$build" "$repo" "$tag" "$root" "$project" "$yaml" "$uuid" "$debug")
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <<'REMOTE'
set -euo pipefail
build=$1 repo=$2 tag=$3 root=$4 project=$5 yaml=$6 uuid=$7
# The generated Compose files pass this through to every application service.
export PARISHKIT_DEBUG_LOGGING=$8
services="$root/config/services"
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)

echo "==> $(date -u +%H:%M:%S) Building ${repo}:${tag}"
docker build --quiet --file "$build/deploy/stewardship/Dockerfile" \
    --tag "${repo}:${tag}" "$build" >/dev/null
echo "==> $(date -u +%H:%M:%S) Pushing"
docker push --quiet "${repo}:${tag}" >/dev/null
image=$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${repo}:${tag}" |
    grep -m1 "^${repo}@sha256:")
echo "    ${image}"

step() {
    # Timestamped progress, so downtime can be measured from the log (#162).
    echo "==> $(date -u +%H:%M:%S) $*"
}
quiet() {
    # Compose progress lines add nothing to a deploy log.
    grep -vE ' (Creat|Start|Wait|Running|Healthy|Recreat)' || true
}

# The postgres service is identical in every topology, so any rendered file
# reaches it. The setup wizard's committed completion marker decides which
# topology the online services must run under.
probe=(docker compose -f "$services/compose-initial.json" -p "$project")
completed=$("${probe[@]}" exec -T postgres psql -U pk_stewardship_operator \
    -d stewardship -Atc "SELECT EXISTS (SELECT 1 FROM stewardship_setup_completion)" \
    2>/dev/null || true)
running=$(docker compose ls --all --format json |
    jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles' | tr , '\n' | head -1)
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
mapfile -t wanted < <("${dc[@]}" config --services | grep -vxE 'postgres|valkey')
online=$("${dc[@]}" ps --services --status running | grep -vxE 'postgres|valkey' || true)
step "Project ${project} will run $(basename "$compose") (setup complete: ${completed})"
[ -z "$running" ] || [ "$running" = "$compose" ] ||
    echo "    switching from $(basename "$running")"

step "Backup (best effort)"
"${dc[@]}" run --rm -T backup-worker >/dev/null 2>&1 &&
    echo "    taken" || echo "    refused or unavailable; continuing"

step "Stopping online services"
stopped_at=$(date -u +%s)
# Stop whatever runs now, under whichever file started it; the start below
# brings up the full target topology regardless.
# shellcheck disable=SC2086 # one service name per word
[ -z "$online" ] || "${dc[@]}" stop $online >/dev/null 2>&1

step "Retargeting"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" retarget-image --config /run/operator.yaml --image "$image"

step "Migration and grants"
"${dc[@]}" run --rm -T migration 2>&1 | tail -1
"${dc[@]}" run --rm -T database-provision database-grants \
    --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1

step "Static files"
# Disposable pre-launch data: keep only the previous tree.
rm -rf "$root/cache/static.previous"
mv "$root/cache/static" "$root/cache/static.previous"
install -d -o 10001 -g 10001 -m 0700 "$root/cache/static"
"${isolated[@]}" \
    --mount "type=bind,source=$root/cache/static,target=$root/cache/static" \
    "$image" collect-static --destination "$root/cache/static"

step "Starting every online service"
# caddy last, as first installation does: it fronts web, so the site returns
# only once everything behind it is up.
mapfile -t first < <(printf '%s\n' "${wanted[@]}" | grep -vx caddy || true)
[ "${#first[@]}" -eq 0 ] || "${dc[@]}" up --detach --wait "${first[@]}" 2>&1 | quiet
if printf '%s\n' "${wanted[@]}" | grep -qx caddy; then
    "${dc[@]}" up --detach --wait caddy 2>&1 | quiet
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
step "Online services were down for $(( $(date -u +%s) - stopped_at ))s"

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
