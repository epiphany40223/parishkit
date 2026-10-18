#!/usr/bin/env bash
# Remove old stewardship release images from the host that runs the
# deployment (#1661), following the operations specification's Production
# upgrades rules (docs/specs/stewardship/operations/spec.md#production-upgrades).
# Every upgrade pulls a new release image and Docker never removes one, so
# without this the host's disk fills with releases nobody will run again.
# tools/stewardship-upgrade.sh uploads and runs it after a successful
# upgrade or rollback; an operator may also run it on the host directly,
# for example as a preview:
#
#   ssh HOST bash -s -- --dry-run < tools/stewardship-prune-images.sh
#
# Usage: stewardship-prune-images.sh [--repo REPO] [--keep N]
#            [--protect IMAGE]... [--dry-run] [--keep-build-cache]
#
#   --repo REPO        the release repository whose images are candidates
#                      (default ghcr.io/epiphany40223/parishkit/stewardship)
#   --keep N           how many of the newest other release images to keep
#                      besides the ones below (1-99, default 4)
#   --protect IMAGE    an image never to remove, by reference or ID: the
#                      upgrade passes the release it started and the one it
#                      replaced (the rollback target); repeatable
#   --dry-run          print what would be removed; remove nothing
#   --keep-build-cache leave Docker's build cache alone
#
# What it keeps: every image a container on the host uses (running or
# stopped, any project), every --protect image, every image that also
# carries a reference outside REPO, and the N newest remaining release
# images by creation time. The rest of REPO's images are removed one by one
# by image ID with `docker image rm`, never forced, so Docker itself also
# refuses an image a container uses or another repository shares. It never
# touches volumes or other repositories, and never runs `docker image
# prune`, `docker system prune` or any other blanket image, container or
# volume prune. The build cache (`docker builder prune`) holds no images or
# volumes; a host that only pulls releases never needs it.
#
# It prints each image kept or removed and the free space of Docker's root
# before and after, and appends the same lines to
# ${STEWARDSHIP_LOG_DIR:-/var/log}/stewardship-prune-TIMESTAMP.log when that
# directory is writable. It exits 1 when a removal or the build cache prune
# failed (the caller treats that as a warning) and 2 on a usage error.

set -euo pipefail

repo=ghcr.io/epiphany40223/parishkit/stewardship
keep=4
dry_run=0
build_cache=1
protect=()
usage="usage: $0 [--repo REPO] [--keep N] [--protect IMAGE]... [--dry-run] [--keep-build-cache]"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repo) [ "$#" -ge 2 ] || { echo "$usage" >&2; exit 2; }; repo=$2; shift 2 ;;
        --keep) [ "$#" -ge 2 ] || { echo "$usage" >&2; exit 2; }; keep=$2; shift 2 ;;
        --protect) [ "$#" -ge 2 ] || { echo "$usage" >&2; exit 2; }; protect+=("$2"); shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --keep-build-cache) build_cache=0; shift ;;
        *) echo "$usage" >&2; exit 2 ;;
    esac
done
# At least one other release stays, so even a run that was told nothing
# about the replaced release leaves a recent rollback target on the host.
[[ $keep =~ ^[1-9][0-9]?$ ]] || { echo "--keep must be 1-99; refusing." >&2; exit 2; }
[[ $repo =~ ^[a-z0-9][a-z0-9./_-]*$ ]] || { echo "--repo must be a repository name; refusing." >&2; exit 2; }

logdir=${STEWARDSHIP_LOG_DIR:-/var/log}
log=/dev/null
if [ -w "$logdir" ]; then
    log=$logdir/stewardship-prune-$(date -u +%Y%m%dT%H%M%SZ).log
fi
say() {
    # Print a line and keep it in the host log.
    printf '%s\n' "$*" | tee -a "$log"
}

free_kb() {
    # Free space (KiB) of the filesystem holding Docker's data, POSIX df.
    df -Pk "$docker_root" 2>/dev/null | awk 'NR == 2 { print $4 }'
}

if [ "$dry_run" = 1 ]; then
    say "==> Pruning $repo images (dry run: nothing is removed)"
else
    say "==> Pruning $repo images"
fi
[ "$log" = /dev/null ] || say "    Log: $log"
docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
[ -d "$docker_root" ] || docker_root=/
before=$(free_kb || true)
# Any failure while finding what to keep stops the run before anything more
# is removed: an incomplete keep list must never decide a removal.
trap 'say "    WARNING: pruning stopped (line $LINENO failed); nothing more is removed"' ERR

# The kept image IDs, one "ID reason" per line (bash 3.2 on a macOS test
# machine has no associative arrays). A container's .Image is its image's
# ID whatever reference it was created from; stopped containers count too.
kept=""
containers=$(docker ps --all --quiet --no-trunc)
if [ -n "$containers" ]; then
    # shellcheck disable=SC2086 # container IDs hold no whitespace
    kept=$(docker inspect --format '{{.Image}} used by a container' $containers)
fi
# The protected images, resolved to IDs. Only Docker's "No such image"
# means one is not on the host (just noted); any other lookup failure, such
# as a daemon error, stops the run, since that protection would be lost.
# Docker's error text goes to a file so it never mixes into the ID.
errf=$(mktemp)
trap 'rm -f "$errf"' EXIT
for ref in ${protect[@]+"${protect[@]}"}; do
    [ -n "$ref" ] || continue
    if id=$(docker image inspect --format '{{.Id}}' "$ref" 2>"$errf") && [ -n "$id" ]; then
        kept+=$'\n'"$id protected ($ref)"
    elif grep -q "No such image" "$errf"; then
        say "    protected image not on this host: $ref"
    else
        say "    WARNING: could not look up protected image $ref: $(head -1 "$errf")"
        false
    fi
done
reason() {
    # Why image ID $1 is kept, or nothing when no rule above keeps it.
    awk -v id="$1" '$1 == id { $1 = ""; sub(/^ /, ""); print; exit }' <<<"$kept"
}

# Every candidate as "created id size refs", newest first. Created is
# RFC 3339 in UTC, so it sorts as text.
candidates=$(
    docker image ls --no-trunc --quiet --filter "reference=$repo" | sort -u |
        while read -r id; do
            [ -z "$id" ] || docker image inspect --format \
                '{{.Created}} {{.Id}} {{.Size}} {{join .RepoTags ","}},{{join .RepoDigests ","}}' "$id"
        done | sort -r
)

failed=0
newest=0
removed=0
while read -r _created id size refs; do
    [ -n "$id" ] || continue
    name=${refs#,}
    name=${name%%,*}
    name=${name:-$id}
    why=$(reason "$id")
    # An image that also carries a reference outside the repository is
    # someone else's too; the filter's own matching is not trusted alone.
    if [ -z "$why" ]; then
        IFS=, read -ra all <<<"$refs"
        for ref in ${all[@]+"${all[@]}"}; do
            [ -z "$ref" ] || [[ $ref == "$repo:"* || $ref == "$repo@"* ]] ||
                why="also named outside $repo ($ref)"
        done
    fi
    if [ -z "$why" ] && [ "$newest" -lt "$keep" ]; then
        newest=$((newest + 1))
        why="one of the $keep newest"
    fi
    if [ -n "$why" ]; then
        say "    keep   $name ($why)"
    elif [ "$dry_run" = 1 ]; then
        say "    would remove $name ($((size / 1048576)) MB)"
    elif out=$(docker image rm "$id" 2>&1 </dev/null); then
        removed=$((removed + 1))
        say "    removed $name ($((size / 1048576)) MB)"
    else
        failed=1
        say "    WARNING: could not remove $name: $(head -1 <<<"$out")"
    fi
done <<<"$candidates"

if [ "$build_cache" = 0 ]; then
    say "    build cache kept (--keep-build-cache)"
elif [ "$dry_run" = 1 ]; then
    say "    would clear the build cache"
elif out=$(docker builder prune --force 2>&1 </dev/null); then
    say "    build cache cleared: $(tail -1 <<<"$out")"
else
    failed=1
    say "    WARNING: could not clear the build cache: $(head -1 <<<"$out")"
fi

after=$(free_kb || true)
if [ -n "$before" ] && [ -n "$after" ]; then
    say "==> Removed $removed image(s); freed $(((after - before) / 1024)) MB" \
        "($((before / 1024)) MB free before, $((after / 1024)) MB after)"
else
    say "==> Removed $removed image(s)"
fi
exit "$failed"
