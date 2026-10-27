#!/usr/bin/env bash
# The VM half of tools/stewardship-local.sh (#476). The laptop half uploads
# this file into the Lima VM over ssh and runs it there as root; nothing in
# it is meant to be run by hand, and nothing in it ever targets another
# host. It follows the deployment runbook's first-installation steps one for
# one (docs/guides/stewardship-deployment-runbook.md#first-installation),
# with the LOCAL profile's image, YAML and credentials, so a local
# deployment is installed by the same code paths as a production one.
#
# Usage (from the laptop half):
#   stewardship-local-vm.sh ROOT PROJECT YAML ENVFILE BUILD SNAPSHOTS COMMAND [ARGS...]
#
# Commands:
#   build TAG                           build the image from the packed checkout
#   up TAG FAMILIES ADMIN_EMAIL DEBUG   first-time install (builds TAG unless present)
#   start                               start the services in the recorded clock mode
#   down                                stop the services (data is never removed)
#   status [MAC_FREE_KB]                service health, Docker and VM disk use, snapshots
#   snapshot NAME [REPLACE MAC_FREE_KB LIMIT]
#                                       check the space, stop, copy the root to
#                                       SNAPSHOTS/NAME with its meta record, restart
#   snapshot-list [MAC_FREE_KB]         the snapshots, what a switch to each would do,
#                                       and the free space
#   snapshot-delete NAME                remove SNAPSHOTS/NAME, then fstrim the VM disk
#   snapshot-restore NAME HOST_SCRIPT   the data-only switch: keep the deployed code,
#                                       restore NAME's data (migrated forward if older)
#   has-snapshot NAME                   exit 0 when SNAPSHOTS/NAME exists
#   reset NAME                          stop, restore SNAPSHOTS/NAME over the root, restart
#   seed-testing HOST_SCRIPT            switch to the post-setup snapshot and move it to
#                                       real time: a Testing-mode draft campaign
#   wipe                                remove the containers and the root (marker required)
#   deploy TAG SCHEMA_CHANGE HOST_SCRIPT [BULK LATENCY [PARISHSOFT]]
#                                       upgrade the running deployment to TAG with the
#                                       scripted upgrade's host half in local mode (OPS-10.10);
#                                       BULK on|off|keep and LATENCY ms|keep choose what it
#                                       renders for BG-12's rehearsal, PARISHSOFT
#                                       real|fake|keep which ParishSoft it reads (#1399)
#   rollback HOST_SCRIPT                its image-only rollback to the image deploy replaced
#   seed RESPONSE_SCALE                 seed the campaign through the local seeder's
#                                       phases under the fake clock (OPS-10.07)
#   rehearse DUE_IN SEND_ONLY TIMEOUT LABEL
#                                       add a Reminder due in DUE_IN minutes, measure its
#                                       send (BG-12 rehearsal) and print the report
#   spike FAMILIES SOURCES DURING_SEND WAIT LABEL
#                                       the launch-day spike check (#392 M3): SOURCES
#                                       shard containers on the ingress network open and
#                                       submit FAMILIES Families' emailed links through
#                                       Caddy, beside a new Reminder send when DURING_SEND
#                                       is 1, for at most WAIT minutes; prints the report
#   wizard                              complete the setup wizard unattended through
#                                       its own service layer, then Create the
#                                       campaign and its first load (LOCAL convenience)
#   finish-setup                        after the browser wizard: acknowledge its frozen
#                                       attempt's credentials (runbook step 5)
#   sign-in EMAIL                       print a local test sign-in link (OPS-10.08)
#   fake-parishsoft WORDS...            change or show the fake ParishSoft's data, or
#                                       run the Admin's full refresh of it (#1780)
#   ca                                  print Caddy's local root certificate
#
# Safety rules (local-environment specification, "Operator script"): every
# destructive command acts only on a root that carries the marker file
# ROOT/.parishkit-local (or, for wipe alone, the in-progress marker a failed
# `up` leaves beside the deployment record), no command removes Docker
# volumes or run/persistent, and a snapshot is restored only after every
# service has stopped.

set -euo pipefail
# The VM's bash is 5.x: a failure inside $(...) must end the run too. The
# stand-in tests may run this half under an older bash without the option;
# the explicit checks below hold there as well.
shopt -s inherit_errexit 2>/dev/null || true

root=$1 project=$2 yaml=$3 envfile=$4 build=$5 snapshots=$6 command=$7
shift 7

marker="$root/.parishkit-local"
installing="$envfile.installing"
services="$root/config/services"
clock_dir="$root/run/local/clock"
# shellcheck disable=SC2054 # the tmpfs options are one comma-separated word
isolated=(docker run --rm --init --network none --user 10001:10001 --read-only
    --cap-drop ALL --security-opt no-new-privileges:true
    --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777)
spec=docs/specs/stewardship/local-environment/spec.md

refuse() {
    echo "$*" >&2
    exit 1
}

began=$(date -u +%s)
last=$began
step() {
    # Timestamp each step and say how long the previous one took.
    now=$(date -u +%s)
    echo "==> $(date -u +%H:%M:%S) (+$((now - last))s) $*"
    last=$now
}
quiet() {
    # Compose progress lines add nothing to a log. With pipefail, a failing
    # Compose command still fails the pipeline this filter ends.
    grep -vE ' (Creat|Start|Stop|Wait|Running|Healthy|Recreat|Remov)' || true
}

wait_until() {
    # wait_until LIMIT_SECONDS DESCRIPTION COMMAND...: poll every five
    # seconds until the command succeeds. On expiry, log what was waited
    # for, the limit and the elapsed time (every timeout in this tool says
    # so), and fail.
    local limit=$1 what=$2 started elapsed
    shift 2
    started=$(date -u +%s)
    while ! "$@"; do
        elapsed=$(( $(date -u +%s) - started ))
        if [ "$elapsed" -ge "$limit" ]; then
            echo "TIMEOUT: gave up waiting for $what after ${elapsed}s (limit ${limit}s)" >&2
            return 1
        fi
        sleep 5
    done
}

timed() {
    # timed LIMIT_SECONDS DESCRIPTION COMMAND...: run one command under a
    # time limit. On the limit (exit 124) or any failure, log what ran, the
    # limit and the elapsed time, then fail.
    local limit=$1 what=$2 started rc=0
    shift 2
    started=$(date -u +%s)
    timeout "$limit" "$@" || rc=$?
    if [ "$rc" -eq 124 ]; then
        echo "TIMEOUT: $what killed after $(( $(date -u +%s) - started ))s (limit ${limit}s)" >&2
        return 1
    elif [ "$rc" -ne 0 ]; then
        echo "FAILED: $what (exit $rc after $(( $(date -u +%s) - started ))s, limit ${limit}s)" >&2
        return "$rc"
    fi
}

require_marker() {
    # The one guard every destructive command shares.
    [ -f "$marker" ] || refuse "$root does not carry the marker $marker; refusing (see $spec)."
}

load_env() {
    # The values `up` recorded for this deployment: UUID, admin email, image,
    # synthetic-parish inputs and the debug-logging switch.
    [ -r "$envfile" ] || refuse "Cannot read $envfile; was 'up' run?"
    # shellcheck disable=SC1090 # written by `up` below
    . "$envfile"
    # The generated Compose files pass this through to every application
    # service; recreating a container with another value would change it.
    export PARISHKIT_DEBUG_LOGGING=${DEBUG:-0}
}

clock_mode() {
    # The persisted clock mode: fake or normal. A missing marker is an
    # error, not a default (specification, "Fake clock").
    local mode
    [ -f "$clock_dir/mode" ] || refuse "No clock-mode marker at $clock_dir/mode; refusing."
    mode=$(cat "$clock_dir/mode")
    case "$mode" in
        fake|normal) echo "$mode" ;;
        *) refuse "Clock-mode marker says '$mode'; expected fake or normal." ;;
    esac
}

# The sentinel OAuth client's ID (runtime_web.LOCAL_OAUTH_SENTINEL; a test
# pins this copy to it).
OAUTH_SENTINEL_ID=local-environment-sentinel.invalid

refuse_real_oauth_client() {
    # A real Google OAuth client cannot run under the fake clock: web refuses
    # to start with one (runtime_web.admit_google_clock, #1399). Refuse before
    # anything starts or stops under the override, so the developer gets this
    # message instead of a web that will not start. Only a parsable client
    # document other than the sentinel is refused.
    local id
    id=$(jq -r '.client_id // empty' "$root/credentials/google_oauth/credential" 2>/dev/null || true)
    if [ -n "$id" ] && [ "$id" != "$OAUTH_SENTINEL_ID" ]; then
        refuse "A real Google OAuth client is installed, and this deployment runs under the fake clock, which it cannot use; reinstall the sentinel OAuth client first (guide: Signing in with Google)."
    fi
}

initial_files() {
    # The initial topology, plus the fake-clock override when the marker says
    # fake: the install steps and the stores then run under the fake clock
    # too, so no row predates the clock (specification, "Fake clock").
    local mode
    mode=$(clock_mode) || exit 1
    printf -- '-f\n%s\n' "$services/compose-initial.json"
    if [ "$mode" = fake ]; then
        [ -f "$services/compose.faketime.json" ] ||
            refuse "Clock mode is fake but $services/compose.faketime.json is missing."
        refuse_real_oauth_client
        printf -- '-f\n%s\n' "$services/compose.faketime.json"
    fi
}

dc0=()
select_initial() {
    # Fill `dc0` with the initial-topology Compose command (see initial_files).
    local text line
    text=$(initial_files)
    local -a files=()
    while IFS= read -r line; do files+=("$line"); done <<<"$text"
    dc0=(docker compose "${files[@]}" -p "$project")
}

dependencies_up() {
    # Start postgres and valkey under the initial file and wait for them;
    # every topology shares these two services.
    select_initial
    timed 210 "starting postgres and valkey" \
        "${dc0[@]}" up --detach --wait --wait-timeout 180 postgres valkey 2>&1 | quiet
}

build_faketime_images() {
    # The libfaketime-derived local images (specification, "Fake clock"):
    # one FROM the application image just built and one each FROM the pinned
    # PostgreSQL and Valkey images, tagged as the rendered override names
    # them. Built here, after provisioning rendered the override, so the tags
    # come from the code's rule and are never copied into this script.
    local override="$services/compose.faketime.json" service base derived
    if [ ! -f "$override" ]; then
        echo "    this image renders no fake-clock override; the clock stays real"
        return 0
    fi
    [ -f "$build/deploy/stewardship/Dockerfile.faketime" ] ||
        refuse "No packed Dockerfile.faketime at $build; refusing."
    step "Building the fake-clock images"
    for service in web postgres valkey; do
        base=$(jq -r --arg s "$service" '.services[$s].image' "$services/compose.json")
        derived=$(jq -r --arg s "$service" '.services[$s].image' "$override")
        if docker image inspect "$derived" >/dev/null 2>&1; then
            echo "    $derived (already built)"
            continue
        fi
        timed 900 "building $derived" docker build --quiet \
            --file "$build/deploy/stewardship/Dockerfile.faketime" \
            --build-arg "BASE=$base" --tag "$derived" "$build/deploy/stewardship" >/dev/null
        echo "    $derived"
    done
}

setup_completed() {
    # t or f from the database: has the setup wizard recorded completion?
    docker compose -f "$services/compose-initial.json" -p "$project" \
        exec -T postgres psql -U pk_stewardship_operator -d stewardship -Atc \
        "SELECT EXISTS (SELECT 1 FROM stewardship_setup_completion)" 2>/dev/null || true
}

compose_files() {
    # The Compose file the deployment must run under, as the dev deploy tool
    # chooses it: compose-initial.json until the setup wizard has recorded
    # completion, then compose.json (or compose-slack.json when that is what
    # already runs). In fake-clock mode the faketime override is added; it
    # is the one deliberate overlay (specification, "Fake clock"). Any
    # refusal here ends the run: inherit_errexit carries set -e into the
    # $(...) that calls this.
    local completed running compose mode
    # Explicit: without inherit_errexit a failed start would not end this
    # subshell, and the caller would go on to start the services.
    dependencies_up >&2 || exit 1
    completed=$(setup_completed)
    running=$(docker compose ls --all --format json |
        jq -r --arg p "$project" '.[] | select(.Name == $p) | .ConfigFiles | split(",")[0]')
    case "$completed" in
        t)
            compose="$services/compose.json"
            [ "$(basename "$running")" = compose-slack.json ] && compose="$running" ;;
        f) compose="$services/compose-initial.json" ;;
        *) refuse "Cannot read the setup completion marker from postgres; refusing." ;;
    esac
    printf -- '-f\n%s\n' "$compose"
    mode=$(clock_mode) || exit 1
    if [ "$mode" = fake ]; then
        [ -f "$services/compose.faketime.json" ] ||
            refuse "Clock mode is fake but $services/compose.faketime.json is missing."
        refuse_real_oauth_client
        printf -- '-f\n%s\n' "$services/compose.faketime.json"
    fi
}

dc=()
select_compose() {
    # Fill `dc` with the Compose command for this deployment.
    local text line
    text=$(compose_files)
    local -a files=()
    while IFS= read -r line; do files+=("$line"); done <<<"$text"
    dc=(docker compose "${files[@]}" -p "$project")
}

healthy() {
    "${dc[@]}" exec -T web pk-stewardship health --config "$services/web.yaml" >/dev/null 2>&1
}

answers() {
    # The application answers the public origin through Caddy's internal TLS.
    # The name must be localhost (the site block), reached at the VM's
    # loopback. Before the setup wizard runs, the application's own answer
    # is a 503 "not configured yet" page, and Caddy's maintenance page is a
    # 503 too, so the status code cannot tell them apart: the Server header
    # does (gunicorn answered, not Caddy alone).
    curl -ksSI --max-time 20 --resolve localhost:8443:127.0.0.1 https://localhost:8443/ 2>/dev/null |
        grep -qi '^server: gunicorn'
}

service_states() {
    # One "service state health exitcode" line per container of the project.
    docker compose -p "$project" ps --all --format json | jq -rs 'flatten | .[] |
        select(.Service != null) |
        "\(.Service) \(.State) \(if .Health == "" then "none" else .Health end) \(.ExitCode)"'
}

all_healthy() {
    # all_healthy [SERVICE]: every container (or the named one) is running
    # and healthy, or running with no health check.
    service_states | awk -v s="${1-}" '
        s != "" && $1 != s { next }
        !($2 == "running" && ($3 == "healthy" || $3 == "none")) { bad = 1 }
        END { exit bad }'
}

start_services() {
    # start_services [--force-recreate]: web first and alone (every other
    # service imports the application at the same time, which on a small VM
    # multiplies web's start-up time), then everything else without a
    # Compose profile, caddy included. The waits are this script's own, so a
    # slow start says how long it took rather than Compose's fail-fast on a
    # container still marked unhealthy from before a stop. After a restore
    # the containers are recreated: an existing container keeps its bind
    # mounts on the inodes it started with, and a restore replaces files.
    local mode
    select_compose
    mode=$(clock_mode)
    step "Starting web ($mode clock mode)"
    "${dc[@]}" up --detach "$@" web 2>&1 | quiet
    wait_until 300 "web to turn healthy" all_healthy web
    step "Starting the other services"
    "${dc[@]}" up --detach "$@" 2>&1 | quiet
    wait_until 300 "every service to turn healthy" all_healthy
    step "Checking"
    wait_until 120 "the web health check" healthy
    wait_until 60 "https://localhost:8443/ to answer" answers
    echo "    all $(service_states | wc -l | tr -d ' ') services running and healthy"
}

stop_services() {
    # `stop`, never `down --volumes`: data is never removed by a stop. Each
    # container gets 90 seconds to finish (the worker's grace period is the
    # longest); one Docker kills after that is reported as exit 137.
    timed 300 "stopping the services" docker compose -p "$project" stop --timeout 90 2>&1 | quiet
    local killed
    killed=$(service_states | awk '$4 == 137 { print $1 }')
    [ -z "$killed" ] || echo "    killed after the 90s stop timeout (exit 137): $(tr '\n' ' ' <<<"$killed")"
}

image_constant() {
    # image_constant MODULE NAME [FALLBACK]: a Python constant from the
    # application image, so the script never copies a value that lives in
    # the code. Prints the fallback (or a note) when the image lacks it.
    local value
    if value=$(docker run --rm --network none --entrypoint python "$IMAGE" -c "
import json
from $1 import $2 as value
if isinstance(value, bytes):
    value = value.decode()
print(value if isinstance(value, str) else json.dumps(value), end='')" 2>/dev/null); then
        printf '%s' "$value"
    else
        printf '%s' "${3:-(not in this image yet)}"
    fi
}

private_file() {
    # private_file PATH < content: an owner-only file of the application uid.
    local tmp
    tmp=$(mktemp)
    cat >"$tmp"
    install -o 10001 -g 10001 -m 0600 "$tmp" "$1"
    rm -f "$tmp"
}

ensure_backup_key() {
    # The backup recipient key, installed as the deployment runbook's first
    # installation and the backup runbook ("The key") have the operator do
    # it: a key pair from the image's backup-keygen, and only the public key,
    # the bare base64 line, as the backup_data credential. The private key
    # stays in the runtime root under run/local (a backup archives config,
    # credentials and media, never run/), so the pair travels with snapshots
    # and a local backup can be opened for a restore drill. A deployment
    # installed before this step gets its key the first time `deploy` runs,
    # since the upgrade's required backup needs one.
    local credential="$root/credentials/backup_data/credential" key="$root/run/local/backup-key" answer
    [ ! -f "$credential" ] || return 0
    step "Installing the backup recipient key"
    install -d -o 10001 -g 10001 -m 0700 "$root/run/local" "$(dirname "$credential")"
    rm -f "$key"
    answer=$("${isolated[@]}" --mount "type=bind,source=$root/run/local,target=/keys" \
        "$IMAGE" backup-keygen --destination /keys/backup-key | tail -1)
    jq -er .public_key <<<"$answer" | private_file "$credential"
    echo "    recipient fingerprint $(jq -r .recipient_fingerprint <<<"$answer"); private key $key"
}

# ---------------------------------------------------------------------------

cmd_build() {
    local tag=$1
    [ -f "$build/deploy/stewardship/Dockerfile" ] || refuse "No packed checkout at $build; refusing."
    step "Building $tag from the packed checkout"
    docker build --quiet --file "$build/deploy/stewardship/Dockerfile" --tag "$tag" "$build" >/dev/null
}

cmd_up() {
    local tag=$1 families=$2 admin=$3 debug=$4 uuid seed anchor
    [ -e "$marker" ] && refuse "$root is already a local deployment: 'start' starts it, 'reset' restores a snapshot, 'reset --reinstall' installs again."
    [ -e "$installing" ] && refuse "A previous 'up' did not finish (marker $installing); run 'reset --reinstall' to remove its root and install again."
    if [ -d "$root" ] && [ -n "$(ls -A "$root")" ]; then
        refuse "$root is not empty and carries no marker, so this tool will not touch it." \
            "Remove it by hand if it is yours (sudo rm -rf $root), then run 'up' again."
    fi
    [ -e "$yaml" ] && refuse "$yaml exists but $root carries no marker; another deployment's configuration? Remove it by hand, then run 'up' again."
    if ! [[ $families =~ ^[0-9]+$ ]] || [ "$families" -lt 1 ]; then refuse "--families must be a positive integer."; fi
    if docker image inspect "$tag" >/dev/null 2>&1; then
        step "Using the image $tag built a moment ago"
    else
        cmd_build "$tag"
    fi

    step "Writing the deployment YAML and the deployment record"
    uuid=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen | tr '[:upper:]' '[:lower:]')
    seed=$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')
    # The fake clock (OPS-10.07) runs `up` 17 days behind real time; the
    # synthetic parish is anchored to that date now, so the data does not
    # move when the clock arrives.
    anchor=$(date -u -d '17 days ago' +%F)
    install -d -m 0755 "$(dirname "$yaml")"
    # The in-progress marker: until the root carries its own marker, this
    # is what lets `wipe` (and only `wipe`) remove a half-installed root.
    printf 'installing %s\n' "$root" >"$installing"
    cat >"$yaml" <<YAML
# Written by tools/stewardship-local.sh: the LOCAL deployment (#476).
# It holds no secrets. The offline commands read it as uid 10001.
deployment:
  schema_version: 1
  profile: local
  public_origin: https://localhost:8443
  trusted_proxy_hops: 1
  paths:
    root: $root
YAML
    chmod 0644 "$yaml"
    cat >"$envfile" <<ENV
# Written by tools/stewardship-local.sh; read by its later commands.
UUID=$uuid
ADMIN_EMAIL=$admin
IMAGE=$tag
FAMILIES=$families
SEED=$seed
ANCHOR_DATE=$anchor
DEBUG=$debug
ENV
    chmod 0600 "$envfile"
    load_env

    step "Provisioning the runtime root"
    install -d -o 10001 -g 10001 -m 0700 "$root"
    # Census publication (#1884) is on from the start: a new deployment reads
    # the fake ParishSoft, the only one pk-local may write to. An operational
    # switch, so it reaches the service documents but not the provisioning
    # record; every deploy renders it again (stewardship-upgrade-host.sh).
    "${isolated[@]}" \
        --mount "type=bind,source=$root,target=$root" \
        --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
        -e PARISHKIT_STEWARDSHIP_PARISHSOFT_PUBLICATION=1 \
        "$IMAGE" provision-runtime --config /run/operator.yaml --image "$IMAGE" | tail -1
    # The marker can only be written now: provisioning requires an empty
    # root. From here on every destructive command recognizes this root.
    printf 'parishkit-local deployment %s\n' "$uuid" >"$marker"
    chmod 0600 "$marker"
    rm -f "$installing"

    step "Collecting the static files"
    "${isolated[@]}" \
        --mount "type=bind,source=$root/cache/static,target=$root/cache/static" \
        "$IMAGE" collect-static --destination "$root/cache/static" | tail -1

    step "Installing the sentinel OAuth client and the fake-ParishSoft configuration"
    # The sentinel has the client document's shape and no real client, so a
    # new deployment has no Google sign-in; only LOCAL admits it. A developer
    # may later replace it with a real localhost-only client (#1399; the
    # guide's "Signing in with Google").
    image_constant parishkit.stewardship.runtime_web LOCAL_OAUTH_DOCUMENT \
        '{"client_id": "local-environment-sentinel.invalid", "client_secret": "local-environment-sentinel-not-a-secret"}' |
        private_file "$root/credentials/google_oauth/credential"
    install -d -o 10001 -g 10001 -m 0700 "$root/run/local" "$clock_dir"
    # The fake reads this once at start (specification, "Fake configuration");
    # release_at stays null until the seeder's final phase.
    printf '{"seed": %s, "families": %s, "anchor_date": "%s", "release_at": null}\n' \
        "$seed" "$families" "$anchor" | private_file "$root/run/local/fake-parishsoft.json"
    ensure_backup_key
    # Clock mode. An unseeded deployment runs in fake-clock mode, 17 days
    # behind real time, so a later seed can start forward of every row; an
    # image without the override (an older build) runs in normal mode.
    if [ -f "$services/compose.faketime.json" ]; then
        printf 'fake\n' >"$clock_dir/mode"
        printf -- '-1468800\n' >"$clock_dir/offset"
    else
        printf 'normal\n' >"$clock_dir/mode"
        printf -- '0\n' >"$clock_dir/offset"
    fi
    chown 10001:10001 "$clock_dir/mode" "$clock_dir/offset"
    chmod 0644 "$clock_dir/mode" "$clock_dir/offset"

    build_faketime_images

    # Runbook, first installation, step 3: the numbered runtime-guide steps
    # with compose-initial.json (plus the fake-clock override in fake mode)
    # and the fixed project name.
    step "1. Starting postgres and valkey"
    dependencies_up
    select_initial
    step "2. database-roles"
    "${dc0[@]}" run --rm -T database-provision database-roles \
        --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1
    step "3. bootstrap prepare"
    "${dc0[@]}" run --rm -T bootstrap bootstrap --config "$services/bootstrap.yaml" \
        --phase prepare --deployment-id "$uuid" --admin-email "$admin" 2>&1 | tail -1
    step "4. migration"
    "${dc0[@]}" run --rm -T migration 2>&1 | tail -1
    step "5. database-grants"
    "${dc0[@]}" run --rm -T database-provision database-grants \
        --config "$services/database-provision.yaml" --confirm-deployment "$uuid" 2>&1 | tail -1
    step "6. bootstrap import"
    "${dc0[@]}" run --rm -T bootstrap bootstrap --config "$services/bootstrap.yaml" \
        --phase import --deployment-id "$uuid" --admin-email "$admin" 2>&1 | tail -1
    step "7. Starting the online services"
    start_services
    summary
}

summary() {
    # What the developer needs next: where the site is, how to sign in, and
    # the values the setup wizard asks for. Constants come from the image;
    # the upper-case names are the deployment record load_env sources.
    load_env
    local mode
    mode=$(clock_mode)
    # shellcheck disable=SC2153
    cat <<SUMMARY

Local deployment is up (installed in $(( $(date -u +%s) - began ))s).

  Site:       https://localhost:8443   (Caddy's own CA: run 'ca' to trust it)
  Mail:       http://localhost:8025    (Mailpit)
  Admin:      $ADMIN_EMAIL
  Sign in:    tools/stewardship-local.sh sign-in --email $ADMIN_EMAIL
  Image:      $IMAGE
  Deployment: $UUID, project $project, root $root
  Clock mode: $mode

Setup wizard values (synthetic parish of $FAMILIES Families, seed $SEED):
  ParishSoft API key:      $(image_constant parishkit.stewardship.local LOCAL_PARISHSOFT_KEY)
  ParishSoft organization: $(image_constant parishkit.stewardship.local LOCAL_ORGANIZATION_ID) ($(image_constant parishkit.stewardship.local LOCAL_ORGANIZATION_NAME))
  Mail credential (paste as the Workspace JSON): $(image_constant parishkit.stewardship.mail_catcher MAIL_CATCHER_DOCUMENT)
  Mail delegated address:  $ADMIN_EMAIL
SUMMARY
}

cmd_start() {
    require_marker
    load_env
    start_services
}

cmd_down() {
    require_marker
    step "Stopping the services (data stays)"
    stop_services
}

cmd_status() {
    # status [MAC_FREE_KB]: the laptop half passes the Mac's free space so
    # the snapshot summary can show both disks.
    echo "VM disk:"
    df -h / | tail -1
    echo "Docker disk use:"
    docker system df
    if [ -f "$marker" ]; then
        load_env
        echo "Deployment $UUID ($IMAGE), clock mode $(cat "$clock_dir/mode" 2>/dev/null || echo missing)"
        [ -z "${PREVIOUS_IMAGE-}" ] || echo "Previous image ('deploy --rollback' returns to it): $PREVIOUS_IMAGE"
        echo "Services (state, health):"
        service_states | awk '{ print "    " $1, $2, ($3 == "none" ? "-" : $3) }'
    elif [ -e "$installing" ]; then
        echo "A previous 'up' did not finish; 'reset --reinstall' removes its root and installs again."
    else
        echo "No local deployment at $root."
    fi
    cmd_snapshot_list "${1-}"
}

# Named snapshots (specification, "Snapshots and fast reset"). Each one is
# SNAPSHOTS/NAME/{root/,deployment.yaml,deployment.env,taken}, plus, since
# #1395, `meta` (key=value lines, read with record_value and never sourced)
# and `migrations` (the applied django_migrations rows, "app name" per
# line). A snapshot taken before #1395 has neither; every command still
# accepts it.
# The disk guard (#1395): the Mac must keep 20 GiB free after twice the
# copy's size (the VM's sparse disk file grows by the copy and does not
# shrink until fstrim), and the VM 5 GiB after the copy.
mac_reserve_kb=$((20 * 1024 * 1024))
vm_reserve_kb=$((5 * 1024 * 1024))

check_name() {
    # Snapshot names are path components and command arguments: 1-32
    # lowercase letters, digits or '-', never starting with '-'.
    [[ $1 =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
        refuse "A snapshot name is 1-32 lowercase letters, digits or '-', starting with a letter or digit: '$1'."
}

record_value() {
    # record_value FILE KEY: the value of KEY=value in FILE, or nothing. A
    # snapshot's records are read this way, never sourced.
    [ -r "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" | tail -1
}

gib() {
    # A size in KiB as GiB with one decimal.
    awk -v kb="$1" 'BEGIN { printf "%.1f GiB", kb / 1048576 }'
}

free_kb() {
    # Free KiB on the file system that holds the path.
    df -Pk "$1" | awk 'NR == 2 { print $4 }'
}

image_commit() {
    # The checkout commit a LOCAL image tag names (<commit>[-dirty]-<epoch>).
    local tag=${1##*:}
    printf '%s' "${tag%-*}"
}

operator_query() {
    # operator_query SQL: one read-only query as the operator login against
    # the running postgres; unaligned and tab-separated, empty on failure
    # (a pre-wizard database, or postgres not running).
    docker compose -f "$services/compose-initial.json" -p "$project" exec -T \
        -e PGOPTIONS='-c default_transaction_read_only=on' postgres \
        psql -X -U pk_stewardship_operator -d stewardship -At -F $'\t' -c "$1" </dev/null 2>/dev/null || true
}

applied_migrations() {
    # The applied migrations, one sorted "app name" line each.
    operator_query "SELECT app || ' ' || name FROM django_migrations" | LC_ALL=C sort -u
}

data_summary() {
    # One tab-separated line: the global mode, the current campaign's state,
    # its Family count and the Testing recipient (empty before the wizard).
    # One line of SQL (the continuations join it), so a log shows it whole.
    operator_query "SELECT s.mode, coalesce(c.state, 'none'), \
(SELECT count(*) FROM stewardship_family_campaign f WHERE f.campaign_id = s.current_campaign_id), \
s.testing_recipient FROM stewardship_system_configuration s \
LEFT JOIN stewardship_campaign c ON c.id = s.current_campaign_id" | head -1
}

snapshot_rows() {
    # One "TAKEN NAME" line per snapshot, oldest first.
    local dir
    [ -d "$snapshots" ] || return 0
    for dir in "$snapshots"/*/; do
        # Valid snapshots only (cmd_has_snapshot); hidden partial copies
        # never match the glob.
        [ -d "$dir/root" ] || continue
        [ -f "$dir/taken" ] || continue
        printf '%s %s\n' "$(cat "$dir/taken")" "$(basename "$dir")"
    done | LC_ALL=C sort
}

snapshot_kb() {
    # A snapshot's size in KiB: its meta record's, or measured.
    local kb
    kb=$(record_value "$snapshots/$1/meta" size_kb)
    [ -n "$kb" ] || kb=$(du -sk "$snapshots/$1" | cut -f1)
    printf '%s' "$kb"
}

compare_migrations() {
    # compare_migrations SNAPSHOT_FILE RUNNING_FILE: same, older (the
    # running code has migrations the snapshot lacks) or newer (the
    # snapshot has one the running code does not know).
    if [ -n "$(LC_ALL=C comm -23 "$1" "$2")" ]; then
        echo newer
    elif [ -n "$(LC_ALL=C comm -13 "$1" "$2")" ]; then
        echo older
    else
        echo same
    fi
}

running_migrations=""
# The report email conversion and the digest retirement that needs it
# (#1834); stewardship-upgrade-host.sh's step-1 guard names the same
# conversion migration.
conversion_migration=0041_report_conversion
retirement_migration=0046_retire_digests
plan_switch() {
    # plan_switch NAME: what `snapshot-restore NAME` would do, as one word.
    # data: the snapshot was taken on the running image, so its schema,
    # grants and logins are the running code's and only the data changes.
    # upgrade: another image with recorded migrations the running code all
    # knows, migrated forward on the snapshot's own image by the upgrade's
    # steps (which also check the grants and logins). Refusals: foreign
    # (another install's UUID), newer (a migration the running code does
    # not know), unrecorded (another image and no migrations record, so
    # newer code cannot be ruled out), unconverted (the running code has
    # retired the digests, #1834, but the snapshot predates the report
    # email conversion that the retirement needs first, so the host half
    # would refuse only after the data was replaced), missing-image (the
    # upgrade needs the snapshot's image, which is gone) and unknown (the
    # running database's migrations could not be read). Needs load_env and
    # $running_migrations.
    local dir="$snapshots/$1" snap_image
    [ "$(record_value "$dir/deployment.env" UUID)" = "$UUID" ] || { echo foreign; return 0; }
    snap_image=$(record_value "$dir/deployment.env" IMAGE)
    if [ "$snap_image" = "$IMAGE" ]; then
        echo data
        return 0
    fi
    [ -s "$dir/migrations" ] || { echo unrecorded; return 0; }
    [ -s "${running_migrations:-/nonexistent}" ] || { echo unknown; return 0; }
    if [ "$(compare_migrations "$dir/migrations" "$running_migrations")" = newer ]; then
        echo newer
    elif grep -qxF "stewardship_reports $retirement_migration" "$running_migrations" &&
        ! grep -qxF "stewardship_reports $conversion_migration" "$dir/migrations"; then
        echo unconverted
    elif docker image inspect "$snap_image" >/dev/null 2>&1; then
        echo upgrade
    else
        echo missing-image
    fi
}

cmd_snapshot_list() {
    # snapshot-list [MAC_FREE_KB]: every snapshot with its record and what
    # a switch to it would do, then the free space on both disks.
    local taken name dir mode campaign clock commit plan size
    echo "Snapshots in $snapshots, oldest first ('switch' is what 'snapshot restore NAME' would do):"
    if [ -f "$marker" ]; then
        load_env
        running_migrations=$(mktemp)
        applied_migrations >"$running_migrations"
    fi
    printf '    %-24s %-21s %-18s %-7s %-12s %-22s %s\n' NAME TAKEN MODE/CAMPAIGN CLOCK COMMIT SWITCH SIZE
    while read -r taken name; do
        dir="$snapshots/$name"
        mode=$(record_value "$dir/meta" mode)
        campaign=$(record_value "$dir/meta" campaign)
        clock=$(cat "$dir/root/run/local/clock/mode" 2>/dev/null || echo "?")
        commit=$(image_commit "$(record_value "$dir/deployment.env" IMAGE)")
        plan="-"
        if [ -f "$marker" ]; then
            case "$(plan_switch "$name")" in
                data) plan="data only" ;;
                upgrade) plan="migrates forward" ;;
                newer) plan="refused: newer code" ;;
                foreign) plan="refused: other install" ;;
                unrecorded) plan="refused: no record" ;;
                missing-image) plan="refused: image gone" ;;
                unconverted) plan="refused: unconverted" ;;
                unknown) plan="unknown: no migrations" ;;
            esac
        fi
        size=$(gib "$(snapshot_kb "$name")")
        printf '    %-24s %-21s %-18s %-7s %-12s %-22s %s\n' "$name" "$taken" \
            "${mode:-?}/${campaign:-?}" "$clock" "${commit:0:7}${commit:40}" "$plan" "$size"
    done < <(snapshot_rows)
    [ -z "$running_migrations" ] || rm -f "$running_migrations"
    running_migrations=""
    if [ -d "$snapshots" ]; then
        echo "VM free: $(gib "$(free_kb "$snapshots")") (a save keeps $(gib "$vm_reserve_kb"))"
    fi
    if [[ ${1-} =~ ^[0-9]+$ ]]; then
        echo "Mac free: $(gib "$1") (a save keeps $(gib "$mac_reserve_kb") plus twice the copy)"
    fi
}

check_space() {
    # check_space NAME MAC_FREE_KB LIMIT: refuse a save, before anything
    # stops, that would pass the snapshot cap or either disk reserve. The
    # copy counts at the root's full size even for a replacement: it is
    # written beside the old copy before the old one is removed, and the
    # VM's sparse disk file keeps the space until the trim that follows.
    # Nothing is pruned automatically: snapshots are deliberate return
    # points, so the refusal lists them.
    local name=$1 mac_free=$2 limit=$3 need vm_free count taken other
    local -a problems=()
    need=$(du -sk "$root" | cut -f1)
    vm_free=$(free_kb "$snapshots")
    count=$(snapshot_rows | awk -v n="$name" '$2 != n' | wc -l | tr -d ' ')
    [ "$count" -lt "$limit" ] ||
        problems+=("there are already $count other snapshots (the cap is $limit)")
    [ $(( vm_free - need )) -ge "$vm_reserve_kb" ] ||
        problems+=("the VM would have $(gib $(( vm_free - need ))) free, below its $(gib "$vm_reserve_kb") reserve")
    if [ -n "$mac_free" ] && [ $(( mac_free - 2 * need )) -lt "$mac_reserve_kb" ]; then
        problems+=("the Mac would have $(gib $(( mac_free - 2 * need ))) free, below its $(gib "$mac_reserve_kb") reserve")
    fi
    [ "${#problems[@]}" -gt 0 ] || return 0
    {
        echo "Not saving the $name snapshot (about $(gib "$need")):"
        printf '    %s\n' "${problems[@]}"
        echo "Snapshots, oldest first:"
        while read -r taken other; do
            echo "    $other  taken $taken  $(gib "$(snapshot_kb "$other")")"
        done < <(snapshot_rows)
    } >&2
    refuse "Delete one you no longer need first: tools/stewardship-local.sh snapshot delete NAME"
}

write_meta() {
    # write_meta DIR TAKEN SUMMARY: the snapshot's meta record. SUMMARY is
    # data_summary's line, read before the services stopped.
    local dir=$1 taken=$2 mode campaign families recipient
    IFS=$'\t' read -r mode campaign families recipient <<<"$3" || true
    {
        echo "taken=$taken"
        echo "image=$IMAGE"
        echo "commit=$(image_commit "$IMAGE")"
        echo "uuid=$UUID"
        echo "mode=${mode:-unknown}"
        echo "campaign=${campaign:-unknown}"
        echo "families=${families:-unknown}"
        echo "testing_recipient=${recipient-}"
        echo "clock=$(cat "$clock_dir/mode" 2>/dev/null || echo unknown)"
        echo "migrations=$(wc -l <"$dir/migrations" | tr -d ' ')"
        echo "leader_user=$(cat "$root/run/local/leader-user" 2>/dev/null || true)"
        echo "size_kb=$(du -sk "$dir/root" | cut -f1)"
    } >"$dir/meta"
}

# The EXIT trap of a save, a switch and `seed --testing` (#1395). `phase`
# says how far the command got: "stopped" (the services are down and the
# root is untouched, so they are started again), "restoring" (the root is
# part-way to a snapshot's, so nothing is started: the advice names the
# whole restore that recovers), or empty (nothing to undo).
phase="" phase_name="" phase_advice=""
failure_note() {
    # A trap's message: to the terminal (and through tee to the log) while
    # the tee is there, and to the log file directly when it is gone.
    printf '%s\n' "$*" >&2 2>/dev/null || trap_note "$*"
}

snapshot_failed() {
    local rc=$1
    [ "$rc" -ne 0 ] || return 0
    [ -n "$phase" ] || return 0
    # No errexit and SIGPIPE ignored, as in seed_failed: nothing may end
    # this trap before the restart.
    set +e
    trap '' PIPE
    if [ "$phase" = stopped ]; then
        if (set -e; start_services) >>"${log:-/dev/null}" 2>&1; then
            failure_note "FAILED: $phase_name (exit $rc); the root is unchanged and the services are running again."
        else
            failure_note "FAILED: $phase_name (exit $rc); the root is unchanged but the services did not start: run 'start'."
        fi
    else
        failure_note "FAILED: $phase_name (exit $rc) after the services stopped; the deployment holds a mix of data and is not started."
    fi
    [ -z "$phase_advice" ] || failure_note "$phase_advice"
}

cmd_snapshot() {
    # snapshot NAME [REPLACE MAC_FREE_KB LIMIT]: REPLACE 0 refuses an
    # existing NAME. The defaults (replace, no Mac check, cap 8) are what a
    # laptop half from before #1395 gets. The copy is made in a hidden
    # .NAME.partial directory (never listed) and renamed only once it is
    # complete, so a failed save leaves no half copy and, for a
    # replacement, keeps the earlier copy whole.
    local name=$1 replace=${2:-1} mac_free=${3-} limit=${4:-8} completed migrations summary taken
    local dir="$snapshots/$1" partial="$snapshots/.$1.partial" old="$snapshots/.$1.old" link=()
    check_name "$name"
    [[ $limit =~ ^[0-9]+$ ]] || refuse "The snapshot cap must be a whole number: '$limit'."
    [ -z "$mac_free" ] || [[ $mac_free =~ ^[0-9]+$ ]] ||
        refuse "The Mac's free space must be a number of KiB: '$mac_free'."
    require_marker
    load_env
    if [ -d "$dir" ] && [ "$replace" != 1 ]; then
        refuse "A snapshot named $name exists; 'snapshot save $name --replace' overwrites it."
    fi
    install -d -m 0700 "$snapshots"
    check_space "$name" "$mac_free" "$limit"
    select_compose
    completed=$(setup_completed)
    [ "$completed" = t ] || echo "    note: the setup wizard has not completed; this is a pre-wizard snapshot"
    # What the records describe is read while postgres still serves. The
    # migrations record is what a later switch trusts to refuse newer
    # code, so a save without it is refused rather than written empty.
    migrations=$(applied_migrations)
    [ -n "$migrations" ] || refuse "Could not read the applied migrations from postgres; refusing the save (nothing stopped)."
    summary=$(data_summary)
    phase_name="the $name snapshot" phase_advice="Run 'snapshot save $name' again once the cause is fixed."
    trap 'snapshot_failed $?' EXIT
    step "Stopping the services for the $name snapshot"
    phase=stopped
    stop_services
    step "Copying $root to $dir"
    rm -rf "$partial" "$old"
    install -d -m 0700 "$partial"
    # Unchanged files of an earlier copy are hard-linked, not copied again.
    [ ! -d "$dir/root" ] || link=(--link-dest="$dir/root")
    # Numeric ownership, modes and hard links preserved; the deployment YAML
    # and record travel with the root so a restore is self-contained.
    rsync -aHAX --delete --numeric-ids "${link[@]+"${link[@]}"}" "$root/" "$partial/root/"
    cp -p "$yaml" "$partial/deployment.yaml"
    cp -p "$envfile" "$partial/deployment.env"
    taken=$(date -u +%FT%TZ)
    printf '%s\n' "$migrations" >"$partial/migrations"
    write_meta "$partial" "$taken" "$summary"
    # `taken` last: a snapshot is valid only with it (cmd_has_snapshot).
    printf '%s\n' "$taken" >"$partial/taken"
    [ ! -d "$dir" ] || mv "$dir" "$old"
    mv "$partial" "$dir"
    echo "    $(du -sh "$dir" | cut -f1) copied"
    phase=""
    trap - EXIT
    start_services
    if [ -d "$old" ]; then
        rm -rf "$old"
        timed 600 "trimming the VM disk" fstrim -v / ||
            echo "    the trim did not finish; the VM disk file keeps the replaced copy's space until one does"
    fi
}

cmd_snapshot_delete() {
    # snapshot-delete NAME: the laptop half has had the name typed. The VM
    # disk is a sparse file on the Mac that does not shrink when files are
    # deleted until the file system is trimmed.
    local name=$1
    check_name "$name"
    [ -d "$snapshots/$name" ] || refuse "No $name snapshot at $snapshots/$name."
    step "Deleting the $name snapshot ($(gib "$(snapshot_kb "$name")"))"
    rm -rf "${snapshots:?}/$name"
    step "Trimming the VM disk so the Mac gets the space back"
    timed 600 "trimming the VM disk" fstrim -v / ||
        echo "    the trim did not finish; the VM disk file keeps its size until one does"
}

cmd_has_snapshot() {
    # A snapshot is valid only with its root and its `taken` stamp, which
    # a save writes last.
    [ -d "$snapshots/$1/root" ] && [ -f "$snapshots/$1/taken" ]
}

# The data-only switch (#1395), taken only when the snapshot was saved on
# the running image. What it keeps of the running deployment, as rsync
# exclusions anchored at the root: the rendered service configuration and
# deployment documents, the static trees, Caddy's data (so the CA the
# browser trusts stays valid) and the provisioning records. Everything else
# (the stores under run/persistent, the clock marker and fake configuration
# under run/local, the installed configuration documents under
# config/stewardship and the credentials the database pairs with) comes from
# the snapshot. With one image on both sides those match each other.
switch_keeps=(--exclude=/.parishkit-local --exclude=/.stewardship-provisioned.json
    --exclude=/.stewardship-provisioning.json --exclude=/config/services/
    --exclude=/config/deployment/ --exclude=/cache/ --exclude=/run/persistent/caddy/)

sign_in_line() {
    # A local sign-in link for EMAIL, as `sign-in --email` prints it. Needs
    # `dc` (start_services selected it).
    "${dc[@]}" exec -T web pk-stewardship local-sign-in --config "$services/web.yaml" --email "$1"
}

print_sign_in() {
    # After a switch the sessions live in the other data: fresh links for
    # the Administrator and, when the data records one, the seeded Ministry
    # leader user, and where that data's mail is.
    local leader
    echo "Sign in again; sessions belong to the data that was switched out."
    echo "  Administrator $ADMIN_EMAIL:"
    sign_in_line "$ADMIN_EMAIL"
    leader=$(cat "$root/run/local/leader-user" 2>/dev/null || true)
    if [ -n "$leader" ]; then
        echo "  Ministry leader user $leader:"
        sign_in_line "$leader"
    fi
    echo "  Mail: http://localhost:8025 (Mailpit holds this snapshot's mail)"
}

switch_to() {
    # switch_to NAME HOST_SCRIPT: the switch without the sign-in lines.
    # Every refusal comes before anything stops. The snapshot itself is
    # never changed, so switching back migrates it again. A failure after
    # the stop is reported by the EXIT trap with the whole restore that
    # recovers (the deployment record stays truthful throughout).
    local name=$1 host_script=$2 dir="$snapshots/$1" plan snap_image clock
    local run_image run_previous bulk latency parishsoft
    check_name "$name"
    require_marker
    load_env
    cmd_has_snapshot "$name" || refuse "No $name snapshot at $dir; 'snapshot list' lists them."
    select_compose
    running_migrations=$(mktemp)
    applied_migrations >"$running_migrations"
    plan=$(plan_switch "$name")
    rm -f "$running_migrations"
    running_migrations=""
    snap_image=$(record_value "$dir/deployment.env" IMAGE)
    clock=$(cat "$dir/root/run/local/clock/mode" 2>/dev/null || true)
    case "$plan" in
        foreign) refuse "The $name snapshot belongs to another install (deployment $(record_value "$dir/deployment.env" UUID), not $UUID). A switch keeps this install's configuration, so it refuses; 'reset $name' restores it whole." ;;
        newer) refuse "The $name snapshot has migrations the running code does not know: it was taken on commit $(image_commit "$snap_image"). Deploy that commit (or a later one) first, or 'reset $name' to restore it whole." ;;
        unrecorded) refuse "The $name snapshot was taken on another image ($snap_image) before snapshots recorded their migrations, so newer code cannot be ruled out; refusing. Record it with 'reset $name' then 'snapshot save $name --replace', or use 'reset $name' alone." ;;
        unknown) refuse "Could not read the running database's migrations, so the $name snapshot cannot be checked against the running code; refusing (nothing stopped)." ;;
        unconverted) refuse "The $name snapshot was taken before the report email conversion, which the running code needs before it retires the digests; refusing (nothing stopped). Use 'reset $name', deploy a commit that converts the digests but does not retire them (it has frozen file 0161 but not 0171), then deploy this code again; 'snapshot save $name --replace' after the conversion keeps the snapshot usable." ;;
        missing-image) refuse "The $name snapshot's data is migrated forward on its own image $snap_image, which is no longer in the VM; refusing." ;;
    esac
    case "$clock" in fake|normal) ;; *) refuse "The $name snapshot has no clock-mode marker; refusing." ;; esac
    if [ "$plan" = data ] && [ "$clock" = fake ] && [ ! -f "$services/compose.faketime.json" ]; then
        refuse "The $name snapshot is in fake-clock mode but the running image renders no fake-clock override; refusing."
    fi
    if [ "$plan" = upgrade ]; then
        [ -f "$host_script" ] || refuse "No uploaded upgrade host script at $host_script; refusing."
    fi
    run_image=$IMAGE run_previous=${PREVIOUS_IMAGE-}
    phase_name="the switch to the $name snapshot"
    phase_advice="Recover with 'reset $name' (the snapshot whole, image and data) or 'reset' to another snapshot."
    trap 'snapshot_failed $?' EXIT
    if [ "$plan" = data ]; then
        step "Switching to the $name snapshot's data (taken on the running image $IMAGE)"
        phase=stopped
        stop_services
        phase=restoring
        # As cmd_reset restores, less what the running deployment keeps.
        rsync -aHAX --checksum --inplace --delete --numeric-ids "${switch_keeps[@]}" "$dir/root/" "$root/"
        [ "$clock" = normal ] || build_faketime_images
        start_services --force-recreate
    else
        # Another image: its data is started on the image it was taken with
        # and moved to the running image by the Production upgrade's steps,
        # as `deploy --schema-change` would, keeping the running rendering
        # choices. The upgrade check then covers migrations, grants and
        # logins alike, and the running image's required backup never sees
        # pending migrations. The users upgrade's inputs (#952), when the
        # laptop half was given them, reach the host half from this
        # environment as on a deploy.
        bulk=$(jq -r '.deployment.bulk_family_send // false' "$services/worker.yaml")
        if [ "$bulk" = true ]; then bulk=on; else bulk=off; fi
        latency=$(jq -r '.deployment.local_smtp_latency_ms // 0' "$services/worker.yaml")
        # The ParishSoft choice (#1399): unset is the fake, and the only
        # other value the host half renders is the real API.
        parishsoft=$(jq -r '.deployment.parishsoft_base_url // empty' "$services/worker.yaml")
        case "$parishsoft" in
            "") parishsoft=fake ;;
            https://ps-fs-external-api-prod.azurewebsites.net/api/v2) parishsoft=real ;;
            *) refuse "The running ParishSoft base URL is not the real API; refusing." ;;
        esac
        # The mail redirect (#1399) is carried over too: unset is "off".
        redirect=$(jq -r '.deployment.mail_redirect // empty' "$services/worker.yaml")
        redirect=${redirect:-off}
        step "Switching to the $name snapshot (taken on $snap_image): restoring it whole, then upgrading it to $run_image"
        phase=stopped
        stop_services
        phase=restoring
        rsync -aHAX --checksum --inplace --delete --numeric-ids --exclude=/run/persistent/caddy/ "$dir/root/" "$root/"
        # The running record (debug setting and all), naming the snapshot's
        # image until the host half moves it on.
        record_images "$snap_image" "$run_previous"
        load_env
        [ "$clock" = normal ] || build_faketime_images
        start_services --force-recreate
        export PARISHKIT_LOCAL_BULK_FAMILY_SEND=$bulk PARISHKIT_LOCAL_SMTP_LATENCY_MS=$latency
        export PARISHKIT_LOCAL_PARISHSOFT=$parishsoft PARISHKIT_LOCAL_MAIL_REDIRECT=$redirect
        # The host half reports its own failures; the trap adds the recovery.
        phase="" phase_advice=""
        trap - EXIT
        run_host_half "$host_script" "$run_image" 1 upgrade ||
            refuse "The upgrade of the $name snapshot's data to $run_image did not finish (its log is above); the deployment holds that data on $(compose_image). Recover with 'reset $name' (the snapshot whole) or 'reset' to another snapshot."
        record_images "$run_image" "$run_previous"
        load_env
    fi
    phase=""
    trap - EXIT
    echo "Switched to the $name snapshot ($plan) in $(( $(date -u +%s) - began ))s; $IMAGE runs."
}

cmd_restore() {
    # snapshot-restore NAME HOST_SCRIPT: the switch, then the sign-in lines.
    switch_to "$1" "$2"
    print_sign_in
}

cmd_reset() {
    local name=$1
    check_name "$name"
    require_marker
    cmd_has_snapshot "$name" || refuse "No $name snapshot at $snapshots/$name."
    load_env
    step "Stopping the services to restore the $name snapshot"
    stop_services
    step "Restoring $snapshots/$name over $root"
    # --checksum: a file the services rewrote with the same size and time
    # as the snapshot's copy is still put back. --inplace keeps the inode
    # of every file that exists on both sides, so a bind mount of a
    # configuration or credential file sees the restored bytes; the
    # containers are recreated below all the same.
    rsync -aHAX --checksum --inplace --delete --numeric-ids "$snapshots/$name/root/" "$root/"
    cp -p "$snapshots/$name/deployment.yaml" "$yaml"
    cp -p "$snapshots/$name/deployment.env" "$envfile"
    load_env
    # The snapshot carries its own clock-mode marker; start_services reads it.
    start_services --force-recreate
}

compose_image() {
    # The image the deployment's Compose file names for web: what the host
    # half retargets, and so what the deployment runs or will run.
    jq -r '.services.web.image // empty' "$services/compose.json"
}

record_images() {
    # record_images IMAGE PREVIOUS: the deployment record follows a deploy
    # or a rollback, so `status` names the running image and `rollback`
    # knows which one it replaced. Private, as `up` wrote it.
    local tmp
    tmp=$(mktemp)
    grep -vE '^(IMAGE|PREVIOUS_IMAGE)=' "$envfile" >"$tmp" || true
    printf 'IMAGE=%s\nPREVIOUS_IMAGE=%s\n' "$1" "$2" >>"$tmp"
    install -m 0600 "$tmp" "$envfile"
    rm -f "$tmp"
}

run_host_half() {
    # run_host_half HOST_SCRIPT IMAGE SCHEMA_CHANGE MODE: the scripted
    # upgrade's host half in local mode, then the record. The host half may
    # fail after it retargeted (its "maintenance page stays up" phase), so
    # the record follows what the Compose file names once it returns,
    # success or failure, and the failure is then passed on: a later
    # 'deploy --rollback' or re-run finds the record truthful either way.
    local host_script=$1 target=$2 schema_change=$3 mode=$4 rc=0 now
    bash "$host_script" - "$target" "$root" "$project" "$yaml" "$UUID" "$schema_change" "$mode" local "$build" || rc=$?
    now=$(compose_image)
    if [ -n "$now" ] && [ "$now" != "$IMAGE" ]; then
        record_images "$now" "$IMAGE"
        [ "$rc" -eq 0 ] || echo "The Compose file now names $now; recorded it (previous $IMAGE)." >&2
    fi
    return "$rc"
}

return_points() {
    # Name the snapshots `reset` can restore after a deploy. A deploy
    # changes the root (documents, static tree) and may migrate the
    # database, so without any snapshot there is no return point: refuse.
    local taken name option found=0
    while read -r taken name; do
        case "$name" in
            post-setup) option="" ;;
            seeded) option=" --seeded" ;;
            *) option=" $name" ;;
        esac
        echo "    return point: 'reset$option' restores the $name snapshot (taken $taken)"
        found=1
    done < <(snapshot_rows)
    [ "$found" = 1 ] || refuse "No snapshot to return to after a deploy; take one first: 'snapshot' (or 'snapshot --seeded' on a seeded deployment)."
}

cmd_deploy() {
    # deploy TAG SCHEMA_CHANGE HOST_SCRIPT: upgrade the running deployment to
    # the image the laptop half just built, through the scripted upgrade's
    # host half in local mode, which is the Production upgrade's steps with
    # the local differences the host half's header lists (specification,
    # "Operator script"). load_env exported the deployment's debug-logging
    # setting, which the host half keeps. BULK and LATENCY (BG-12's
    # rehearsal), PARISHSOFT and MAIL_REDIRECT (#1399) reach the host half as
    # the local-mode variables it reads; "keep" carries the current value over.
    local tag=$1 schema_change=$2 host_script=$3 previous
    export PARISHKIT_LOCAL_BULK_FAMILY_SEND=${4:-keep}
    export PARISHKIT_LOCAL_SMTP_LATENCY_MS=${5:-keep}
    export PARISHKIT_LOCAL_PARISHSOFT=${6:-keep}
    export PARISHKIT_LOCAL_MAIL_REDIRECT=${7:-keep}
    require_marker
    load_env
    [ -f "$host_script" ] || refuse "No uploaded upgrade host script at $host_script; refusing."
    docker image inspect "$tag" >/dev/null 2>&1 || refuse "The image $tag is not built; refusing."
    return_points
    ensure_backup_key
    previous=$IMAGE
    step "Deploying $tag over $previous with the scripted upgrade's host half (local mode)"
    run_host_half "$host_script" "$tag" "$schema_change" upgrade
    echo "Deployed $tag in $(( $(date -u +%s) - began ))s; it replaced $previous."
    echo "  Back to the previous image only (as Production's --rollback): deploy --rollback"
    echo "  Back to a snapshot, image and data together: reset --seeded (or reset, or reset NAME)"
    echo "  Another snapshot's data on this code: snapshot restore NAME"
}

cmd_rollback() {
    # rollback HOST_SCRIPT: the scripted upgrade's image-only rollback to the
    # image the last deploy replaced. The host half refuses, before anything
    # stops, unless the schema and grants already match that image; after a
    # deploy that migrated, the return path is a snapshot (`reset`).
    local host_script=$1 current
    require_marker
    load_env
    [ -f "$host_script" ] || refuse "No uploaded upgrade host script at $host_script; refusing."
    [ -n "${PREVIOUS_IMAGE-}" ] ||
        refuse "No previous image is recorded (no deploy since the install or the last reset); restore a snapshot instead: 'reset --seeded' or 'reset'."
    current=$(compose_image)
    # The record and the Compose file must agree on what runs now; if they
    # do not (a hand-edited root), the rollback target cannot be trusted.
    [ "$current" = "$IMAGE" ] ||
        refuse "The Compose file names $current but the deployment record says $IMAGE; refusing. Restore a snapshot instead ('reset --seeded' or 'reset')."
    step "Rolling back from $current to $PREVIOUS_IMAGE with the scripted upgrade's host half (local mode)"
    run_host_half "$host_script" "$PREVIOUS_IMAGE" 0 rollback
    echo "Rolled back to $PREVIOUS_IMAGE in $(( $(date -u +%s) - began ))s; a second 'deploy --rollback' returns to $current."
}

cmd_wipe() {
    # Only the laptop half calls this, after the operator typed the instance
    # name. Containers and networks go; Docker volumes are never removed
    # (the rendered topology has none; the data lives under the root). The
    # in-progress marker of a failed `up` is accepted here and nowhere else.
    [ -f "$marker" ] || [ -e "$installing" ] ||
        refuse "$root does not carry the marker $marker; refusing (see $spec)."
    step "Removing the $project containers"
    timed 300 "removing the containers" docker compose -p "$project" down --remove-orphans 2>&1 | quiet
    local left
    left=$(docker compose -p "$project" ps --all --quiet)
    [ -z "$left" ] || refuse "Containers of $project still exist; not removing $root."
    step "Removing $root"
    rm -rf "$root"
    rm -f "$envfile" "$installing" "$yaml"
    install -d -m 0755 "$root"
}

# The seeder (specification, "Seeded campaign and responses"). Each phase is
# one `pk-stewardship local-seed --step` run as a one-shot container under an
# existing identity; the clock directory is mounted writable at a second
# path for the phases that move the clock (the services keep their read-only
# mount at the standard path). Every step prints one JSON document.
clock_control=/run/parishkit-clock-control
mailpit=http://127.0.0.1:8025

seed_arguments() {
    # The arguments every seeder step takes, from the deployment record.
    printf '%s\n' --seed "$SEED" --families "$FAMILIES" --anchor-date "$ANCHOR_DATE" \
        --now "$seed_now" --response-scale "$seed_scale" --admin-email "$ADMIN_EMAIL"
}

oneoff=()
web_oneoff_command() {
    # Fill `oneoff` with the docker command that runs the application image
    # once under the web identity, with web's own rendered mounts, user,
    # hardening and environment (read from compose.json, plus the fake-clock
    # override's when the clock is fake), on the backend network only, and
    # the clock directory also mounted writable at $clock_control. `docker
    # compose run web` cannot do this: web holds a fixed address on the proxy
    # network, which a second container could not share. A command array, not
    # a function, so `timed` can run it under its limit. It takes the seeder's
    # own fixed backend address (`--ip`), the one PostgreSQL's source rules
    # allow for web's logins besides web itself; a dynamic address is in the
    # range no rule allows. Seed steps hold the seed lock and run one at a
    # time, so the one address is never needed twice.
    local web_image file mode line seeder_ip
    local -a options=()
    # Explicit returns: callers run this as `web_oneoff_command || exit 1`,
    # where bash ignores errexit inside the function.
    oneoff=()
    web_image=$(jq -er '.services.web.image' "$services/compose.json") || return 1
    seeder_ip=$(jq -er '."x-local-seeder".ipv4_address' "$services/compose.json") || {
        echo "compose.json names no seeder address; retarget the deployment first." >&2
        return 1
    }
    # Web's own environment, mounts and tmpfs exactly as rendered; Compose
    # variable references (${NAME:-default}) are resolved from this shell as
    # Compose would resolve them from the operator's.
    while IFS= read -r line; do options+=("$line"); done < <(
        jq -r '.services.web.environment | to_entries[] | "--env", "\(.key)=\(.value)"' \
            "$services/compose.json" | envsubst_defaults)
    while IFS= read -r line; do options+=("$line"); done < <(
        jq -r '.services.web.volumes[] |
            "--mount", "type=bind,source=\(.source),target=\(.target)\(if .read_only then ",readonly" else "" end)"' \
            "$services/compose.json")
    while IFS= read -r line; do options+=("$line"); done < <(
        jq -r '.services.web.tmpfs[] | "--tmpfs", .' "$services/compose.json")
    mode=$(clock_mode)
    if [ "$mode" = fake ]; then
        refuse_real_oauth_client
        file="$services/compose.faketime.json"
        web_image=$(jq -er '.services.web.image' "$file") || return 1
        while IFS= read -r line; do options+=("$line"); done < <(
            jq -r '.services.web.environment | to_entries[] | "--env", "\(.key)=\(.value)"' "$file")
        while IFS= read -r line; do options+=("$line"); done < <(
            jq -r '.services.web.volumes[] |
                "--mount", "type=bind,source=\(.source),target=\(.target),readonly"' "$file")
    fi
    oneoff=(docker run --rm --init --user 10001:10001 --read-only --cap-drop ALL
        --security-opt no-new-privileges:true --network "${project}_backend"
        --ip "$seeder_ip"
        --mount "type=bind,source=$clock_dir,target=$clock_control"
        "${options[@]}" "$web_image")
}

envsubst_defaults() {
    # Resolve Compose's ${NAME:-default} and ${NAME} references on stdin from
    # this shell's environment, as `docker compose` does when it starts web.
    # Built left to right (prefix, value, rest), so a value holding `&` or a
    # `${…}` of its own is copied literally and never scanned again.
    local line rest out match name default value
    while IFS= read -r line; do
        out="" rest=$line
        while [[ $rest =~ \$\{([A-Z_][A-Z0-9_]*)(:-([^}]*))?\} ]]; do
            match=${BASH_REMATCH[0]} name=${BASH_REMATCH[1]} default=${BASH_REMATCH[3]}
            value=${!name:-$default}
            out+="${rest%%"$match"*}${value}"
            rest=${rest#*"$match"}
        done
        printf '%s\n' "${out}${rest}"
    done
}

seed_step() {
    # seed_step LIMIT STEP SERVICE CONFIG [EXTRA...]: run one seeder step and
    # keep its JSON answer in $seed_result. Web steps run the one-off web command;
    # the offline check runs the migration profile service with Compose.
    local limit=$1 name=$2 service=$3 config=$4 started
    shift 4
    local -a arguments=()
    while IFS= read -r line; do arguments+=("$line"); done < <(seed_arguments)
    step "Seeder step $name ($service)"
    started=$(date -u +%s)
    if [ "$service" = web ]; then
        web_oneoff_command || exit 1
        seed_result=$(timed "$limit" "the seeder step $name" \
            "${oneoff[@]}" local-seed --step "$name" --config "$services/$config" \
            --clock-dir "$clock_control" "${arguments[@]}" "$@" | tail -1)
    else
        seed_result=$(timed "$limit" "the seeder step $name" \
            "${dc[@]}" run --rm -T --no-deps "$service" \
            local-seed --step "$name" --config "$services/$config" \
            "${arguments[@]}" "$@" | tail -1)
    fi
    # A long answer (the rehearsal's measure step) is kept by its caller;
    # the log shows its start.
    local shown=$seed_result
    [ "${#shown}" -le 1000 ] || shown="${shown:0:1000}... (${#seed_result} characters)"
    echo "    $name finished in $(( $(date -u +%s) - started ))s: $shown"
    echo "    fake now: $(fake_now)"
}

fake_now() {
    # The current fake instant, from the offset file.
    date -u -d "now $(cat "$clock_dir/offset") seconds" +%FT%TZ 2>/dev/null || cat "$clock_dir/offset"
}

mailpit_clear() {
    curl -fsS -X DELETE --max-time 20 "$mailpit/api/v1/messages" >/dev/null
}

mailpit_tag_all() {
    # Tag every message the catcher holds now: after a seed, that is the
    # seed's mail history (specification, "Mail history").
    local ids
    ids=$(curl -fsS --max-time 20 "$mailpit/api/v1/messages?limit=10000" | jq -c '[.messages[].ID]')
    if [ "$ids" = "[]" ]; then
        echo "    no messages to tag"
        return 0
    fi
    curl -fsS -X PUT --max-time 20 -H 'Content-Type: application/json' \
        -d "{\"IDs\": $ids, \"Tags\": [\"$1\"]}" "$mailpit/api/v1/tags" >/dev/null
    echo "    tagged $(jq length <<<"$ids") messages $1"
}

seed_failed() {
    # The EXIT trap of a seed that did not finish: the specification promises
    # a failed seed leaves the deployment in fake-clock mode with its services
    # running. Phases 1 and 2 fail with everything running; the check runs
    # with the services stopped, so they are started again here. A failure
    # after the switch to normal mode (the finish phase) cannot be undone
    # here: the data is seeded but the late Family may not be promoted;
    # `reseed` restores the post-setup snapshot and seeds afresh.
    local rc=$1 mode started=1
    [ "$rc" -ne 0 ] || return 0
    # Nothing may end this trap before the restart: no errexit, SIGPIPE
    # ignored, the restart before any output, and every message written to
    # the log file directly (the tee to the laptop may be gone).
    set +e
    trap '' PIPE
    mode=$(cat "$clock_dir/mode" 2>/dev/null || echo missing)
    if [ "$mode" = fake ]; then
        # A subshell with errexit: a failure inside start_services ends it
        # and is reported below, instead of being swallowed by the `if`.
        (set -e; start_services) >>"${log:-/dev/null}" 2>&1 || started=0
    fi
    trap_note "SEED FAILED (exit $rc after $(( $(date -u +%s) - began ))s); clock mode $mode."
    if [ "$mode" = fake ]; then
        trap_note "The deployment is partially seeded in fake-clock mode; run 'reseed' to restore the post-setup snapshot and seed again."
        if [ "$started" = 1 ]; then
            trap_note "The services are running."
        else
            trap_note "Could not restart the services; run 'start' (or 'reseed')."
        fi
    else
        trap_note "The deployment is partially seeded past the switch to normal mode (the late-added Family may not be promoted); run 'reseed' to restore the post-setup snapshot and seed again."
    fi
}

trap_note() {
    # An EXIT trap's message, written to the run's log file directly: the
    # tee that copies output to the laptop may already be gone.
    printf '%s\n' "$*" >>"${log:-/dev/null}" 2>/dev/null || true
}

cmd_seed() {
    local lock="$snapshots/.seed.lock" mode completed seeded_now leader_user
    seed_scale=$1
    require_marker
    load_env
    install -d -m 0700 "$snapshots"
    exec 9>"$lock"
    flock -n 9 || refuse "Another seed is running (lock $lock); refusing."
    select_compose
    mode=$(clock_mode)
    [ "$mode" = fake ] || refuse "The deployment is in normal clock mode; it is seeded already (reseed restores the post-setup snapshot first)."
    completed=$(setup_completed)
    [ "$completed" = t ] || refuse "The setup wizard has not completed; run it (or 'wizard') first."
    trap 'seed_failed $?' EXIT
    seed_now=$(date -u +%FT%TZ)
    step "Seeding at $seed_now (response scale $seed_scale, $FAMILIES Families, seed $SEED)"
    echo "    fake now: $(fake_now)"
    step "Clearing the mail catcher"
    mailpit_clear
    seed_step 1800 prepare web web.yaml
    seed_step 3600 drive web web.yaml
    step "Stopping the services for the invariant check and the clock-mode switch"
    stop_services
    # The seeded now: the fake instant once every service has stopped, so no
    # row written while the services drained is later than it (the drive's
    # own end instant is a few seconds earlier). Rounded up to the second.
    seeded_now=$(date -u -d "now $(cat "$clock_dir/offset") seconds 1 seconds" +%FT%TZ)
    echo "    seeded now: $seeded_now"
    # Offline work runs against the stores alone, as migration does on an
    # upgrade: postgres and valkey back up (still faked), nothing else.
    dependencies_up
    seed_step 600 check migration migration.yaml --seeded-now "$seeded_now"
    step "Switching to normal clock mode"
    printf -- '+0\n' >"$clock_dir/offset"
    printf 'normal\n' >"$clock_dir/mode"
    chown 10001:10001 "$clock_dir/mode" "$clock_dir/offset"
    # The late-added Family is served from now on (specification, "Fake configuration").
    # stop_services above stopped the fake too, so no contact PUT it records
    # in this file (#1884) can land between the read and the rewrite.
    jq --arg at "$(date -u +%FT%TZ)" '.release_at = $at' "$root/run/local/fake-parishsoft.json" |
        private_file "$root/run/local/fake-parishsoft.json"
    start_services --force-recreate
    seed_step 900 finish web web.yaml
    # The Ministry leader user the finish step imported (#1150), if any.
    leader_user=$(jq -r '.leader_user // empty' <<<"$seed_result")
    # Kept with the data, so a snapshot's meta record and a switch to it
    # can name the user (#1395).
    if [ -n "$leader_user" ]; then
        printf '%s\n' "$leader_user" | private_file "$root/run/local/leader-user"
    fi
    step "Tagging the seed's mail in the catcher"
    mailpit_tag_all seed-history
    trap - EXIT
    echo "Seeded at $seeded_now (requested $seed_now) in $(( $(date -u +%s) - began ))s."
    if [ -n "$leader_user" ]; then
        echo "Ministry leader user: $leader_user (sign-in --email $leader_user)"
    fi
}

cmd_seed_testing() {
    # seed-testing HOST_SCRIPT (#1395): a Testing-mode review state. A
    # seeded campaign is live, and no supported path takes a live campaign
    # back to Testing, so the state is built forward from the post-setup
    # snapshot's Testing draft: switch to it on the running code, check it
    # is still a never-live Testing draft whose Testing recipient is a
    # synthetic address, clear Mailpit, and move the clock forward to real
    # time as the seed's finish does (a forward jump, so no row postdates
    # the clock). The result is a draft campaign in Testing mode at real
    # time. When the image's seeder offers a `testing` step (the automated
    # Testing sends and submissions), it runs last, under the web identity.
    local host_script=$1 dir="$snapshots/post-setup" mode campaign families recipient steps
    require_marker
    load_env
    cmd_has_snapshot post-setup ||
        refuse "No post-setup snapshot to build the Testing state from; take one on a set-up, unseeded deployment: 'snapshot'."
    # The recorded state, when there is a record, refuses before anything stops.
    mode=$(record_value "$dir/meta" mode) campaign=$(record_value "$dir/meta" campaign)
    if [ -n "$mode" ] && { [ "$mode" != testing ] || [ "$campaign" != draft ]; }; then
        refuse "The post-setup snapshot is in $mode mode with a $campaign campaign, not a Testing draft; refusing."
    fi
    switch_to post-setup "$host_script"
    select_compose
    [ "$(clock_mode)" = fake ] ||
        refuse "The post-setup snapshot is in normal clock mode, so it is not the unseeded post-setup state; refusing (the deployment now holds its data)."
    [ "$(setup_completed)" = t ] || refuse "The post-setup snapshot's setup wizard has not completed; refusing (the deployment now holds its data)."
    IFS=$'\t' read -r mode campaign families recipient <<<"$(data_summary)" || true
    if [ "$mode" != testing ] || [ "$campaign" != draft ]; then
        refuse "The post-setup data is in ${mode:-unknown} mode with a ${campaign:-missing} campaign, not a Testing draft; refusing (the deployment now holds its data)."
    fi
    [[ ${recipient-} == *@example.test ]] ||
        refuse "The Testing recipient '${recipient-}' is not an @example.test address; refusing (the deployment now holds its data)."
    step "Clearing the mail catcher"
    mailpit_clear
    # A failure from here restarts the services in whatever mode the marker
    # says, and names the way back.
    phase_name="seed --testing's move to real time"
    phase_advice="'switch post-setup' returns to the post-setup data; then run 'seed --testing' again."
    trap 'snapshot_failed $?' EXIT
    step "Stopping the services to move the clock forward to real time"
    phase=stopped
    stop_services
    printf -- '+0\n' >"$clock_dir/offset"
    printf 'normal\n' >"$clock_dir/mode"
    chown 10001:10001 "$clock_dir/mode" "$clock_dir/offset"
    start_services --force-recreate
    phase=""
    trap - EXIT
    steps=$(image_constant parishkit.stewardship.local.seeder STEPS '[]')
    if [[ $steps == *'"testing"'* ]]; then
        seed_now=$(date -u +%FT%TZ)
        seed_scale=1
        seed_step 1800 testing web web.yaml ||
            refuse "The seeder's testing step failed; the deployment is the post-setup data at real time. 'switch post-setup' returns to it; then run 'seed --testing' again."
    else
        echo "    this image's seeder has no 'testing' step: add the schedules, Testing sends and"
        echo "    Testing submissions to review in the Admin portal and Mailpit"
    fi
    echo "A Testing-mode draft campaign of $families Families at real time (Testing recipient $recipient), in $(( $(date -u +%s) - began ))s."
    print_sign_in
    echo "Save it: tools/stewardship-local.sh snapshot save testing   (add --replace to overwrite)"
}

# The BG-12 rehearsal (developer guide, "Rehearsing a bulk send"; plan,
# BG-12 "Rehearsal protocol"). On a seeded deployment it adds one Reminder
# through the seeder's `reminder` step, samples the work-order lock once a
# second, optionally holds mail-dispatch until preparation has finished (the
# send-only rate), waits for the send to settle, then collects the services'
# DEBUG timing lines, the seeder's read-only `measure` step and the deadlock
# counter, and prints the offline report; it exits non-zero unless the run
# passed the report's correctness check. It changes nothing about how mail
# is sent; it only schedules one more Reminder, as an Administrator could.
pg_container=""
rehearsal_out=""
rehearsal_sampler=""
# Every query the rehearsal makes is a read: a read-only session with a
# statement limit, and no psqlrc (-X). `timed` bounds the whole call and
# logs a kill with its limit and elapsed time.
rehearsal_pgoptions="-c default_transaction_read_only=on -c statement_timeout=5s"

psql_query() {
    # psql_query SQL [PSQL OPTIONS...]: one read-only query as the operator
    # login; prints the unaligned, tab-separated answer. With options (such
    # as -v due=...), the SQL is fed on stdin so psql interpolates them.
    local sql=$1
    shift
    printf '%s\n' "$sql" | timed 20 "a rehearsal query" \
        docker exec -i -e PGOPTIONS="$rehearsal_pgoptions" "$pg_container" \
        psql -X -U pk_stewardship_operator -d stewardship -At -F $'\t' \
        -v ON_ERROR_STOP=1 "$@" -f -
}

# The work-order lock (campaigns/work_locks.py WORK_ORDER_LOCK, 736220,1):
# granted holders and waiters, the holders' role and session state (an
# "idle in transaction" holder is application work done under the lock),
# and the waiters' roles.
LOCK_SAMPLE_SQL="SELECT round(extract(epoch FROM clock_timestamp())::numeric, 3),
  count(*) FILTER (WHERE l.granted), count(*) FILTER (WHERE NOT l.granted),
  coalesce(string_agg(coalesce(a.usename, '?') || ':'
      || replace(coalesce(a.state, '?'), ' ', '_'), ',') FILTER (WHERE l.granted), '-'),
  coalesce(string_agg(coalesce(a.usename, '?'), ',') FILTER (WHERE NOT l.granted), '-')
FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.classid = 736220 AND l.objid = 1 AND l.objsubid = 2"

sample_locks() {
    # sample_locks DIR: one line a second into DIR/locks.tsv until DIR/.stop
    # exists: the lock sample, the VM's /proc/stat CPU counters and its
    # one-minute load average (rehearsal_report.parse_samples reads it).
    local row
    while [ ! -e "$1/.stop" ]; do
        # A failed or killed sample is skipped; timed's FAILED or TIMEOUT
        # line is kept in DIR/sampler-errors.log.
        if row=$(psql_query "$LOCK_SAMPLE_SQL" 2>>"$1/sampler-errors.log") && [ -n "$row" ]; then
            printf '%s\t%s\t%s\n' "$row" "$(head -1 /proc/stat | cut -d' ' -f3-)" \
                "$(cut -d' ' -f1 /proc/loadavg)"
        fi
        sleep 1
    done >>"$1/locks.tsv"
}

stop_sampler() {
    # Ask the sampler to stop and wait for it (it notices within a second).
    [ -n "$rehearsal_sampler" ] || return 0
    touch "$rehearsal_out/.stop"
    wait "$rehearsal_sampler" 2>/dev/null || true
    rehearsal_sampler=""
}

start_mail_dispatch() {
    # Start mail-dispatch (a send-only run stops it), bounded and logged.
    timed 180 "starting mail-dispatch" "${dc[@]}" up --detach mail-dispatch 2>&1 | quiet
}

rehearse_stopped() {
    # The EXIT trap of a rehearsal. Restarting mail-dispatch comes first, so
    # nothing here can stand between a send-only run and its restart.
    local rc=$1 restarted=""
    # No errexit and SIGPIPE ignored, the restart before any output, and the
    # messages written to the log file directly (see trap_note).
    set +e
    trap '' PIPE
    if ! all_healthy mail-dispatch >/dev/null 2>&1; then
        if start_mail_dispatch >>"${log:-/dev/null}" 2>&1; then
            restarted="Started mail-dispatch again."
        else
            restarted="Could not start mail-dispatch; run 'start'."
        fi
    fi
    stop_sampler
    [ -z "$restarted" ] || trap_note "$restarted"
    [ "$rc" -eq 0 ] || trap_note "REHEARSAL FAILED (exit $rc); its files are in $rehearsal_out."
}

prepared_sql="" settled_sql=""
quiet_polls=0
two_polls() {
    # two_polls SQL DUE: the wait predicate (rehearsal.PREPARED_SQL or
    # SETTLED_SQL, read from the image) answers true on two polls in a row:
    # the scheduler plans Families in pages, so one quiet poll may fall
    # between two pages.
    local answer
    answer=$(psql_query "$1" -v "due=$2") || answer=""
    if [ "$answer" != t ]; then
        quiet_polls=0
        return 1
    fi
    quiet_polls=$((quiet_polls + 1))
    [ "$quiet_polls" -ge 2 ]
}
prepared() { two_polls "$prepared_sql" "$1"; }
settled() { two_polls "$settled_sql" "$1"; }

mailpit_total() {
    # How many messages Mailpit holds, or null when it does not answer; a
    # timeout or failure is logged (what, limit, elapsed), never hidden.
    local answer total started rc=0
    started=$(date -u +%s)
    answer=$(curl -fsS --max-time 20 "$mailpit/api/v1/messages?limit=1") || rc=$?
    if [ "$rc" -eq 28 ]; then
        echo "TIMEOUT: Mailpit's message count after $(( $(date -u +%s) - started ))s (limit 20s); recorded as null" >&2
    elif [ "$rc" -ne 0 ]; then
        echo "FAILED: Mailpit's message count (curl exit $rc after $(( $(date -u +%s) - started ))s, limit 20s); recorded as null" >&2
    fi
    total=$(jq -er '.total | numbers' <<<"${answer:-null}" 2>/dev/null) || total=null
    echo "$total"
}

cmd_rehearse() {
    local due_in=$1 send_only=$2 limit=$3 label=$4
    local started due bulk latency consumers before after mail_before mail_after rc=0
    local stopped_at="" resumed_at="" timed_out=false
    # The laptop half checks these too; this half is the one that uses them
    # in a path and in SQL.
    [[ $due_in =~ ^[0-9]+$ && $send_only =~ ^[01]$ && $limit =~ ^[0-9]+$ ]] ||
        refuse "rehearse needs whole-number DUE_IN, SEND_ONLY (0 or 1) and TIMEOUT."
    [[ $label =~ ^[A-Za-z0-9._-]{1,40}$ ]] || refuse "rehearse LABEL must be 1-40 letters, digits, '.', '_' or '-'."
    require_marker
    load_env
    [ "$(clock_mode)" = normal ] ||
        refuse "Rehearsals run on a seeded deployment (normal clock mode); seed it first, or 'reset --seeded'."
    select_compose
    [ "$(setup_completed)" = t ] || refuse "The setup wizard has not completed; refusing."
    pg_container=$("${dc[@]}" ps -q postgres)
    [ -n "$pg_container" ] || refuse "postgres is not running; 'start' first."
    # The wait predicates come from the deployed image (rehearsal.py), so the
    # polls are the queries the PostgreSQL tests run; an image without them
    # answers image_constant's fallback text instead.
    prepared_sql=$(image_constant parishkit.stewardship.local.rehearsal PREPARED_SQL)
    settled_sql=$(image_constant parishkit.stewardship.local.rehearsal SETTLED_SQL)
    [[ $prepared_sql == *stewardship_schedule_occurrence* && $settled_sql == *stewardship_schedule_occurrence* ]] ||
        refuse "The deployed image has no rehearsal support; deploy a build that has it."
    rehearsal_out=/var/log/stewardship-rehearsal-$label-$(date -u +%Y%m%dT%H%M%SZ)
    install -d -m 0755 "$rehearsal_out"
    started=$(date -u +%FT%TZ)
    # Whole minutes: DUE_IN minutes after the next minute boundary, so the
    # schedule's wall-clock minute is the due instant exactly.
    due=$(date -u -d "@$(( ($(date -u +%s) / 60 + 1 + due_in) * 60 ))" +%FT%TZ)
    bulk=$(jq -r '.deployment.bulk_family_send // false' "$services/worker.yaml")
    latency=$(jq -r '.deployment.local_smtp_latency_ms // 0' "$services/mail-dispatch.yaml")
    consumers=$(jq -r '.deployment.mail_consumers // 2' "$services/mail-dispatch.yaml")
    step "Rehearsal $label: Reminder due $due; bulk $bulk, SMTP latency ${latency} ms, $consumers mail consumers, $FAMILIES Families"
    echo "    files: $rehearsal_out"
    before=$(psql_query "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
    mail_before=$(mailpit_total)
    trap 'rehearse_stopped $?' EXIT
    sample_locks "$rehearsal_out" &
    rehearsal_sampler=$!
    seed_now=$(date -u +%FT%TZ)
    seed_scale=1
    seed_step 600 reminder web web.yaml --due-at "$due"
    if [ "$send_only" = 1 ]; then
        step "Stopping mail-dispatch until preparation has finished (send-only run)"
        timed 150 "stopping mail-dispatch" "${dc[@]}" stop --timeout 90 mail-dispatch 2>&1 | quiet
        stopped_at=$(date -u +%FT%TZ)
        quiet_polls=0
        wait_until $(( (due_in + 2 + limit) * 60 )) "the Reminder due $due to be prepared" prepared "$due"
        step "Starting mail-dispatch again"
        start_mail_dispatch
        resumed_at=$(date -u +%FT%TZ)
        wait_until 300 "mail-dispatch to turn healthy" all_healthy mail-dispatch
    fi
    step "Waiting for the Reminder due $due to settle (limit $limit minutes after it)"
    quiet_polls=0
    wait_until $(( (due_in + 2 + limit) * 60 )) "the Reminder due $due to settle" settled "$due" ||
        timed_out=true
    stop_sampler
    after=$(psql_query "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
    mail_after=$(mailpit_total)
    step "Collecting the timing lines, the measurements and the run record"
    # Compose's own stderr goes to a file in the run directory; timed's
    # TIMEOUT or FAILED line stays on stderr, in the log.
    timed 120 "collecting the services' logs" \
        "${dc[@]}" logs --no-color --no-log-prefix --since "$started" worker mail-dispatch scheduler \
        2>>"$rehearsal_out/logs-errors.log" |
        grep -F 'timing: ' >"$rehearsal_out/timings.jsonl" || true
    echo "    $(wc -l <"$rehearsal_out/timings.jsonl") timing lines, $(wc -l <"$rehearsal_out/locks.tsv") lock samples"
    # Under the offline migration identity, as the seed's invariant check:
    # the web login cannot read the outcome events' evidence (rehearsal.py).
    seed_step 600 measure migration migration.yaml --due-at "$due"
    printf '%s\n' "$seed_result" >"$rehearsal_out/measure.json"
    jq -n --arg label "$label" --arg image "$IMAGE" --argjson families "$FAMILIES" \
        --argjson bulk "$bulk" --argjson latency "$latency" --argjson consumers "$consumers" \
        --argjson send_only "$( [ "$send_only" = 1 ] && echo true || echo false )" \
        --arg due "$due" --arg started "$started" --arg finished "$(date -u +%FT%TZ)" \
        --arg stopped "$stopped_at" --arg resumed "$resumed_at" --argjson timed_out "$timed_out" \
        --argjson before "${before:-null}" --argjson after "${after:-null}" \
        --argjson mail_before "$mail_before" --argjson mail_after "$mail_after" \
        '{label: $label, image: $image, families: $families, bulk: $bulk,
          smtp_latency_ms: $latency, mail_consumers: $consumers, send_only: $send_only,
          due_at: $due, started_at: $started, finished_at: $finished,
          dispatch_stopped_at: (if $stopped == "" then null else $stopped end),
          dispatch_started_at: (if $resumed == "" then null else $resumed end),
          timed_out: $timed_out, deadlocks_before: $before, deadlocks_after: $after,
          mailpit_before: $mail_before, mailpit_after: $mail_after}' >"$rehearsal_out/meta.json"
    # The report runs offline in the image, as the application's uid; it
    # writes summary.json beside the inputs and exits 1 when the run failed
    # its correctness check (or timed out).
    chown -R 10001:10001 "$rehearsal_out"
    step "Report"
    "${isolated[@]}" --mount "type=bind,source=$rehearsal_out,target=/rehearsal" \
        "$IMAGE" local-rehearsal-report --input /rehearsal | tee "$rehearsal_out/report.txt" || rc=$?
    trap - EXIT
    rehearse_stopped "$rc"
    if [ "$rc" -eq 0 ]; then
        echo "Rehearsal $label passed in $(( $(date -u +%s) - began ))s; files in $rehearsal_out."
    else
        echo "Rehearsal $label did NOT pass (report exit $rc); files in $rehearsal_out." >&2
    fi
    return "$rc"
}

dcc=()
select_configured() {
    # Fill `dcc` with the configured topology (compose.json, plus the
    # fake-clock override in fake mode) whatever the setup marker says: the
    # runbook's post-wizard step recreates the consumers from it before
    # setup is complete.
    local mode
    mode=$(clock_mode)
    dcc=(docker compose -f "$services/compose.json")
    if [ "$mode" = fake ]; then
        refuse_real_oauth_client
        dcc+=(-f "$services/compose.faketime.json")
    fi
    dcc+=(-p "$project")
}

awaiting_ack() {
    # Both initial credential requests await their consumers' acknowledgement.
    local count
    count=$(docker compose -p "$project" exec -T postgres psql -U pk_stewardship_operator -d stewardship -Atc \
        "SELECT count(*) FROM stewardship_secret_request WHERE state='awaiting_ack' AND target IN ('parishsoft','google_workspace')" </dev/null 2>/dev/null)
    [ "$count" = 2 ]
}

setup_is_complete() {
    [ "$(setup_completed)" = t ]
}

cmd_wizard() {
    # Complete the setup wizard without a browser, through the wizard pages'
    # own service layer (seed_web.run_wizard), then the runbook's first-
    # installation step 5: once the installers report awaiting_ack, recreate
    # worker and mail-dispatch from compose.json and acknowledge each request
    # inside them, which lets the installer finish setup (finish_setup,
    # shared with `finish-setup`). Setup collects no campaign (#142), so the
    # seeder's campaign step then runs Create the campaign and loads its
    # Families. The developer-facing path is still the real wizard, finished
    # by `finish-setup`; this is for unattended installs and tests.
    require_marker
    load_env
    select_compose
    seed_now=$(date -u +%FT%TZ)
    seed_scale=1
    seed_step 1800 wizard web web.yaml
    if [ "$(jq -r .result <<<"$seed_result")" = frozen ]; then
        finish_setup "$(jq -r '.requests.parishsoft // empty' <<<"$seed_result")" \
            "$(jq -r '.requests.google_workspace // empty' <<<"$seed_result")"
    else
        echo "    setup needs no finishing"
    fi
    seed_step 1800 campaign web web.yaml
    echo "Setup and the campaign complete in $(( $(date -u +%s) - began ))s."
}

finish_setup() {
    # finish_setup PARISHSOFT WORKSPACE: the runbook's first-installation
    # step 5 in LOCAL, the one implementation `wizard` and `finish-setup`
    # share. Once the installers report awaiting_ack, recreate worker and
    # mail-dispatch from compose.json (plus the fake-clock override) and
    # acknowledge each request inside them, which lets the installer finish
    # setup; then restart everything under the configured topology. A
    # repeat acknowledgement is a no-op, so a rerun after an interruption
    # is safe. The ids are the frozen attempt's sealed credentials' ids.
    local parishsoft=$1 workspace=$2 uuid='^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
    [[ $parishsoft =~ $uuid && $workspace =~ $uuid ]] ||
        refuse "The frozen setup attempt lacks its ParishSoft or Google Workspace credential; refusing."
    step "Waiting for the credential installers to report awaiting_ack"
    wait_until 600 "the credential installers to report awaiting_ack" awaiting_ack
    step "Recreating worker and mail-dispatch from compose.json (runbook step 5)"
    select_configured
    "${dcc[@]}" up --detach --force-recreate worker mail-dispatch 2>&1 | quiet
    wait_until 300 "worker to turn healthy" all_healthy worker
    wait_until 300 "mail-dispatch to turn healthy" all_healthy mail-dispatch
    step "Acknowledging the credentials inside the recreated consumers"
    "${dcc[@]}" exec -T worker pk-stewardship acknowledge-credential \
        --config "$services/worker.yaml" --request-id "$parishsoft" </dev/null
    "${dcc[@]}" exec -T mail-dispatch pk-stewardship acknowledge-credential \
        --config "$services/mail-dispatch.yaml" --request-id "$workspace" </dev/null
    step "Waiting for setup to complete"
    wait_until 900 "the configuration installer to complete setup" setup_is_complete
    step "Restarting every service under the configured topology"
    start_services --force-recreate
}

cmd_finish_setup() {
    # After the browser wizard stops at Finishing setup ("Waiting for the
    # server operator"): look up the frozen attempt's request ids (the
    # seeder's read-only credentials step) and finish setup exactly as
    # `wizard` does. Already-complete setup is reported and left alone.
    require_marker
    load_env
    select_compose
    if setup_is_complete; then
        echo "Setup is already complete; nothing to finish."
        return 0
    fi
    seed_now=$(date -u +%FT%TZ)
    seed_scale=1
    seed_step 300 credentials web web.yaml
    case "$(jq -r .result <<<"$seed_result")" in
        frozen) ;;
        "setup was already complete")
            echo "Setup is already complete; nothing to finish."
            return 0 ;;
        *) refuse "The setup wizard has not reached Finishing setup; complete its pages in the browser first, then run finish-setup again." ;;
    esac
    finish_setup "$(jq -r '.requests.parishsoft // empty' <<<"$seed_result")" \
        "$(jq -r '.requests.google_workspace // empty' <<<"$seed_result")"
    echo "Setup complete in $(( $(date -u +%s) - began ))s."
}

reads_fake_parishsoft() {
    # Succeeds only when every service document can be read and none names
    # a ParishSoft base URL (#1399's default: the fake). After `deploy
    # --parishsoft real` the worker reads the real API, which a fake edit
    # cannot change and a refresh must never be aimed at from here. A
    # document jq cannot read fails closed: callers run this under `||`,
    # where errexit does not apply, so each failure returns explicitly.
    local file value found=0
    for file in "$services"/*.yaml; do
        [ -f "$file" ] || continue
        found=1
        value=$(jq -r '.deployment.parishsoft_base_url // empty' "$file") || return 1
        [ -z "$value" ] || return 1
    done
    [ "$found" = 1 ]
}

fake_parishsoft_pledge_defaults() {
    # fake_parishsoft_pledge_defaults WORDS...: for a `pledge` edit without
    # fund= or start=, print the current campaign's pledge fund and period
    # start as those options (the seeder's read-only pledge-period step), so
    # the pledge lands where the refresh reads giving. Prints nothing when
    # both were given. It runs under `||` (errexit is off), so every
    # failure is checked explicitly.
    local word fund=0 start=0 value
    for word in "$@"; do
        case "$word" in fund=*) fund=1 ;; start=*) start=1 ;; esac
    done
    [ "$fund" = 0 ] || [ "$start" = 0 ] || return 0
    [ "$(setup_completed)" = t ] || refuse "The setup wizard has not completed; give fund= and start= for the pledge."
    seed_now=$(date -u +%FT%TZ) seed_scale=1
    seed_step 600 pledge-period web web.yaml >&2 ||
        refuse "Could not read the campaign's pledge fund and period (see above); give fund= and start=."
    if [ "$fund" = 0 ]; then
        value=$(jq -er .fund <<<"$seed_result" 2>/dev/null) ||
            refuse "Could not read the campaign's pledge fund and period (see above); give fund= and start=."
        printf 'fund=%s\n' "$value"
    fi
    if [ "$start" = 0 ]; then
        value=$(jq -er .start <<<"$seed_result" 2>/dev/null) ||
            refuse "Could not read the campaign's pledge fund and period (see above); give fund= and start=."
        printf 'start=%s\n' "$value"
    fi
}

cmd_fake_parishsoft() {
    # fake-parishsoft WORDS... (#1780): change the fake ParishSoft's data,
    # show it, or run the Admin's full refresh of it. Every form refuses
    # unless this is a marked pk-local root whose deployment reads the fake
    # and has the fake's configuration file; Production has neither.
    local file="$root/run/local/fake-parishsoft.json" new line defaults status
    local -a words=("$@")
    require_marker
    load_env
    [ -f "$file" ] || refuse "No fake ParishSoft configuration at $file; refusing."
    reads_fake_parishsoft ||
        refuse "This deployment reads the real ParishSoft (deploy --parishsoft real), or a service document is unreadable; fake-parishsoft refuses. Deploy with --parishsoft fake first."
    case "${1-}" in
        "" | -h | --help | help)
            refuse "fake-parishsoft needs a command; see tools/stewardship-local.sh --help" ;;
        refresh)
            [ $# -eq 1 ] || refuse "fake-parishsoft refresh takes no arguments"
            [ "$(setup_completed)" = t ] || refuse "The setup wizard has not completed; there is nothing to refresh yet."
            select_compose
            seed_now=$(date -u +%FT%TZ) seed_scale=1
            seed_step 1500 refresh web web.yaml
            echo "Full ParishSoft refresh finished: $seed_result" ;;
        show)
            "${isolated[@]}" -i "$IMAGE" fake-parishsoft-edit "$@" <"$file" ;;
        *)
            # Everything that can refuse runs before the file is written, so
            # a refusal leaves the file and the running fake in step.
            select_compose
            if [ "$1" = pledge ]; then
                # A command substitution, not a process substitution, so a
                # refusal inside it ends this command too.
                defaults=$(fake_parishsoft_pledge_defaults "$@") || exit 1
                while IFS= read -r line; do
                    [ -z "$line" ] || words+=("$line")
                done <<<"$defaults"
            fi
            # The fake stops before its file is read (#1884): it records each
            # contact PUT that Update ParishSoft sends in this file, so a PUT
            # answered while the edit runs would be lost when the new
            # document replaces the file. A stopped fake answers no PUT (the
            # write fails as unavailable, as in any ParishSoft outage); a
            # refusal starts it again unchanged.
            step "Stopping the fake ParishSoft for the edit"
            "${dc[@]}" stop fake-parishsoft 2>&1 | quiet
            # The image checks the edit against the generated parish and
            # prints the new document; nothing is written unless it is one.
            status=0
            new=$("${isolated[@]}" -i "$IMAGE" fake-parishsoft-edit "${words[@]}" <"$file") || status=$?
            if [ "$status" -ne 0 ] || ! jq -e 'type == "object" and has("seed")' >/dev/null <<<"$new"; then
                "${dc[@]}" start fake-parishsoft 2>&1 | quiet
                wait_until 120 "the fake ParishSoft to turn healthy" all_healthy fake-parishsoft
                [ "$status" -eq 0 ] || exit "$status"
                refuse "The edit command did not answer with a configuration document; nothing was changed."
            fi
            printf '%s\n' "$new" | private_file "$file"
            # A container keeps a bind mount on the inode it started with,
            # and private_file replaces the file, so the service is recreated.
            step "Recreating the fake ParishSoft with the new data"
            "${dc[@]}" up --detach --no-deps --force-recreate fake-parishsoft 2>&1 | quiet
            wait_until 120 "the fake ParishSoft to turn healthy" all_healthy fake-parishsoft
            echo "The fake ParishSoft now serves the edit; run 'fake-parishsoft refresh' (or Refresh from ParishSoft in the Admin portal) for the application to read it." ;;
    esac
}

cmd_sign_in() {
    require_marker
    load_env
    select_compose
    # Like every operator command, it takes the service's own configuration.
    "${dc[@]}" exec -T web pk-stewardship local-sign-in --config "$services/web.yaml" --email "$1"
}

cmd_ca() {
    require_marker
    local crt="$root/run/persistent/caddy/data/caddy/pki/authorities/local/root.crt"
    [ -f "$crt" ] || refuse "Caddy has not written its local root certificate yet ($crt)."
    cat "$crt"
}

spike_label=""
spike_stopped() {
    # Remove this run's shard containers that are still there; bounded and
    # logged, never fatal (it also runs from the EXIT trap).
    local -a ids=()
    [ -n "$spike_label" ] || return 0
    while IFS= read -r line; do [ -z "$line" ] || ids+=("$line"); done < <(
        docker ps -aq --filter "label=$spike_label" 2>/dev/null)
    [ "${#ids[@]}" -eq 0 ] ||
        timed 60 "removing leftover spike shards" docker rm -f "${ids[@]}" >/dev/null || true
}

cmd_spike() {
    # The launch-day spike check (local-environment specification, "Spike
    # check"; spike.py). Each shard is a one-off container on the ingress
    # network, so it reaches Caddy and Mailpit by name and is its own source
    # address; the report runs offline over the shard documents.
    local families=$1 sources=$2 during_send=$3 wait=$4 label=$5
    local out since due started before after rc=0 shard pids=() failed=0
    [[ $families =~ ^[0-9]+$ && $sources =~ ^[0-9]+$ && $during_send =~ ^[01]$ && $wait =~ ^[0-9]+$ ]] ||
        refuse "spike needs whole-number FAMILIES, SOURCES, DURING_SEND (0 or 1) and WAIT."
    [[ $label =~ ^[A-Za-z0-9._-]{1,40}$ ]] || refuse "spike LABEL must be 1-40 letters, digits, '.', '_' or '-'."
    require_marker
    load_env
    [ "$(clock_mode)" = normal ] ||
        refuse "The spike check runs on a seeded deployment (normal clock mode); seed it first, or 'reset --seeded'."
    select_compose
    [ "$(setup_completed)" = t ] || refuse "The setup wizard has not completed; refusing."
    # psql_query (the deadlock counter) runs in this container, as rehearse's do.
    pg_container=$("${dc[@]}" ps -q postgres)
    [ -n "$pg_container" ] || refuse "postgres is not running; 'start' first."
    local crt="$root/run/persistent/caddy/data/caddy/pki/authorities/local/root.crt"
    [ -f "$crt" ] || refuse "Caddy has not written its local root certificate yet ($crt)."
    # STEWARDSHIP_LOG_DIR lets the tests run this half without /var/log.
    out=${STEWARDSHIP_LOG_DIR:-/var/log}/stewardship-spike-$label-$(date -u +%Y%m%dT%H%M%SZ)
    install -d -m 0755 "$out"
    install -m 0644 "$crt" "$out/root.crt"
    started=$(date -u +%FT%TZ)
    before=$(psql_query "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
    if [ "$during_send" = 1 ]; then
        # Families read only mail that arrives from now on: the new send's.
        since=$started
        due=$(date -u -d "@$(( ($(date -u +%s) / 60 + 3) * 60 ))" +%FT%TZ)
        step "Spike $label: a Reminder due $due, then $families Families from $sources sources as its mail arrives"
        seed_now=$(date -u +%FT%TZ)
        seed_scale=1
        seed_step 600 reminder web web.yaml --due-at "$due"
    else
        since=1970-01-01T00:00:00Z
        step "Spike $label: $families Families from $sources sources, from the mail already in Mailpit"
    fi
    echo "    files: $out"
    chown -R 10001:10001 "$out"
    # Every shard container carries this run's label, so an interrupted run
    # (or a shard its time limit killed) leaves none behind.
    spike_label="parishkit.local.spike=$label-$$"
    trap 'spike_stopped' EXIT
    for (( shard = 0; shard < sources; shard++ )); do
        timed $(( wait * 60 + 300 )) "spike shard $shard" \
            docker run --rm --init --label "$spike_label" \
            --user 10001:10001 --read-only --cap-drop ALL \
            --security-opt no-new-privileges:true --network "${project}_ingress" \
            --mount "type=bind,source=$out,target=/spike,readonly" "$IMAGE" \
            local-spike --profile local --shard "$shard" --shards "$sources" \
            --families "$families" --concurrency 4 --since "$since" \
            --wait-seconds $(( wait * 60 )) --ca-file /spike/root.crt \
            >"$out/shard-$shard.json" &
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do wait "$pid" || failed=$((failed + 1)); done
    [ "$failed" -eq 0 ] || echo "    $failed of $sources shards failed; the report reads the rest." >&2
    after=$(psql_query "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
    jq -n --arg label "$label" --arg image "$IMAGE" --argjson families "$families" \
        --argjson sources "$sources" \
        --argjson during_send "$( [ "$during_send" = 1 ] && echo true || echo false )" \
        --arg started "$started" --arg finished "$(date -u +%FT%TZ)" \
        --argjson before "${before:-null}" --argjson after "${after:-null}" \
        --argjson failed "$failed" \
        '{label: $label, image: $image, families: $families, sources: $sources,
          during_send: $during_send, started_at: $started, finished_at: $finished,
          deadlocks_before: $before, deadlocks_after: $after,
          failed_shards: $failed}' >"$out/meta.json"
    chown -R 10001:10001 "$out"
    step "Report"
    trap - EXIT
    spike_stopped
    "${isolated[@]}" --mount "type=bind,source=$out,target=/spike" \
        "$IMAGE" local-spike-report --input /spike | tee "$out/report.txt" || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "Spike $label passed; files in $out."
    else
        echo "Spike $label did NOT pass (report exit $rc); files in $out." >&2
    fi
    return "$rc"
}

# ---------------------------------------------------------------------------
# Long-running commands keep their whole log on the VM under /var/log, as
# the upgrade script does; the short ones print only their answer.
case "$command" in
    build|up|snapshot|snapshot-delete|snapshot-restore|reset|wipe|seed|seed-testing|wizard|finish-setup|start|down|deploy|rollback|rehearse|spike|fake-parishsoft)
        # STEWARDSHIP_LOG_DIR lets the tests run this half without /var/log.
        log=${STEWARDSHIP_LOG_DIR:-/var/log}/stewardship-local-$command-$(date -u +%Y%m%dT%H%M%SZ).log
        exec > >(tee -a "$log") 2>&1
        echo "Log: $log" ;;
esac

case "$command" in
    build) cmd_build "${1:?build needs TAG}" ;;
    up) [ $# -eq 4 ] || refuse "up needs TAG FAMILIES ADMIN_EMAIL DEBUG"; cmd_up "$@" ;;
    start) cmd_start ;;
    down) cmd_down ;;
    status) cmd_status "${1-}" ;;
    snapshot)
        [ $# -eq 1 ] || [ $# -eq 4 ] || refuse "snapshot needs NAME [REPLACE MAC_FREE_KB LIMIT]"
        cmd_snapshot "$@" ;;
    snapshot-list) cmd_snapshot_list "${1-}" ;;
    snapshot-delete) cmd_snapshot_delete "${1:?snapshot-delete needs NAME}" ;;
    snapshot-restore) [ $# -eq 2 ] || refuse "snapshot-restore needs NAME HOST_SCRIPT"; cmd_restore "$@" ;;
    seed-testing) cmd_seed_testing "${1:?seed-testing needs HOST_SCRIPT}" ;;
    has-snapshot) cmd_has_snapshot "${1:?has-snapshot needs NAME}" ;;
    reset) cmd_reset "${1:?reset needs NAME}" ;;
    wipe) cmd_wipe ;;
    deploy)
        [ $# -eq 3 ] || [ $# -eq 5 ] || [ $# -eq 6 ] || [ $# -eq 7 ] ||
            refuse "deploy needs TAG SCHEMA_CHANGE HOST_SCRIPT [BULK LATENCY [PARISHSOFT [MAIL_REDIRECT]]]"
        cmd_deploy "$@" ;;
    rehearse) [ $# -eq 4 ] || refuse "rehearse needs DUE_IN SEND_ONLY TIMEOUT LABEL"; cmd_rehearse "$@" ;;
    spike) [ $# -eq 5 ] || refuse "spike needs FAMILIES SOURCES DURING_SEND WAIT LABEL"; cmd_spike "$@" ;;
    rollback) cmd_rollback "${1:?rollback needs HOST_SCRIPT}" ;;
    seed) cmd_seed "${1:-1}" ;;
    wizard) cmd_wizard ;;
    finish-setup) cmd_finish_setup ;;
    sign-in) cmd_sign_in "${1:?sign-in needs EMAIL}" ;;
    fake-parishsoft) cmd_fake_parishsoft "$@" ;;
    ca) cmd_ca ;;
    *) refuse "unknown VM command: $command" ;;
esac
