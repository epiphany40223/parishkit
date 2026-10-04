#!/usr/bin/env bash
# Read-only checks to run just before a Production upgrade: background task
# runs that have not finished, outbox messages still in flight, and Family
# sessions active in the last 15 minutes. It changes nothing: the queries
# (predeploy.sql, beside this script) run as the operator (superuser)
# login, inside a BEGIN READ ONLY transaction that they roll back. See
# docs/guides/stewardship-operator-scripts.md.
#
# Usage: tools/stewardship-ops/predeploy-check.sh [-h]
#
# Exit status: 0 nothing running or being submitted; 1 something an
# upgrade would interrupt is running or being submitted (wait, then run it
# again), or the check could not run; 2 a usage error; 3 the ssh call
# passed its limit.
#
# Configuration (environment variables), with lib.sh's ssh and psql
# settings (STEWARDSHIP_PROJECT, STEWARDSHIP_DATABASE, ...):
#   STEWARDSHIP_HOST      ssh destination (required)
#   STEWARDSHIP_TIMEZONE  time zone the times are shown in (default UTC)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

case "$#:${1-}" in
    0:) ;;
    1:-h | 1:--help)
        sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
        exit 0
        ;;
    *)
        echo "usage: $0 [-h]" >&2
        exit 2
        ;;
esac
ops_require_host
tz=$(ops_timezone)

status=0
output=$(ops_psql -q -v tz="$tz" <"$here/predeploy.sql") || status=$?
if [ "$status" = "$OPS_TIMEOUT_STATUS" ]; then
    exit "$OPS_TIMEOUT_STATUS"
fi
printf '%s\n' "$output"
if [ "$status" != 0 ]; then
    ops_refuse "The checks did not run (ssh or psql exited $status; its error is above), so the deployment is not known to be clear"
fi
blocking=$(printf '%s\n' "$output" | sed -n 's/^predeploy: blocking=//p')
if [ -z "$blocking" ]; then
    ops_refuse "The checks printed no blocking count, so the deployment is not known to be clear"
fi
if [ "$blocking" != 0 ]; then
    echo "Not clear to upgrade: $blocking task run(s) running or message(s) being submitted." >&2
    exit 1
fi
echo "Clear to upgrade: nothing running or being submitted." >&2
