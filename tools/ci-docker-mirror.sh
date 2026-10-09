#!/bin/bash
# Pull Docker Hub images in CI through Google's public Docker Hub mirror.
#
# GitHub-hosted runners pull anonymously from Docker Hub, and full CI runs
# pull enough images to hit its unauthenticated rate limit ("toomanyrequests",
# HTTP 429; issue #892). mirror.gcr.io serves the same content without a
# credential. Every Docker Hub image ParishKit uses is pinned by digest, so a
# pull through the mirror yields byte-identical images under their canonical
# names: compose files, the Dockerfile FROM and the release image's base stay
# untouched. The daemon falls back to Docker Hub when the mirror lacks an
# image. This configures only the CI runner's daemon (whose builder honours
# the daemon mirror too); it is not for deployment hosts.
#
# The daemon's registry-mirrors apply only to its classic image store: with
# the containerd image store (the default on newer runner images) docker
# pull and docker compose pull ignore them, and the first CI run with this
# script still reached Docker Hub for PostgreSQL and Valkey. So the runner's
# daemon uses the classic store; nothing has been pulled yet when this runs.
set -euo pipefail

config=/etc/docker/daemon.json
mirror=https://mirror.gcr.io
# Keep any settings the runner image already put in daemon.json.
current='{}'
if sudo test -s "$config"; then
    current=$(sudo cat "$config")
fi
# Write a complete file first, so a failure never leaves daemon.json empty.
next=$(mktemp)
jq --arg mirror "$mirror" \
    '."registry-mirrors" = [$mirror] | .features."containerd-snapshotter" = false' \
    <<<"$current" >"$next"
sudo install -m 0644 "$next" "$config"
rm -f "$next"
sudo systemctl restart docker
# Fail loudly rather than silently pulling from Docker Hub again.
docker info --format '{{json .RegistryConfig.Mirrors}}' | grep -F "$mirror"
docker info --format 'image store driver: {{.Driver}}'
