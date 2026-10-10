#!/usr/bin/env bash
# Tag and publish a release from main's head, following the release
# evidence rule that .github/workflows/release.yml enforces: a release needs
# a successful full CI run ("CI (jobs: all)", a workflow_dispatch) on a
# commit whose tree is identical to the tagged commit's or differs only in
# docs-safe paths (release_evidence.py holds the rule and its allowlist). See
# docs/guides/stewardship-operator-scripts.md.
#
# Steps, stopping at the first failure:
#   1. Check that the git remote is the GitHub repository, read main's head
#      from it, and check that its committed pyproject.toml version (read
#      with tomllib, as release.yml does) is VERSION and that tag vVERSION
#      exists neither locally nor on the remote. Land the version bump first.
#      Check too that this gh can run step 6's gh attestation verify with
#      its flags, so an older gh is refused before anything is tagged.
#   2. Ask release_evidence.py which full run decides for that commit (the
#      run release.yml will check). CI_RUN_ID must be that run. Without it,
#      use that run when it passed or is still running, and otherwise
#      dispatch a new full run on main and find it; an already tested tree
#      (such as a train head merged in order) needs no second run.
#   3. Watch the run with ci-watch.sh, then check that it is still the
#      deciding run and that it completed successfully. Whether a run's
#      commit is readable is decided by the remote (release_evidence.py
#      fetches it into an empty repository), as for release.yml's fresh
#      clone; keep a train branch until the release run has passed. When
#      the evidence tree differs (docs only), run release.yml's
#      documentation checks on main's head in a temporary worktree.
#   4. Ask for the tag name to be typed (skipped with --yes), check once
#      more that the run still decides and passed, create the annotated
#      tag vVERSION at that commit and push it; a failed push deletes the
#      local tag again. Pushing a release tag needs a human's explicit
#      authorization; running this is that act. git fetch, ls-remote and
#      push have a two-minute limit each.
#   5. Wait for the new release.yml run (a push of the tag on that commit)
#      and read the published image digest from its "Application image:"
#      log line.
#   6. Verify the image's build provenance with gh attestation verify: it
#      must be signed by this repository's release.yml for the tag, on a
#      GitHub-hosted runner (#392 M2). Only then print the digest on stdout;
#      a failed verification prints nothing there and exits 1.
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
#   STEWARDSHIP_PYTHON         Python 3.11 or newer (default python3); with
#                              pymarkdown, pytest and the project's
#                              development requirements when the evidence
#                              tree differs in documentation
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

# Step 6 verifies the image with gh attestation verify; a gh too old for it
# or its flags would otherwise fail there, after the tag is pushed, with a
# misleading "did not verify". Ask its help text now, before any tag exists.
help_status=0
gh_help=$(ops_gh attestation verify --help 2>&1) || help_status=$?
if [ "$help_status" = "$OPS_TIMEOUT_STATUS" ]; then
    exit "$OPS_TIMEOUT_STATUS"
fi
for flag in --signer-workflow --source-ref --deny-self-hosted-runners; do
    if [ "$help_status" -ne 0 ] || ! grep -q -e "$flag" <<<"$gh_help"; then
        ops_refuse "This gh ($(command -v gh)) cannot run gh attestation verify $flag; upgrade gh before releasing"
    fi
done

# The ids of the newest workflow_dispatch CI runs on this commit, newest first.
dispatch_runs() {
    ops_gh run list --repo "$repo" --workflow ci.yml --event workflow_dispatch \
        --commit "$sha" --limit 20 --json databaseId -q '.[].databaseId'
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

# Set chosen, status, conclusion, head and docs from the deciding full run
# for main's head, as release.yml will choose it (all empty when none
# qualifies). A failed lookup ends the script.
# The script limits each git and gh call itself (logging what, limit and
# elapsed on stderr) and exits 3 past a limit, recorded here too. Once a run
# is named, the script also reads it directly (#730) and re-reads a run
# listing that omits it, shows it in a stale state or lets another run
# decide, a bounded number of times, min(STEWARDSHIP_POLL_SECONDS, 10)
# seconds apart, scaled per retry. The listing decides, as for release.yml,
# and only when it agrees with the direct read: a persistent disagreement
# decides no run, so neither a stale listed success nor a run release.yml
# could not see is ever tagged.
evidence() {
    local line found=0 start=$SECONDS given=()
    if [ -n "$run" ]; then
        given=(--run "$run" --retry-seconds "$((poll < 10 ? poll : 10))")
    fi
    line=$("$python" "$here/release_evidence.py" select --repo "$repo" \
        --commit "$sha" --checkout "$checkout" --remote "$remote" \
        ${given[@]+"${given[@]}"}) || found=$?
    if [ "$found" = "$OPS_TIMEOUT_STATUS" ]; then
        ops_timeout "a git or gh call in the release evidence lookup (named above)" "its own limit" "$start"
    fi
    if [ "$found" != 0 ]; then
        ops_refuse "Cannot look up the release evidence for $sha"
    fi
    read -r chosen status conclusion head docs <<<"$line" || true
}

# Documentation is a test input. When the evidence tree differs from main's
# head (docs-safe paths only), run the documentation checks release.yml will
# run (Markdown lint and release_evidence.py's docs-tests) on main's head in
# a temporary worktree, so that a failure refuses here, before the tag is
# pushed. They need STEWARDSHIP_PYTHON to be a development environment.
docs_checks() {
    local checked=0
    if [ "${docs:-0}" = 0 ]; then
        return 0
    fi
    docs_tree=$(mktemp -d)/tree
    # Remove the worktree however this ends: a refusal, a timeout (exit 3)
    # or Ctrl-C (exit 130) all leave through EXIT.
    trap remove_docs_tree EXIT
    ops_limited 120 "git worktree add of $sha" "${git[@]}" worktree add -q --detach "$docs_tree" "$sha"
    # Every step must pass: `|| checked=$?` turns off set -e inside the
    # subshell, so each step exits on its own failure.
    (
        cd "$docs_tree" || exit 1
        export PYTHONPATH=$docs_tree/src
        IFS=$'\n' read -r -d '' -a md < <(git ls-files '*.md' && printf '\0') || exit 1
        IFS=$'\n' read -r -d '' -a tests < <("$python" tools/stewardship-ops/release_evidence.py docs-tests && printf '\0') || exit 1
        if [ "${#md[@]}" = 0 ] || [ "${#tests[@]}" = 0 ]; then
            echo "No Markdown files or no documentation tests to run on $sha" >&2
            exit 1
        fi
        ops_log "documentation checks on $sha: Markdown lint and ${tests[*]}"
        ops_limited 600 "Markdown lint of $sha" "$python" -m pymarkdown --config .pymarkdown.json scan "${md[@]}" >&2 || exit $?
        ops_limited 1200 "documentation tests of $sha" "$python" -m pytest "${tests[@]}" \
            --ds=parishkit.stewardship.settings.test -p no:cacheprovider -q >&2 || exit $?
    ) || checked=$?
    remove_docs_tree
    trap - EXIT
    if [ "$checked" = "$OPS_TIMEOUT_STATUS" ]; then
        exit "$OPS_TIMEOUT_STATUS"
    fi
    if [ "$checked" != 0 ]; then
        ops_refuse "The documentation checks failed on $sha; not tagging"
    fi
}

# Remove docs_checks' temporary worktree, or say where it was left.
remove_docs_tree() {
    if [ -n "${docs_tree:-}" ] && [ -d "$docs_tree" ]; then
        if ! "${git[@]}" worktree remove --force "$docs_tree" 2>/dev/null; then
            echo "Could not remove the temporary worktree $docs_tree; remove it with git worktree remove --force" >&2
        fi
    fi
    if [ -n "${docs_tree:-}" ]; then
        rmdir "$(dirname "$docs_tree")" 2>/dev/null || true
    fi
    docs_tree=""
}

# The documentation checks need a development environment; check for it
# before waiting on a long CI run rather than after.
require_docs_tools() {
    if [ "${docs:-0}" != 0 ] &&
        ! "$python" -c 'import django, pymarkdown, pytest, pytest_django' 2>/dev/null; then
        ops_refuse "The evidence tree differs from $sha in documentation, so the documentation checks must run here, but $python cannot import django, pymarkdown, pytest and pytest_django; set STEWARDSHIP_PYTHON to a development environment (requirements.txt installed)"
    fi
}

# 2. The CI run: the one named, an existing passing or pending one, or a
# freshly dispatched one.
evidence
if [ -n "$run" ]; then
    if [ "${chosen:-}" = "$run" ] && [ "$status" = unreadable ]; then
        ops_refuse "The commit $head of CI run $run cannot be fetched from $remote, so release.yml could not verify it; keep (or restore) the branch that holds it, or dispatch a full run on main"
    fi
    if [ "${chosen:-}" != "$run" ]; then
        ops_refuse "CI run $run is not the full CI run that release.yml will check for $sha (that is '${chosen:-none}'); see release_evidence.py"
    fi
elif [ -n "${chosen:-}" ] && [ "$status" != unreadable ] &&
    { [ "$status" != completed ] || [ "$conclusion" = success ]; }; then
    run=$chosen
    ops_log "reusing full CI run $run on $head ($docs docs-safe paths differ)"
else
    if [ "${status:-}" = unreadable ]; then
        ops_log "full CI run $chosen's commit $head cannot be read; a new full run on main will decide instead"
    fi
    before=$(dispatch_runs)
    ops_log "dispatching CI on main"
    ops_gh workflow run ci.yml --repo "$repo" --ref main -f jobs=all
    run=$(await_new_run "the dispatched CI run on $sha to appear (if main moved, it ran on the new head)" dispatch_runs "$before")
fi
ops_log "CI run $run"
require_docs_tools

# 3. Watch it, then check that it still decides and that it passed.
if ! watch "$run"; then
    ops_refuse "CI run $run did not pass; not tagging"
fi

# The deciding run must be $run and must have passed; $1 says when.
require_evidence() {
    evidence
    if [ "${chosen:-}" != "$run" ]; then
        ops_refuse "$1, the full CI run release.yml will check for $sha is '${chosen:-none}', not $run; re-run with it once it passes"
    fi
    if [ "$status/$conclusion" != completed/success ]; then
        ops_refuse "CI run $run is $status/$conclusion, not completed/success"
    fi
}
require_evidence "After the watch"
docs_checks
ops_log "release evidence: CI run $run on $head ($docs docs-safe paths differ from $sha)"

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
require_evidence "Just before tagging"
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
# Only the push step's "Application image:" line names the digest, so no
# other reference in the log (an attestation's, say) can be mistaken for it.
pattern="${image_repo//./\\.}@sha256:[0-9a-f]{64}"
digest=""
for _ in 1 2 3; do
    digest=$( (ops_gh run view "$release" --repo "$repo" --log || true) |
        grep -oE "Application image: \`$pattern\`" | grep -oE "$pattern" | sort -u || true)
    [ -z "$digest" ] || break
    sleep "$poll"
done
if [ -z "$digest" ]; then
    ops_refuse "Release run $release passed but its log names no $image_repo digest; read the release notes"
fi
if [ "$(printf '%s\n' "$digest" | wc -l)" -ne 1 ]; then
    ops_refuse "Release run $release names more than one $image_repo digest; read the release notes"
fi

# 6. The image's provenance: signed by this repository's release workflow
# for this tag, on a GitHub-hosted runner. The package and the repository
# are public, so gh reads the image and its attestation without a registry
# login. Its output goes to stderr, so stdout carries only the digest.
if ! ops_gh attestation verify "oci://$digest" --repo "$repo" \
    --signer-workflow "$repo/.github/workflows/release.yml" \
    --source-ref "refs/tags/$tag" --deny-self-hosted-runners >&2; then
    ops_refuse "The build provenance of $digest did not verify; do not deploy it (see the gh output above and release run $release)"
fi
ops_log "verified the build provenance of $digest"
printf '%s\n' "$digest"
