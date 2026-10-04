#!/usr/bin/env bash
# Run the read-only mail send report
# (docs/guides/stewardship-mail-send-report.md) against a deployment. The
# SQL is read from that guide's "The report" section each time this runs,
# never copied here, so the guide and this script cannot drift. It runs as
# the operator (superuser) login and changes nothing only because the
# report runs in one transaction that it rolls back, read-only after its
# temporary view; statement_timeout (lib.sh) bounds every query. See
# docs/guides/stewardship-operator-scripts.md.
#
# Usage: tools/stewardship-ops/send-report.sh [-h] --since TIME --until TIME
#            [--purpose PURPOSE] [--definition UUID]
#        tools/stewardship-ops/send-report.sh --print-sql
#   --since, --until  the window of outcomes, as timestamps such as
#                     '2026-10-03 12:00Z' (UTC is safest) or now
#   --purpose         one Family mail purpose (initial, reminder, receipt,
#                     family_test, daily_digest, weekly_digest) or an
#                     Administrator alert purpose (operational,
#                     security_event); empty or omitted means all Family mail
#   --definition      one schedule definition's UUID
#   --print-sql       print the SQL taken from the guide and stop
#
# Exit status: 0 the report ran; 1 a refusal or failure; 2 a usage error;
# 3 the ssh call passed its limit.
#
# Configuration (environment variables), with lib.sh's ssh and psql
# settings (STEWARDSHIP_PROJECT, STEWARDSHIP_DATABASE,
# STEWARDSHIP_STATEMENT_TIMEOUT, ...):
#   STEWARDSHIP_HOST      ssh destination (required unless --print-sql)

set -euo pipefail
here=$(CDPATH='' cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"
guide=$here/../../docs/guides/stewardship-mail-send-report.md

usage() {
    echo "usage: $0 [-h] --since TIME --until TIME [--purpose PURPOSE] [--definition UUID] | --print-sql" >&2
}

since="" until="" purpose="" definition="" print=0
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help)
            sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"
            exit 0
            ;;
        --print-sql)
            print=1
            shift
            ;;
        --since | --until | --purpose | --definition)
            if [ $# -lt 2 ]; then
                usage
                exit 2
            fi
            case $1 in
                --since) since=$2 ;;
                --until) until=$2 ;;
                --purpose) purpose=$2 ;;
                --definition) definition=$2 ;;
            esac
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

# The report is the guide's first ```sql block after "## The report". Its
# shape is checked so that a reworded guide fails here, loudly, instead of
# running half a report.
if [ ! -f "$guide" ]; then
    ops_refuse "Missing $guide"
fi
sql=$(awk '
    /^## The report$/ { section = 1; next }
    section && /^```sql$/ { inside = 1; next }
    inside && /^```$/ { exit }
    inside { print }
' "$guide")
first=$(printf '%s\n' "$sql" | head -n 1)
last=$(printf '%s\n' "$sql" | tail -n 1)
if [ "$first" != "BEGIN;" ] || [ "$last" != "ROLLBACK;" ]; then
    ops_refuse "The guide's \"The report\" SQL block was not found, or does not run from BEGIN; to ROLLBACK;"
fi
if [ "$print" = 1 ]; then
    printf '%s\n' "$sql"
    exit 0
fi

if [ -z "$since" ] || [ -z "$until" ]; then
    echo "Both --since and --until are required." >&2
    usage
    exit 2
fi
ops_require_timestamp --since "$since"
ops_require_timestamp --until "$until"
case $purpose in
    "" | initial | reminder | receipt | family_test | daily_digest | weekly_digest | operational | security_event) ;;
    *) ops_refuse "Unknown --purpose '$purpose'" ;;
esac
if [ -n "$definition" ] &&
    ! [[ $definition =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    ops_refuse "--definition must be a lowercase schedule definition UUID"
fi
ops_require_host

# ops_psql feeds the report to psql on stdin, after setting
# statement_timeout, so a runaway query on a big outbox ends on its own.
ops_psql -v since="$since" -v until="$until" -v purpose="$purpose" \
    -v definition="$definition" <<<"$sql"
