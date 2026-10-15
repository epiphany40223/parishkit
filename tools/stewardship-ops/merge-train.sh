#!/usr/bin/env bash
# Merge a release train's pull requests into main, in train order, and
# prove that main ends on exactly the tree the train's full CI run tested
# (#158). See docs/guides/stewardship-test-efficiency.md#release-trains.
#
# Usage: tools/stewardship-ops/merge-train.sh [-h] TRAIN PR:SHA [PR:SHA ...]
#
# TRAIN is the tested train head (a commit or a ref such as
# origin/train/2026-10-12a; fetch it first). Each PR:SHA names a pull
# request and the head commit the train merged, in the train's order. The
# pull requests must be branches of this repository; forks are not
# supported.
#
# To resume a train that stopped partway (a failed merge, a timeout), fix
# the cause and run the script again with the same arguments: the pull
# requests already merged, a prefix of the list, are skipped.
#
# Steps, stopping at the first failure, before anything merges:
#   1. Check that the git remote is the GitHub repository, fetch main, and
#      require that each SHA is in TRAIN and that main is TRAIN's base,
#      plus merge commits of the first listed SHAs in order (a resumed
#      train). Anything else on main means it moved since the train was
#      built: rebuild the train.
#   2. Ask release_evidence.py which full CI run decides for TRAIN, as
#      release.yml would; it must be completed and successful. A pass on a
#      later attempt (failed jobs re-run) is printed as RERUN for the train
#      record.
# Then, for each pull request in order that has not merged yet:
#   3. Require that its head is still SHA, then retarget it to main if it
#      was stacked and mark it ready if it is a draft.
#   4. Admin-merge it with a merge commit pinned to SHA
#      (--match-head-commit), retrying while GitHub recomputes
#      mergeability, then fetch main and require that the new commit's
#      parents are the previous main and SHA. Anything else landed on main
#      meanwhile: print MAIN-MOVED and stop.
#   5. Cancel that pull request's unfinished CI runs, if any (usually none:
#      marking a draft ready starts no run, #624); the train's run is its
#      evidence. Pushes to main are never cancelled: ci.yml's
#      main-tree-evidence job is main's check, and it dispatches a full run
#      when no run covers main's tree.
# Finally, compare main's tree with TRAIN's: print TREE-EQUAL, or print
# TREE-DIFFERS and exit 1 (main-tree-evidence then backfills a full run on
# main; do not tag until it passes).
#
# Exit status: 0 every pull request merged and main's tree equals TRAIN's;
# 1 a refusal, a failed merge, MAIN-MOVED or TREE-DIFFERS; 2 a usage error;
# 3 a gh or git call passed its limit (recorded as lib.sh's ops_timeout
# describes; check main before re-running, since a merge may have landed).
#
# Configuration (environment variables):
#   STEWARDSHIP_GH_REPO      repository (default epiphany40223/parishkit)
#   STEWARDSHIP_GIT_REMOTE   git remote for that repository (default origin)
#   STEWARDSHIP_PYTHON       Python 3.11 or newer (default python3)
#   STEWARDSHIP_MERGE_TRIES  merge attempts per pull request (default 6)
#   STEWARDSHIP_POLL_SECONDS pause between merge attempts (default 20)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

usage() {
    echo "usage: $0 [-h] TRAIN PR:SHA [PR:SHA ...]" >&2
}

if [ "${1-}" = -h ] || [ "${1-}" = --help ]; then
    sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
    exit 0
fi
if [ $# -lt 2 ] || [[ $1 == -* ]]; then
    usage
    exit 2
fi
train=$1
shift
for item in "$@"; do
    if ! [[ $item =~ ^[1-9][0-9]*:[0-9a-f]{7,40}$ ]]; then
        ops_refuse "Each pull request must be PR:SHA, such as 123:0123abc, not '$item'"
    fi
done
repo=${STEWARDSHIP_GH_REPO:-epiphany40223/parishkit}
remote=${STEWARDSHIP_GIT_REMOTE:-origin}
python=${STEWARDSHIP_PYTHON:-python3}
tries=${STEWARDSHIP_MERGE_TRIES:-6}
poll=${STEWARDSHIP_POLL_SECONDS:-20}
ops_require_count STEWARDSHIP_MERGE_TRIES "$tries"
ops_require_count STEWARDSHIP_POLL_SECONDS "$poll"
checkout=$(CDPATH='' cd "$here/../.." && pwd)
git=(git -C "$checkout")

# Fetch main from the remote and print its head.
main_head() {
    ops_limited 120 "git fetch of main" "${git[@]}" fetch -q --no-tags "$remote" \
        "+refs/heads/main:refs/remotes/$remote/main"
    "${git[@]}" rev-parse "$remote/main"
}

# 1. The remote, main, the train and the pull requests' commits.
ops_require_github_remote "$checkout" "$remote" "$repo"
main=$(main_head)
if ! train_sha=$("${git[@]}" rev-parse -q --verify "$train^{commit}"); then
    ops_refuse "TRAIN '$train' is not a commit in this checkout; fetch the train branch first"
fi
prs=() heads=()
for item in "$@"; do
    pr=${item%%:*} sha=${item#*:}
    if ! full=$("${git[@]}" rev-parse -q --verify "$sha^{commit}") ||
        ! "${git[@]}" merge-base --is-ancestor "$full" "$train_sha"; then
        ops_refuse "#$pr's head $sha is not in the train $train_sha"
    fi
    prs+=("$pr") heads+=("$full")
done

# Walk main's first-parent chain back to a commit in TRAIN (the train's
# base). Each step must be a merge of a listed head, and together they must
# be the first heads in order: the pull requests a stopped run merged.
moved="main moved since the train was built, so rebuild the train on it and run its CI again"
landed=() cur=$main
while ! "${git[@]}" merge-base --is-ancestor "$cur" "$train_sha"; do
    IFS=$'\n' read -r -d '' -a parents < <("${git[@]}" rev-parse "$cur^@" && printf '\0') || true
    if [ "${#parents[@]}" != 2 ] || [ "${#landed[@]}" -ge "${#heads[@]}" ]; then
        ops_refuse "main ($main) holds $cur, which is not a merge of this train's pull requests; $moved"
    fi
    landed=("${parents[1]}" ${landed[@]+"${landed[@]}"})
    cur=${parents[0]}
done
skip=${#landed[@]}
for ((i = 0; i < skip; i++)); do
    if [ "${landed[$i]}" != "${heads[$i]}" ]; then
        ops_refuse "main ($main) merged ${landed[$i]} where the train has #${prs[$i]}'s ${heads[$i]}; $moved"
    fi
done

# 2. The train's full CI run must have passed, by the release gate's rule.
line=$("$python" "$here/release_evidence.py" select --repo "$repo" \
    --commit "$train_sha" --checkout "$checkout" --remote "$remote") || {
    status=$?
    if [ "$status" = "$OPS_TIMEOUT_STATUS" ]; then
        exit "$OPS_TIMEOUT_STATUS"
    fi
    ops_refuse "Cannot look up the full CI run for the train $train_sha"
}
read -r run status conclusion head _ attempt <<<"$line" || true
if [ "${status:-}/${conclusion:-}" != completed/success ]; then
    ops_refuse "The full CI run deciding for the train $train_sha is '${run:-none}' (${status:-no run}/${conclusion:-}), not a completed success"
fi
echo "evidence: full CI run $run on $head"
if [ "${attempt:-1}" -gt 1 ]; then
    echo "RERUN: full CI run $run passed on attempt $attempt, after its failed jobs were re-run"
fi

# 3-5. Merge each pull request in order, checking main after each.
for i in "${!prs[@]}"; do
    pr=${prs[$i]} sha=${heads[$i]}
    if [ "$i" -lt "$skip" ]; then
        echo "already merged #$pr"
        continue
    fi
    current=$(ops_gh pr view "$pr" --repo "$repo" --json headRefOid -q .headRefOid)
    if [ "$current" != "$sha" ]; then
        ops_refuse "#$pr's head is now $current, not the tested $sha; stopping before it merges"
    fi
    # Assigned first, so that a failed gh call stops the script (set -e
    # ignores a failure inside a test's command substitution).
    base=$(ops_gh pr view "$pr" --repo "$repo" --json baseRefName -q .baseRefName)
    if [ "$base" != main ]; then
        ops_gh pr edit "$pr" --repo "$repo" --base main >/dev/null
        ops_log "retargeted #$pr from $base to main"
    fi
    # Marking a draft ready starts no CI run (#624).
    draft=$(ops_gh pr view "$pr" --repo "$repo" --json isDraft -q .isDraft)
    if [ "$draft" = true ]; then
        ops_gh pr ready "$pr" --repo "$repo" >/dev/null
    fi
    start=$SECONDS merged=0
    for try in $(seq 1 "$tries"); do
        if ops_gh pr merge "$pr" --repo "$repo" --admin --merge --match-head-commit "$sha" >/dev/null; then
            merged=1
            break
        fi
        if [ "$try" -lt "$tries" ]; then
            sleep "$poll"
        fi
    done
    if [ "$merged" != 1 ]; then
        ops_log "gave up merging #$pr: limit $tries attempts, elapsed $((SECONDS - start)) s"
        ops_gh pr view "$pr" --repo "$repo" --json mergeable,mergeStateStatus >&2 || true
        ops_refuse "Could not merge #$pr"
    fi
    after=$(main_head)
    parents=$("${git[@]}" rev-parse "$after^@")
    if [ "$parents" != "$main"$'\n'"$sha" ]; then
        echo "MAIN-MOVED: after merging #$pr, main is $after, not a merge of $main and $sha; something else landed on main, so stop and rebuild the train"
        exit 1
    fi
    main=$after
    echo "merged #$pr as $main"
    # Usually nothing to cancel (marking a draft ready starts no run, #624),
    # but a draft run still in progress would hold runners for nothing.
    branch=$(ops_gh pr view "$pr" --repo "$repo" --json headRefName -q .headRefName)
    for id in $(ops_gh run list --repo "$repo" --branch "$branch" --limit 20 \
        --json databaseId,status -q '.[] | select(.status != "completed") | .databaseId'); do
        ops_gh run cancel "$id" --repo "$repo" >/dev/null || ops_log "could not cancel run $id of #$pr"
    done
done

# The train's run is main's evidence only if the trees are identical.
got=$("${git[@]}" rev-parse "$main^{tree}")
want=$("${git[@]}" rev-parse "$train_sha^{tree}")
if [ "$got" != "$want" ]; then
    echo "TREE-DIFFERS: main $main has tree $got, but the train $train_sha tested $want; main-tree-evidence will dispatch a full run on main, so do not tag until it passes"
    exit 1
fi
echo "TREE-EQUAL: main $main has the train's tree $got"
