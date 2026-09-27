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
#      what was running and check health.
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

set -euo pipefail

host=${STEWARDSHIP_HOST:?set STEWARDSHIP_HOST to the ssh destination}
uuid=${STEWARDSHIP_UUID:?set STEWARDSHIP_UUID to the deployment UUID}
root=${STEWARDSHIP_ROOT:-/opt/parishkit}
project=${STEWARDSHIP_PROJECT:-stewardship}
yaml=${STEWARDSHIP_YAML:-/etc/parishkit/stewardship-deployment.yaml}
repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}

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
args=$(printf '%q ' "$build" "$repo" "$tag" "$root" "$project" "$yaml" "$uuid")
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <<'REMOTE'
set -euo pipefail
build=$1 repo=$2 tag=$3 root=$4 project=$5 yaml=$6 uuid=$7
services="$root/config/services"
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)

echo "==> Building ${repo}:${tag}"
docker build --quiet --file "$build/deploy/stewardship/Dockerfile" \
    --tag "${repo}:${tag}" "$build" >/dev/null
echo "==> Pushing"
docker push --quiet "${repo}:${tag}" >/dev/null
image=$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${repo}:${tag}" |
    grep -m1 "^${repo}@sha256:")
echo "    ${image}"

# The Compose file the project is running under: compose-initial.json before
# the setup wizard, compose.json or compose-slack.json after it.
compose=$(docker compose ls --all --format json |
    jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles' | cut -d, -f1)
if [ -z "$compose" ]; then
    echo "No Compose project named ${project} is running." >&2
    exit 1
fi
dc=(docker compose -f "$compose" -p "$project")
online=$("${dc[@]}" ps --services --status running | grep -vxE 'postgres|valkey' || true)
echo "==> Project ${project} runs $(basename "$compose")"

echo "==> Backup (best effort)"
"${dc[@]}" run --rm -T backup-worker >/dev/null 2>&1 &&
    echo "    taken" || echo "    refused or unavailable; continuing"

echo "==> Stopping online services"
[ -z "$online" ] || "${dc[@]}" stop $online >/dev/null 2>&1

echo "==> Retargeting"
"${isolated[@]}" \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
    "$image" retarget-image --config /run/operator.yaml --image "$image"

echo "==> Migration and grants"
"${dc[@]}" run --rm -T migration 2>&1 | tail -1
"${dc[@]}" run --rm -T database-provision database-grants \
    --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1

echo "==> Static files"
# Disposable pre-launch data: keep only the previous tree.
rm -rf "$root/cache/static.previous"
mv "$root/cache/static" "$root/cache/static.previous"
install -d -o 10001 -g 10001 -m 0700 "$root/cache/static"
"${isolated[@]}" \
    --mount "type=bind,source=$root/cache/static,target=$root/cache/static" \
    "$image" collect-static --destination "$root/cache/static"

echo "==> Starting"
# caddy last, as first installation does.
rest=$(printf '%s\n' $online | grep -vx caddy || true)
[ -z "$rest" ] || "${dc[@]}" up --detach --wait $rest 2>&1 | grep -vE ' (Creat|Start|Wait|Running|Healthy|Recreat)' || true
if printf '%s\n' $online | grep -qx caddy; then
    "${dc[@]}" up --detach --wait caddy 2>&1 | grep -vE ' (Creat|Start|Wait|Running|Healthy|Recreat)' || true
fi
# Just-started services can report an incomplete dependency observation for a
# few seconds; retry before calling the deploy failed.
for attempt in $(seq 1 12); do
    if "${dc[@]}" exec -T web pk-stewardship health --config "$services/web.yaml"; then
        echo "==> Deployed ${image}"
        exit 0
    fi
    [ "$attempt" -eq 12 ] || sleep 5
done
echo "==> Deployed ${image}, but health is still failing" >&2
exit 1
REMOTE
