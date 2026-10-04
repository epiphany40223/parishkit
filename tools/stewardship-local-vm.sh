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
#   status                              service health, Docker and VM disk use
#   snapshot NAME                       stop, copy the root to SNAPSHOTS/NAME, restart
#   has-snapshot NAME                   exit 0 when SNAPSHOTS/NAME exists
#   reset NAME                          stop, restore SNAPSHOTS/NAME over the root, restart
#   wipe                                remove the containers and the root (marker required)
#   seed RESPONSE_SCALE                 seed the campaign (OPS-10.07; refuses until the
#                                       image carries the seeder)
#   sign-in EMAIL                       print a local test sign-in link (OPS-10.08)
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
    "${isolated[@]}" \
        --mount "type=bind,source=$root,target=$root" \
        --mount "type=bind,source=$yaml,target=/run/operator.yaml,readonly" \
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
    # The sentinel has the client document's shape and no real client; LOCAL
    # admits only it once OPS-10.05 lands, and an image without that rule
    # accepts it as an ordinary client document.
    image_constant parishkit.stewardship.runtime_web LOCAL_OAUTH_DOCUMENT \
        '{"client_id": "local-environment-sentinel.invalid", "client_secret": "local-environment-sentinel-not-a-secret"}' |
        private_file "$root/credentials/google_oauth/credential"
    install -d -o 10001 -g 10001 -m 0700 "$root/run/local" "$clock_dir"
    # The fake reads this once at start (specification, "Fake configuration");
    # release_at stays null until the seeder's final phase.
    printf '{"seed": %s, "families": %s, "anchor_date": "%s", "release_at": null}\n' \
        "$seed" "$families" "$anchor" | private_file "$root/run/local/fake-parishsoft.json"
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
  Mail:       http://localhost:8025    (Mailpit; rendered once OPS-10.05 lands)
  Admin:      $ADMIN_EMAIL
  Sign in:    tools/stewardship-local.sh sign-in --email $ADMIN_EMAIL   (OPS-10.08)
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
    echo "VM disk:"
    df -h / | tail -1
    echo "Docker disk use:"
    docker system df
    if [ -f "$marker" ]; then
        load_env
        echo "Deployment $UUID ($IMAGE), clock mode $(cat "$clock_dir/mode" 2>/dev/null || echo missing)"
        echo "Services (state, health):"
        service_states | awk '{ print "    " $1, $2, ($3 == "none" ? "-" : $3) }'
        for name in post-setup seeded; do
            if [ -f "$snapshots/$name/taken" ]; then
                echo "snapshot $name: taken $(cat "$snapshots/$name/taken")"
            fi
        done
    elif [ -e "$installing" ]; then
        echo "A previous 'up' did not finish; 'reset --reinstall' removes its root and installs again."
    else
        echo "No local deployment at $root."
    fi
}

cmd_snapshot() {
    local name=$1 completed
    require_marker
    load_env
    select_compose
    completed=$(setup_completed)
    [ "$completed" = t ] || echo "    note: the setup wizard has not completed; this is a pre-wizard snapshot"
    step "Stopping the services for the $name snapshot"
    stop_services
    step "Copying $root to $snapshots/$name"
    install -d -m 0700 "$snapshots" "$snapshots/$name"
    # Numeric ownership, modes and hard links preserved; the deployment YAML
    # and record travel with the root so a restore is self-contained.
    rsync -aHAX --delete --numeric-ids "$root/" "$snapshots/$name/root/"
    cp -p "$yaml" "$snapshots/$name/deployment.yaml"
    cp -p "$envfile" "$snapshots/$name/deployment.env"
    date -u +%FT%TZ >"$snapshots/$name/taken"
    echo "    $(du -sh "$snapshots/$name" | cut -f1) copied"
    start_services
}

cmd_has_snapshot() {
    [ -d "$snapshots/$1/root" ]
}

cmd_reset() {
    local name=$1
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

cmd_seed() {
    # The seeder's phases are OPS-10.07; this command takes the lock, keeps
    # the log and refuses until the image carries `local-seed`.
    local scale=$1 lock="$snapshots/.seed.lock"
    require_marker
    load_env
    install -d -m 0700 "$snapshots"
    exec 9>"$lock"
    flock -n 9 || refuse "Another seed is running (lock $lock); refusing."
    if docker run --rm --network none "$IMAGE" local-seed 2>&1 | grep -q "invalid command"; then
        refuse "This image has no 'local-seed' command (OPS-10.07); seeding is not available yet."
    fi
    refuse "The seeder phases are not wired yet (OPS-10.07): response scale $scale, families $FAMILIES, seed $SEED, anchor $ANCHOR_DATE."
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

# ---------------------------------------------------------------------------
# Long-running commands keep their whole log on the VM under /var/log, as
# the upgrade script does; the short ones print only their answer.
case "$command" in
    build|up|snapshot|reset|wipe|seed|start|down)
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
    status) cmd_status ;;
    snapshot) cmd_snapshot "${1:?snapshot needs NAME}" ;;
    has-snapshot) cmd_has_snapshot "${1:?has-snapshot needs NAME}" ;;
    reset) cmd_reset "${1:?reset needs NAME}" ;;
    wipe) cmd_wipe ;;
    seed) cmd_seed "${1:-1}" ;;
    sign-in) cmd_sign_in "${1:?sign-in needs EMAIL}" ;;
    ca) cmd_ca ;;
    *) refuse "unknown VM command: $command" ;;
esac
