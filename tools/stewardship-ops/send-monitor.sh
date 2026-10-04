#!/usr/bin/env bash
# Watch a Family mail send from the operator's side, one line per poll: the
# outbox messages of one purpose and mode created since SINCE, by state; how
# many the mail provider accepted in the last minute; the first and last
# acceptance; and how many deadlock and error lines the deployment's
# containers logged since about the previous line. It stops once the send
# has settled (a poll saw pending work, and now no message is pending,
# submitting or waiting to retry) or at the watch limit, so start it before
# or during the send. Read-only: the query (send-monitor.sql, beside this
# script) runs as the operator (superuser) login inside a BEGIN READ ONLY
# transaction that it rolls back. The Admin portal's Family email progress
# page is the Administrator's view of the same send. See
# docs/guides/stewardship-operator-scripts.md.
#
# Usage: tools/stewardship-ops/send-monitor.sh [-h] [-p PURPOSE] [-m MODE] [-s SINCE] [-n MINUTES]
#   -p PURPOSE  outbox purpose (default initial; also reminder, receipt,
#               family_test, daily_digest, weekly_digest, ...)
#   -m MODE     production or testing (default production)
#   -s SINCE    count only messages created from this timestamp on, such
#               as '2026-10-03 12:00Z' (default five minutes before start)
#   -n MINUTES  watch limit (default 90)
#
# Exit status: 0 the send settled; 1 a refusal; 2 a usage error; 3 the limit
# passed first (recorded as lib.sh's ops_timeout describes).
#
# Configuration (environment variables), with lib.sh's ssh and psql
# settings (STEWARDSHIP_PROJECT, STEWARDSHIP_DATABASE, ...):
#   STEWARDSHIP_HOST          ssh destination (required)
#   STEWARDSHIP_TIMEZONE      time zone the times are shown in (default UTC)
#   STEWARDSHIP_POLL_SECONDS  pause between lines (default 60)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

usage() {
    echo "usage: $0 [-h] [-p PURPOSE] [-m MODE] [-s SINCE] [-n MINUTES]" >&2
}

# Five minutes before now, in UTC, with GNU date or the BSD date on a Mac.
default_since() {
    local n=$(($(date -u +%s) - 300))
    date -u -d "@$n" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$n" +%Y-%m-%dT%H:%M:%SZ
}

purpose=initial mode=production minutes=90 since=""
while getopts ':hp:m:s:n:' opt; do
    case $opt in
        h)
            sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
            exit 0
            ;;
        p) purpose=$OPTARG ;;
        m) mode=$OPTARG ;;
        s) since=$OPTARG ;;
        n) minutes=$OPTARG ;;
        *)
            usage
            exit 2
            ;;
    esac
done
shift $((OPTIND - 1))
if [ $# -ne 0 ]; then
    usage
    exit 2
fi
if ! [[ $purpose =~ ^[a-z][a-z_]*$ ]]; then
    ops_refuse "PURPOSE must be an outbox purpose such as initial, not '$purpose'"
fi
if [ "$mode" != production ] && [ "$mode" != testing ]; then
    ops_refuse "MODE must be production or testing, not '$mode'"
fi
since=${since:-$(default_since)}
# A fixed time: now would move with every poll.
if [ "$since" = now ]; then
    ops_refuse "SINCE must be a fixed timestamp, not now"
fi
ops_require_timestamp SINCE "$since"
ops_require_count MINUTES "$minutes"
poll=${STEWARDSHIP_POLL_SECONDS:-60}
ops_require_count STEWARDSHIP_POLL_SECONDS "$poll"
ops_require_host
tz=$(ops_timezone)
project=${STEWARDSHIP_PROJECT:-stewardship}

# Counted on the host so that only two numbers cross ssh, over the
# containers Compose labels as this project's. One line, because a
# multi-line command would reach the host's shell in bash-only $'...'
# quoting. Each container's log since a little before the previous line.
logs_since="$((poll + 10))s"
# shellcheck disable=SC2016 # expanded by the host's shell, not here
counts='p=$1 s=$2; d=0; e=0; for c in $(docker ps --filter "label=com.docker.compose.project=$p" --format "{{.Names}}"); do l=$(docker logs --since "$s" "$c" 2>&1); n=$(printf "%s\n" "$l" | grep -ci deadlock); d=$((d + n)); n=$(printf "%s\n" "$l" | grep -cE "ERROR|CRITICAL|Traceback"); e=$((e + n)); done; echo "deadlock=$d errors=$e"'

ops_log "watching $mode $purpose messages created since $since"
start=$SECONDS
seen_work=0
while :; do
    # A failed poll (a dropped ssh connection, or one past its limit, which
    # is recorded) is shown and retried at the next poll rather than ending
    # the watch; the watch limit still applies.
    if ! line=$(ops_psql -qAt -F ' | ' -v purpose="$purpose" -v mode="$mode" \
        -v since="$since" -v tz="$tz" <"$here/send-monitor.sql"); then
        line="query failed"
    fi
    if ! logs=$(ops_ssh sh -c "$counts" sh "$project" "$logs_since" </dev/null); then
        logs="log counts failed"
    fi
    echo "$line | $logs"
    # The second field is "state=n state=n ..." or "none". Settled needs a
    # poll that saw unfinished work first, so a watch started before the
    # send was planned does not stop on an empty or stale picture.
    states=$(printf '%s\n' "$line" | awk -F ' [|] ' 'NF > 1 {print $2}')
    if [[ $states =~ (pending|submitting|retry_wait)= ]]; then
        seen_work=1
    elif [ "$seen_work" = 1 ] && [ -n "$states" ] && [ "$states" != none ]; then
        echo "settled: no $mode $purpose message is pending, submitting or waiting to retry"
        exit 0
    fi
    if [ $((SECONDS - start)) -ge $((minutes * 60)) ]; then
        ops_timeout "the $mode $purpose send to settle (last: ${states:-unknown})" "$minutes minutes" "$start"
    fi
    sleep "$poll"
done
