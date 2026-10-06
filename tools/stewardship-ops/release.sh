#!/usr/bin/env bash
# Tag and publish a release from main's head, following the release
# evidence rule in .github/workflows/release.yml: a release needs a
# successful workflow_dispatch CI run on exactly the tagged commit. See
# docs/guides/stewardship-operator-scripts.md.
#
# Steps, stopping at the first failure:
#   1. Check that the git remote is the GitHub repository, read main's head
#      from it, and check that its committed pyproject.toml version (read
#      with tomllib, as release.yml does) is VERSION and that tag vVERSION
#      exists neither locally nor on the remote. Land the version bump first.
#   2. Use CI_RUN_ID, which must be the newest workflow_dispatch run of
#      ci.yml on that exact commit (the one release.yml will check), or
#      without it dispatch a new run on main and find it.
#   3. Refuse a run that was not dispatched with every job (its run-name
#      must be exactly "CI (jobs: all)"), watch it with ci-watch.sh, then
#      verify its commit, event and conclusion again.
#   4. Ask for the tag name to be typed (skipped with --yes), check once
#      more that the run is still the newest such run, create the annotated
#      tag vVERSION at that commit and push it; a failed push deletes the
#      local tag again. Pushing a release tag needs a human's explicit
#      authorization; running this is that act. git fetch, ls-remote and
#      push have a two-minute limit each.
#   5. Wait for the new release.yml run (a push of the tag on that commit)
#      and print the published image digest on stdout.
#
# Usage: tools/stewardship-ops/release.sh [-h] [--yes] VERSION [CI_RUN_ID]
#
# Exit status: 0 published; 1 a refusal or failure; 2 a usage error; 3 a
# wait passed its limit (recorded as lib.sh's ops_timeout describes).
#
# Configuration (environment variables):
#   STEWARDSHIP_GH_REPO        repository (default epiphany40223/parishkit)
#   STEWARDSHIP_GIT_REMOTE     git remote for that repository (default origin)
#   STEWARDSHIP_IMAGE_REPO     image repository whose digest is printed
#                              (default ghcr.io/epiphany40223/parishkit/stewardship)
#   STEWARDSHIP_PYTHON         Python 3.11 or newer (default python3)
#   STEWARDSHIP_FIND_MINUTES   how long to wait for a dispatched or release
#                              run to appear (default 5)
#   STEWARDSHIP_WATCH_MINUTES  ci-watch.sh's limit per run (default 120)
#   STEWARDSHIP_POLL_SECONDS   pause between checks (default 60)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

usage() {
    echo "usage: $0 [-h] [--yes] VERSION [CI_RUN_ID]" >&2
}

yes=0
case "${1-}" in
    -h | --help)
        sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
        exit 0
        ;;
    --yes)
        yes=1
        shift
        ;;
esac
if [ $# -lt 1 ] || [ $# -gt 2 ] || [[ $1 == -* ]]; then
    usage
    exit 2
fi
version=$1 run=${2-}
if ! [[ $version =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    ops_refuse "VERSION must be MAJOR.MINOR.PATCH, such as 1.2.3, not '$version'"
fi
if [ -n "$run" ] && ! [[ $run =~ ^[0-9]+$ ]]; then
    ops_refuse "CI_RUN_ID must be a numeric Actions run id, not '$run'"
fi
tag=v$version
repo=${STEWARDSHIP_GH_REPO:-epiphany40223/parishkit}
remote=${STEWARDSHIP_GIT_REMOTE:-origin}
image_repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}
python=${STEWARDSHIP_PYTHON:-python3}
find_minutes=${STEWARDSHIP_FIND_MINUTES:-5}
poll=${STEWARDSHIP_POLL_SECONDS:-60}
ops_require_count STEWARDSHIP_FIND_MINUTES "$find_minutes"
ops_require_count STEWARDSHIP_POLL_SECONDS "$poll"
checkout=$(CDPATH='' cd "$here/../.." && pwd)
git=(git -C "$checkout")

# 1. The remote is the repository gh acts on; the configured URL, not the
# rewritten one, so a url.insteadOf mirror cannot hide another repository.
url=$("${git[@]}" config --get "remote.$remote.url" || true)
if [[ $url =~ ^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)(.+)$ ]]; then
    named=${BASH_REMATCH[2]%/}
    named=${named%.git}
else
    named=""
fi
if [ "$named" != "$repo" ]; then
    ops_refuse "Git remote $remote is '${url:-unset}', not GitHub's $repo (set STEWARDSHIP_GIT_REMOTE or STEWARDSHIP_GH_REPO)"
fi

# main's head, its committed version, and no existing tag.
ops_limited 120 "git fetch of main" "${git[@]}" fetch -q --no-tags "$remote" "+refs/heads/main:refs/remotes/$remote/main"
sha=$("${git[@]}" rev-parse "$remote/main")
if ! committed=$("${git[@]}" show "$sha:pyproject.toml" | "$python" -c \
    'import sys, tomllib; print(tomllib.load(sys.stdin.buffer)["project"]["version"])'); then
    ops_refuse "Cannot read the project version from main's pyproject.toml with $python (Python 3.11 or newer; set STEWARDSHIP_PYTHON)"
fi
if [ "$committed" != "$version" ]; then
    ops_refuse "main ($sha) is version '$committed', not $version; land the version bump first"
fi
if "${git[@]}" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    ops_refuse "Tag $tag already exists in this checkout"
fi
remote_tag=$(ops_limited 120 "git ls-remote" "${git[@]}" ls-remote --tags "$remote" "refs/tags/$tag")
if [ -n "$remote_tag" ]; then
    ops_refuse "Tag $tag already exists on $remote"
fi
ops_log "main is $sha (version $version)"

# The ids of the newest workflow_dispatch CI runs on this commit, newest first.
dispatch_runs() {
    ops_gh run list --repo "$repo" --workflow ci.yml --event workflow_dispatch \
        --commit "$sha" --limit "${1:-20}" --json databaseId -q '.[].databaseId'
}

# The ids of release.yml runs for a push of the tag on this commit.
release_runs() {
    ops_gh run list --repo "$repo" --workflow release.yml --branch "$tag" \
        --commit "$sha" --event push --limit 20 --json databaseId -q '.[].databaseId'
}

# Wait for the first id from command "$2" that is not among "$3" (ids seen
# before the dispatch or push); $1 says what for the timeout record.
await_new_run() {
    local what=$1 list=$2 before=$3 start=$SECONDS found
    while :; do
        found=$("$list" | grep -vxF -e "${before:-none}" | head -n 1 || true)
        if [ -n "$found" ]; then
            printf '%s\n' "$found"
            return
        fi
        if [ $((SECONDS - start)) -ge $((find_minutes * 60)) ]; then
            ops_timeout "$what" "$find_minutes minutes" "$start"
        fi
        sleep "$poll"
    done
}

# Watch a run with ci-watch.sh (its lines on stderr, so stdout carries only
# the digest). A watch that timed out ends this script with the same status.
watch() {
    local status=0
    "$here/ci-watch.sh" "$1" >&2 || status=$?
    if [ "$status" = "$OPS_TIMEOUT_STATUS" ]; then
        exit "$OPS_TIMEOUT_STATUS"
    fi
    return "$status"
}

# 2. The CI run: the one named, or a freshly dispatched one.
if [ -n "$run" ]; then
    newest=$(dispatch_runs 1)
    if [ "$newest" != "$run" ]; then
        ops_refuse "Run $run is not the newest workflow_dispatch CI run on $sha (that is '${newest:-none}'), which is the run release.yml checks"
    fi
else
    before=$(dispatch_runs)
    ops_log "dispatching CI on main"
    ops_gh workflow run ci.yml --repo "$repo" --ref main -f jobs=all
    run=$(await_new_run "the dispatched CI run on $sha to appear (if main moved, it ran on the new head)" dispatch_runs "$before")
fi
ops_log "CI run $run"

# 3. Only a run of every job is release evidence (#626): a jobs=affected
# dispatch may have skipped job groups. Accept exactly the name ci.yml gives
# an all-jobs dispatch, as release.yml does, and refuse before watching.
title=$(ops_gh run view "$run" --repo "$repo" --json displayTitle -q .displayTitle)
if [ "$title" != "CI (jobs: all)" ]; then
    ops_refuse "CI run $run is named '$title', not 'CI (jobs: all)'; release evidence needs a dispatch of every job"
fi

# Watch it, then check what it ran on.
if ! watch "$run"; then
    ops_refuse "CI run $run did not pass; not tagging"
fi
info=$(ops_gh run view "$run" --repo "$repo" --json headSha,event,conclusion \
    -q '"\(.headSha)|\(.event)|\(.conclusion)"')
IFS='|' read -r rsha event conclusion <<<"$info"
if [ "$rsha" != "$sha" ] || [ "$event" != workflow_dispatch ] || [ "$conclusion" != success ]; then
    ops_refuse "CI run $run is $rsha/$event/$conclusion, not $sha/workflow_dispatch/success"
fi

# 4. Confirm; then, immediately before tagging, check that no newer run
# took its place as the one release.yml will check; tag and push.
if [ "$yes" != 1 ]; then
    printf 'Type %s to create and push the annotated tag %s at %s: ' "$tag" "$tag" "$sha" >&2
    answer=""
    read -r answer || true
    if [ "$answer" != "$tag" ]; then
        ops_refuse "The tag was not confirmed"
    fi
fi
newest=$(dispatch_runs 1)
if [ "$newest" != "$run" ]; then
    ops_refuse "A newer CI run ($newest) started on $sha while $run ran, and release.yml checks the newest; re-run with it once it passes"
fi
before=$(release_runs)
"${git[@]}" tag -a "$tag" -m "ParishKit $version" "$sha"
# In a subshell, so that a push past its limit (recorded) still reaches the
# cleanup below; whether a timed-out push landed is unknown, so say so.
pushed=0
(ops_limited 120 "git push of $tag" "${git[@]}" push -q "$remote" "refs/tags/$tag") || pushed=$?
if [ "$pushed" != 0 ]; then
    "${git[@]}" tag -d "$tag" >/dev/null
    echo "Pushing $tag failed, so the local tag was deleted again; check git ls-remote --tags $remote before retrying." >&2
    if [ "$pushed" = "$OPS_TIMEOUT_STATUS" ]; then
        exit "$OPS_TIMEOUT_STATUS"
    fi
    exit 1
fi
ops_log "pushed $tag at $sha"

# 5. The release run on the tag, and the digest it published.
release=$(await_new_run "the release.yml run for $tag to appear" release_runs "$before")
ops_log "release run $release"
if ! watch "$release"; then
    ops_refuse "Release run $release did not pass; the tag $tag stays pushed, see the run"
fi
# The complete log can lag the run's completion briefly; try a few times.
pattern="${image_repo//./\\.}@sha256:[0-9a-f]{64}"
digest=""
for _ in 1 2 3; do
    digest=$( (ops_gh run view "$release" --repo "$repo" --log || true) | grep -oE "$pattern" | sort -u || true)
    [ -z "$digest" ] || break
    sleep "$poll"
done
if [ -z "$digest" ]; then
    ops_refuse "Release run $release passed but its log names no $image_repo digest; read the release notes"
fi
printf '%s\n' "$digest"
