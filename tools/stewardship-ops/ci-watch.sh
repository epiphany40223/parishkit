#!/usr/bin/env bash
# Watch one GitHub Actions run and stop at the first sign of its outcome:
# as soon as any job fails (without waiting for the other shards), when the
# run completes, or, given a pull request number, as soon as that pull
# request has a merge conflict. See
# docs/guides/stewardship-operator-scripts.md.
#
# Usage: tools/stewardship-ops/ci-watch.sh [-h] RUN_ID [PR_NUMBER]
#
# Exit status: 0 the run completed successfully; 1 a job failed, the run
# completed without success, the pull request conflicts, or a refusal
# (including a run id gh cannot read); 2 a usage error; 3 the watch limit
# passed first (recorded as lib.sh's ops_timeout describes).
#
# Configuration (environment variables):
#   STEWARDSHIP_GH_REPO        repository (default epiphany40223/parishkit)
#   STEWARDSHIP_WATCH_MINUTES  watch limit in minutes (default 120)
#   STEWARDSHIP_POLL_SECONDS   pause between checks (default 60)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

usage() {
    echo "usage: $0 [-h] RUN_ID [PR_NUMBER]" >&2
}

if [ "${1-}" = -h ] || [ "${1-}" = --help ]; then
    sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
    exit 0
fi
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    usage
    exit 2
fi
run=$1 pr=${2-}
if ! [[ $run =~ ^[0-9]+$ ]]; then
    ops_refuse "RUN_ID must be a numeric Actions run id, not '$run'"
fi
if [ -n "$pr" ] && ! [[ $pr =~ ^[0-9]+$ ]]; then
    ops_refuse "PR_NUMBER must be a pull request number, not '$pr'"
fi
repo=${STEWARDSHIP_GH_REPO:-epiphany40223/parishkit}
minutes=${STEWARDSHIP_WATCH_MINUTES:-120}
poll=${STEWARDSHIP_POLL_SECONDS:-60}
ops_require_count STEWARDSHIP_WATCH_MINUTES "$minutes"
ops_require_count STEWARDSHIP_POLL_SECONDS "$poll"

start=$SECONDS
first=1
while :; do
    # One line: status|conclusion|names of the jobs that failed so far.
    # A failed query (a network blip during a two-hour watch) is retried at
    # the next poll rather than ending the watch; the limit still applies.
    # Only the first query must succeed: a run id gh cannot read is refused,
    # and a first query that timed out (already recorded) exits 3.
    query=0
    state=$(ops_gh run view "$run" --repo "$repo" --json status,conclusion,jobs \
        -q '"\(.status)|\(.conclusion)|" + ([.jobs[] | select(.conclusion == "failure" or .conclusion == "timed_out") | .name] | join(", "))') || query=$?
    if [ "$query" != 0 ]; then
        if [ "$first" = 1 ] && [ "$query" = "$OPS_TIMEOUT_STATUS" ]; then
            exit "$OPS_TIMEOUT_STATUS"
        fi
        if [ "$first" = 1 ]; then
            ops_refuse "Cannot read Actions run $run in $repo"
        fi
        state="unreadable||"
    fi
    first=0
    IFS='|' read -r status conclusion failed <<<"$state"
    if [ -n "$failed" ]; then
        echo "FAILED EARLY: run $run ($status): failed jobs: $failed"
        exit 1
    fi
    if [ "$status" = completed ]; then
        if [ "$conclusion" != success ]; then
            echo "FAILED: run $run completed with conclusion '$conclusion'"
            exit 1
        fi
        echo "DONE: run $run completed successfully"
        if [ -n "$pr" ]; then
            # Informational only: the run's outcome is already decided.
            (ops_gh pr view "$pr" --repo "$repo" --json mergeable -q '"mergeable=" + .mergeable') || true
        fi
        exit 0
    fi
    if [ -n "$pr" ] && [ "$(ops_gh pr view "$pr" --repo "$repo" --json mergeable -q .mergeable)" = CONFLICTING ]; then
        echo "MERGE CONFLICT: pull request #$pr conflicts with its base"
        exit 1
    fi
    if [ $((SECONDS - start)) -ge $((minutes * 60)) ]; then
        ops_timeout "Actions run $run (last status $status)" "$minutes minutes" "$start"
    fi
    ops_log "run $run: $status"
    sleep "$poll"
done
