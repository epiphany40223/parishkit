# shellcheck shell=bash
# Shared helpers for the stewardship operator scripts in this directory
# (docs/guides/stewardship-operator-scripts.md). Sourced, never run.
#
# Nothing here names a host, deployment or credential: the repository is
# public, so every such value comes from the operator's environment.
#
# Exit statuses shared by every script: 1 a refusal or failure, 2 a usage
# error (as tools/stewardship-upgrade.sh), 3 a wait or remote call that
# passed its limit.
#
# Configuration (environment variables) read here:
#   STEWARDSHIP_HOST            ssh destination (required by ops_ssh)
#   STEWARDSHIP_PROJECT         Compose project name (default stewardship);
#                               the database container is
#                               ${STEWARDSHIP_PROJECT}-postgres-1
#   STEWARDSHIP_DATABASE        database name (default stewardship)
#   STEWARDSHIP_REMOTE_SECONDS  limit for one ssh call (default 360)
#   STEWARDSHIP_GH_SECONDS      limit for one gh call (default 120)
#   STEWARDSHIP_STATEMENT_TIMEOUT  PostgreSQL statement_timeout for every
#                               query (default 5min)
#   STEWARDSHIP_OPS_LOG         file that timeouts are appended to (default
#                               ~/.local/state/parishkit/stewardship-ops.log)

OPS_TIMEOUT_STATUS=3

# Ctrl-C ends a script with status 130 once its current command returns;
# ops_limited stops a limited call at once rather than waiting for it.
trap 'exit 130' INT

# Print a UTC-timestamped progress line on stderr, so stdout stays the
# script's own output (query results, a digest) and can be redirected.
ops_log() {
    printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2
}

# Print a refusal on stderr and stop with status 1.
ops_refuse() {
    printf '%s; refusing.\n' "$*" >&2
    exit 1
}

# Refuse unless $2 is a whole number (0 or more); $1 names the setting.
ops_require_count() {
    if ! [[ $2 =~ ^[0-9]+$ ]]; then
        ops_refuse "$1 must be a whole number of 0 or more, not '$2'"
    fi
}

# Refuse unless STEWARDSHIP_HOST is set, and refuse a project, database or
# limit that is not plainly what it claims to be, before anything reaches
# ssh. Values are %q-quoted for the host anyway; this keeps a typo from
# turning into a confusing remote error.
ops_require_host() {
    if [ -z "${STEWARDSHIP_HOST:-}" ]; then
        ops_refuse "Set STEWARDSHIP_HOST to the ssh destination"
    fi
    if [[ $STEWARDSHIP_HOST == -* ]]; then
        ops_refuse "STEWARDSHIP_HOST must not start with '-'"
    fi
    if ! [[ ${STEWARDSHIP_PROJECT:-stewardship} =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
        ops_refuse "STEWARDSHIP_PROJECT must be a Compose project name"
    fi
    if ! [[ ${STEWARDSHIP_DATABASE:-stewardship} =~ ^[a-z_][a-z0-9_]*$ ]]; then
        ops_refuse "STEWARDSHIP_DATABASE must be a plain database name"
    fi
    if ! [[ ${STEWARDSHIP_STATEMENT_TIMEOUT:-5min} =~ ^([1-9][0-9]{3,}ms|[1-9][0-9]*(s|min))$ ]]; then
        ops_refuse "STEWARDSHIP_STATEMENT_TIMEOUT must be at least a second, like 300s or 5min"
    fi
    if ! [[ ${STEWARDSHIP_REMOTE_SECONDS:-360} =~ ^[1-9][0-9]*$ ]]; then
        ops_refuse "STEWARDSHIP_REMOTE_SECONDS must be a whole number of seconds, 1 or more"
    fi
}

# Refuse a time zone that is not an IANA-style name, such as UTC or
# America/New_York; prints the zone to use.
ops_timezone() {
    local tz=${STEWARDSHIP_TIMEZONE:-UTC}
    if ! [[ $tz =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$ ]]; then
        ops_refuse "STEWARDSHIP_TIMEZONE must be a time zone name such as UTC or Area/City"
    fi
    printf '%s\n' "$tz"
}

# Record a timeout, per the project rule that every timeout says what timed
# out, its limit and the time elapsed, and keeps that record: on stderr and
# appended to STEWARDSHIP_OPS_LOG. Stops with status 3. Inside $(...) it ends
# only that subshell, whose status 3 the caller then sees.
#   $1  what was being waited for
#   $2  the limit, with its unit
#   $3  the $SECONDS value when the wait began
ops_timeout() {
    local line log dir
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $(basename "$0"): timed out waiting for $1: limit $2, elapsed $((SECONDS - $3)) s"
    printf '%s\n' "$line" >&2
    log=${STEWARDSHIP_OPS_LOG:-${HOME:-/tmp}/.local/state/parishkit/stewardship-ops.log}
    dir=$(dirname "$log")
    # A log that cannot be written must not hide the timeout itself.
    if mkdir -p "$dir" 2>/dev/null && printf '%s\n' "$line" >>"$log" 2>/dev/null; then
        printf 'Recorded in %s\n' "$log" >&2
    else
        printf 'Could not append this to %s\n' "$log" >&2
    fi
    exit "$OPS_TIMEOUT_STATUS"
}

# Run a command with a time limit, recording a timeout if it passes.
#   $1  the limit in seconds
#   $2  what the command is, for the timeout record
#   $@  the command; it keeps this function's stdin
# Plain bash rather than timeout(1), which a Mac lacks without coreutils.
# A watcher subshell sleeps for the limit and then stops the command. Its
# output goes to /dev/null so that, inside $(...), nothing it starts holds a
# pipe open; it records its sleep's pid so that a call that finishes in time
# can stop that sleep too instead of leaving it behind.
ops_limited() {
    local limit=$1 what=$2 start=$SECONDS pid watcher status=0 flag tries=0
    shift 2
    flag=$(mktemp)
    # An explicit stdin redirection: a background job would otherwise read
    # /dev/null.
    "$@" <&0 &
    pid=$!
    (
        sleep "$limit" &
        echo "$!" >"$flag.sleep"
        if wait "$!"; then
            echo late >"$flag"
            kill -TERM "$pid"
        fi
    ) </dev/null >/dev/null 2>&1 &
    watcher=$!
    # Background jobs start with SIGINT ignored, and wait does not return on
    # an untrapped one, so without this Ctrl-C would not stop the script
    # until the call ended: stop the call and the watcher, and end here.
    trap 'kill -TERM "$pid" "$watcher" 2>/dev/null; ops_stop_sleep "$flag"; rm -f "$flag"; exit 130' INT TERM
    wait "$pid" || status=$?
    trap 'exit 130' INT
    trap - TERM
    # The watcher writes its sleep's pid at once; wait (briefly) for it.
    while [ ! -s "$flag.sleep" ] && [ "$tries" -lt 200 ]; do
        tries=$((tries + 1))
        sleep 0.01
    done
    kill "$watcher" 2>/dev/null || true
    ops_stop_sleep "$flag"
    wait "$watcher" 2>/dev/null || true
    if [ -s "$flag" ]; then
        rm -f "$flag"
        ops_timeout "$what" "$limit s" "$start"
    fi
    rm -f "$flag"
    return "$status"
}

# Stop the watcher's sleep, whose pid ops_limited's watcher wrote to
# "$1.sleep", and remove that file.
ops_stop_sleep() {
    if [ -s "$1.sleep" ]; then
        kill "$(cat "$1.sleep")" 2>/dev/null || true
    fi
    rm -f "$1.sleep"
}

# Run a command on the host, within STEWARDSHIP_REMOTE_SECONDS. ssh joins
# its arguments into one string for the remote shell, so each argument is
# %q-quoted to arrive unchanged. The ssh options make a dead network fail
# within about a minute instead of hanging.
ops_ssh() {
    local remote
    remote=$(printf '%q ' "$@")
    # shellcheck disable=SC2029 # $remote is meant to expand here, %q-quoted
    ops_limited "${STEWARDSHIP_REMOTE_SECONDS:-360}" "ssh to the host ($1 ${2-})" \
        ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
        -- "$STEWARDSHIP_HOST" "$remote"
}

# Run psql in the deployment's database container, feeding it this
# function's stdin as the script (-f -). Extra arguments (such as -v
# name=value or -At) go to psql. pk_stewardship_operator is the operator
# (superuser) login, so these scripts stay read-only only because each of
# their scripts runs in a BEGIN READ ONLY or rolled-back transaction. -X
# skips any psqlrc on the host, ON_ERROR_STOP makes the first failing
# statement fail the run, and statement_timeout bounds every query.
ops_psql() {
    local project=${STEWARDSHIP_PROJECT:-stewardship}
    ops_ssh docker exec -i "${project}-postgres-1" psql -X \
        -U pk_stewardship_operator -d "${STEWARDSHIP_DATABASE:-stewardship}" \
        -v ON_ERROR_STOP=1 "$@" \
        -c "SET statement_timeout = '${STEWARDSHIP_STATEMENT_TIMEOUT:-5min}'" -f -
}

# Run gh within STEWARDSHIP_GH_SECONDS (default 120) per call, so a hung
# request cannot outlast a watch limit unnoticed; a call that passes it is
# recorded like any timeout.
ops_gh() {
    local limit=${STEWARDSHIP_GH_SECONDS:-120}
    if ! [[ $limit =~ ^[1-9][0-9]*$ ]]; then
        ops_refuse "STEWARDSHIP_GH_SECONDS must be a whole number of seconds, 1 or more"
    fi
    ops_limited "$limit" "gh $1 $2" gh "$@"
}

# Refuse unless $2 is a PostgreSQL timestamp in a plain form: a date,
# optionally a time with seconds and fraction, optionally Z, UTC or an
# offset (such as 2026-10-03, 2026-10-03 12:00Z, 2026-10-03T12:00:00-04:00),
# or the word now. $1 names the option.
ops_require_timestamp() {
    local re='^[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?)? ?(Z|UTC|[+-][0-9]{2}(:?[0-9]{2})?)?$'
    if [ "$2" != now ] && ! [[ $2 =~ $re ]]; then
        ops_refuse "$1 must be a timestamp such as '2026-10-03 12:00Z' or now, not '$2'"
    fi
}
