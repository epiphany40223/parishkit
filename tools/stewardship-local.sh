#!/usr/bin/env bash
# Drive the Stewardship local laptop environment (#476): a production-shaped
# deployment in a Lima VM, installed from the current checkout by the same
# steps the deployment runbook gives for a production host. This is the
# laptop half: it checks the VM, packs the checkout and uploads the VM half
# (tools/stewardship-local-vm.sh), which does everything else inside the VM
# as root. The specification is docs/specs/stewardship/local-environment/spec.md
# ("Operator script"); the developer guide is
# docs/guides/stewardship-local-environment.md.
#
# Usage: tools/stewardship-local.sh COMMAND [OPTIONS]
#
#   vm create | vm start | vm stop   create (and start), start or stop the VM
#   up [--families N]                first-time install from this checkout
#   start                            start a stopped deployment's services
#   deploy [--schema-change] [--bulk on|off] [--smtp-latency-ms N]
#                                    build this checkout and upgrade the running
#                                    deployment to it by the Production upgrade's steps;
#                                    --bulk renders the bulk Family send on or off and
#                                    --smtp-latency-ms the rehearsal's modeled Gmail
#                                    latency (both carry over when not given)
#   deploy --rollback                image-only rollback to the image the last deploy replaced
#                                    (deploy also passes the STEWARDSHIP_USERS_* variables
#                                    of the users upgrade, #952, to the host half)
#   snapshot [--seeded]              save the root as the post-setup or seeded snapshot
#   snapshot save NAME [--replace]   save the root as the named snapshot (refused when
#                                    the disks or the snapshot cap would not allow it)
#   snapshot list                    the snapshots, what a switch to each would do,
#                                    and the VM's and the Mac's free space
#   snapshot delete NAME             delete one snapshot (type its name to confirm)
#   snapshot restore NAME | switch NAME
#                                    keep the deployed code and switch to NAME's data,
#                                    migrating it forward when it is older
#   reset [--seeded | NAME]          restore that snapshot whole, image and data; with
#                                    no post-setup snapshot, wipe the root and run `up` again
#   reset --reinstall                build from this checkout, then wipe the root
#                                    and run `up` again, keeping the snapshots
#   seed [--response-scale M]        seed the campaign (the fake-clock seeder)
#   seed --testing                   switch to the post-setup snapshot and move it to real
#                                    time: a Testing-mode draft campaign to review
#   reseed [--response-scale M]      reset to the post-setup snapshot, then seed
#   rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]
#                                    add a Reminder due in MIN minutes (default 5),
#                                    measure its send and print the BG-12 report
#   spike [--families N] [--sources S] [--during-send] [--wait MIN] [--label NAME]
#                                    the launch-day spike check (#392 M3): N Families
#                                    (default 200) open their emailed links and submit
#                                    through Caddy from S sources (default 8), beside a
#                                    new Reminder send with --during-send
#   wizard                           complete the setup wizard unattended, then
#                                    create the campaign and load its Families
#   status                           VM, services, Docker and VM disk use
#   down                             stop the services; never removes data
#   sign-in --email E                print a local test sign-in link
#   ca                               fetch Caddy's root certificate and say how to trust it
#
# Configuration (environment variables):
#   PARISHKIT_LOCAL_VM             Lima instance name (default parishkit-local)
#   PARISHKIT_LOCAL_ADMIN_EMAIL    the initial Administrator (default admin@example.test)
#   PARISHKIT_LOCAL_FAMILIES       synthetic parish size for `up` (default 100)
#   PARISHKIT_LOCAL_DEBUG_LOGGING  1 (default) starts the services with debug
#                                  logging, as pre-launch dev deploys did; 0 turns it off
#   PARISHKIT_LOCAL_ROOT           runtime root in the VM (default /opt/parishkit)
#   PARISHKIT_LOCAL_SNAPSHOT_LIMIT how many snapshots a save allows (default 8)
#   LIMA_HOME                      where Lima keeps its instances (default ~/.lima)
#
# This half runs under macOS's own bash 3.2; it needs only Lima
# (brew install lima), git, tar, ssh and df. The script never targets a host
# other than the Lima instance's ssh alias. It packs the checkout that
# contains the current directory, so a pull request is tested by running it
# from that pull request's checkout.

set -euo pipefail

vm=${PARISHKIT_LOCAL_VM:-parishkit-local}
admin=${PARISHKIT_LOCAL_ADMIN_EMAIL:-admin@example.test}
families=${PARISHKIT_LOCAL_FAMILIES:-100}
debug=${PARISHKIT_LOCAL_DEBUG_LOGGING:-1}
root=${PARISHKIT_LOCAL_ROOT:-/opt/parishkit}
snapshot_limit=${PARISHKIT_LOCAL_SNAPSHOT_LIMIT:-8}
lima_home=${LIMA_HOME:-$HOME/.lima}
project=parishkit-local
yaml=/etc/parishkit/stewardship-deployment.yaml
envfile=/etc/parishkit/stewardship-local.env
build=/var/tmp/parishkit-local-build
snapshots=/opt/parishkit-snapshots
state=$HOME/.parishkit-local
here=$(cd "$(dirname "$0")" && pwd)
remote=$here/stewardship-local-vm.sh
# The scripted upgrade's host half (shared with Production), which `deploy`
# uploads to a fixed path in the VM and the VM half runs in local mode.
host_script=$here/stewardship-upgrade-host.sh
host_remote=/var/tmp/parishkit-local-upgrade-host.sh
vm_yaml=$here/../deploy/stewardship/lima-local.yaml

usage() {
    # usage [EXIT]: the command summary above; exit 2 unless asked for.
    sed -n '/^# Usage:/,/^# Configuration/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
    exit "${1:-2}"
}
refuse() {
    echo "$*" >&2
    exit 1
}
need() {
    command -v "$1" >/dev/null 2>&1 || refuse "$1 is required: $2"
}

# The runtime root is deleted by `wipe`, so it must be a real path: absolute,
# at least two components (never / or /opt) and without any `..`.
case "$root" in
    *..*) refuse "PARISHKIT_LOCAL_ROOT must not contain '..': $root" ;;
    /?*/?*) ;;
    *) refuse "PARISHKIT_LOCAL_ROOT must be an absolute path with at least two components, such as /opt/parishkit: $root" ;;
esac

vm_status() {
    # Running, Stopped, or empty when there is no such instance.
    limactl list "$vm" --format '{{.Status}}' 2>/dev/null || true
}
require_running() {
    case "$(vm_status)" in
        Running) ;;
        "") refuse "No Lima instance '$vm'. Create it with: $0 vm create" \
            "(or set PARISHKIT_LOCAL_VM to an existing instance's name)." ;;
        *) refuse "Lima instance '$vm' is not running. Start it with: $0 vm start" ;;
    esac
}
ssh_vm() {
    # Lima's generated ssh configuration, without its connection multiplexing
    # (a shared master would outlive this run).
    ssh -F "$lima_home/$vm/ssh.config" -o ControlPath=none "lima-$vm" "$@"
}
run_remote() {
    # Upload the VM half to a file and run it there as root. It is uploaded
    # rather than fed to bash's stdin so that `docker compose run` inside it
    # cannot read the rest of the script as its own input.
    # remote_env, when a command sets it, is an `env NAME=value ...` prefix
    # (the users upgrade's inputs on a deploy); sudo passes it to the VM half.
    local args
    args=$(printf '%q ' "$root" "$project" "$yaml" "$envfile" "$build" "$snapshots" "$@")
    ssh_vm "f=\$(mktemp) && cat > \"\$f\" && sudo ${remote_env-}bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$remote"
}

pack_checkout() {
    # Send the tracked files of the checkout that contains the current
    # directory, as they are on disk now (including uncommitted edits; a
    # deleted tracked file is simply absent, an untracked one is not sent),
    # to the VM's build directory, and set `tag` to the LOCAL image tag.
    local top dirty branch
    top=$(git rev-parse --show-toplevel 2>/dev/null || true)
    [ -n "$top" ] || refuse "Not inside a git work tree; run this from the checkout to build."
    cd "$top"
    dirty=$(git diff --quiet HEAD -- && echo "" || echo "-dirty")
    branch=$(git rev-parse --abbrev-ref HEAD)
    tag="parishkit-stewardship-local:$(git rev-parse HEAD)${dirty}-$(date -u +%s)"
    echo "==> Building checkout $top ($branch at $(git rev-parse --short HEAD)${dirty}) in $vm"
    echo "    image $tag"
    # macOS tar would add resource-fork and extended-attribute entries that
    # mean nothing to the Linux build; GNU tar has no such options.
    local -a tar_options=()
    if [ "$(uname -s)" = Darwin ]; then
        tar_options=(--no-mac-metadata --no-xattrs)
    fi
    git ls-files -z | while IFS= read -r -d '' path; do
        if [ -e "$path" ]; then printf '%s\0' "$path"; fi
    done | COPYFILE_DISABLE=1 tar "${tar_options[@]+"${tar_options[@]}"}" --null -T - -czf - |
        ssh_vm "sudo rm -rf '$build' && sudo mkdir -p '$build' && sudo tar -xzf - -C '$build'"
}

upload_host_script() {
    # Put the scripted upgrade's host half where the VM half's deploy and
    # rollback commands run it (rewritten on every call, so the VM never
    # keeps a stale copy).
    ssh_vm "sudo install -m 0644 /dev/stdin '$host_remote'" <"$host_script"
}

confirm_instance_name() {
    # Deleting the root needs the operator to type the instance name.
    local typed
    echo "This removes the local deployment's containers and runtime root in $vm" >&2
    printf 'Type the instance name (%s) to continue: ' "$vm" >&2
    read -r typed
    [ "$typed" = "$vm" ] || refuse "Not confirmed; nothing changed."
}

mac_free_kb() {
    # Free KiB on the Mac volume that holds the Lima instances, where the
    # VM's sparse disk file grows; the VM half's disk guard reads it. Run in
    # $(...), where bash 3.2 does not stop on a failure, so an answer that
    # is not a number is refused here rather than skipping the check.
    local dir=$lima_home kb
    [ -d "$dir" ] || dir=$HOME
    kb=$(df -Pk "$dir" 2>/dev/null | awk 'NR == 2 { print $4 }')
    case "$kb" in
        ''|*[!0-9]*) refuse "Could not read the Mac's free space on $dir; refusing." ;;
    esac
    printf '%s' "$kb"
}

users_env_prefix() {
    # The users upgrade's four STEWARDSHIP_USERS_* variables (#952) as an
    # `env NAME=value ...` prefix for run_remote, or nothing; the host half
    # validates them. A deploy passes them, and so does a switch or
    # `seed --testing` whose upgrade path converts a snapshot from before
    # the conversion.
    local name prefix=""
    for name in STEWARDSHIP_USERS_OPERATION STEWARDSHIP_USERS_OPERATOR \
        STEWARDSHIP_USERS_REASON STEWARDSHIP_USERS_ADMINISTRATORS; do
        [ -z "${!name-}" ] || prefix+="$name=$(printf '%q' "${!name}") "
    done
    [ -z "$prefix" ] || printf 'env %s' "$prefix"
}

check_snapshot_name() {
    # The VM half checks the name too; this refuses before any ssh.
    [[ $1 =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
        refuse "A snapshot name is 1-32 lowercase letters, digits or '-', starting with a letter or digit: '$1'."
}

save_snapshot() {
    # save_snapshot NAME REPLACE: the VM half checks the space and the cap
    # before it stops anything.
    local kb
    require_running
    kb=$(mac_free_kb) || exit 1
    run_remote snapshot "$1" "$2" "$kb" "$snapshot_limit"
}

switch_snapshot() {
    # The data-only switch; its upgrade path runs the scripted upgrade's
    # host half, so that is uploaded first, as for a deploy.
    check_snapshot_name "$1"
    require_running
    upload_host_script
    remote_env=$(users_env_prefix)
    run_remote snapshot-restore "$1" "$host_remote"
    remote_env=""
}

delete_snapshot() {
    # A snapshot is a deliberate return point: the operator types its name.
    local typed
    check_snapshot_name "$1"
    require_running
    run_remote has-snapshot "$1" || refuse "No $1 snapshot; '$0 snapshot list' lists them."
    printf 'Type the snapshot name (%s) to delete it: ' "$1" >&2
    read -r typed
    [ "$typed" = "$1" ] || refuse "Not confirmed; nothing changed."
    run_remote snapshot-delete "$1"
}

check_families() {
    if ! [[ $families =~ ^[0-9]+$ ]] || [ "$families" -lt 1 ]; then refuse "--families must be a positive integer."; fi
}

do_up() {
    # Pack, then install (the VM half builds the image unless `reinstall`
    # already did).
    require_running
    check_families
    pack_checkout
    run_remote up "$tag" "$families" "$admin" "$debug"
    echo "To trust Caddy's certificate in the browser: $0 ca"
}

do_reinstall() {
    # Confirm, build the new image while the deployment still runs (a build
    # failure then changes nothing), wipe, and install from that image.
    require_running
    check_families
    confirm_instance_name
    pack_checkout
    run_remote build "$tag"
    run_remote wipe
    run_remote up "$tag" "$families" "$admin" "$debug"
    echo "To trust Caddy's certificate in the browser (its CA is new): $0 ca"
}

case "${1-}" in -h|--help|help) usage 0 ;; esac

need limactl "brew install lima"
need git "install Xcode's command-line tools"
[ -f "$remote" ] || refuse "Missing $remote"
[ -f "$host_script" ] || refuse "Missing $host_script"

command=${1-}
[ $# -ge 1 ] && shift
case "$command" in
    vm)
        case "$#:${1-}" in
            1:create)
                [ -z "$(vm_status)" ] || refuse "Lima instance '$vm' already exists."
                [ -f "$vm_yaml" ] || refuse "Missing $vm_yaml"
                echo "==> Creating and starting the Lima instance $vm (this installs Docker Engine)"
                limactl start --name="$vm" --tty=false "$vm_yaml" ;;
            1:start) limactl start "$vm" ;;
            1:stop) limactl stop "$vm" ;;
            *) usage ;;
        esac ;;
    up)
        while [ $# -gt 0 ]; do
            case "$1" in
                --families) [ $# -ge 2 ] || usage; families=$2; shift 2 ;;
                *) usage ;;
            esac
        done
        do_up ;;
    start)
        [ $# -eq 0 ] || usage
        require_running
        run_remote start ;;
    deploy)
        # The scripted upgrade in local mode (specification, "Operator
        # script"): build the image from this checkout while the deployment
        # still runs, then the VM half runs the same host half Production's
        # tools/stewardship-upgrade.sh runs. --schema-change is Production's
        # STEWARDSHIP_SCHEMA_CHANGE=1; --rollback is its --rollback.
        # --bulk and --smtp-latency-ms (BG-12's rehearsal) choose what the
        # host half renders in local mode; "keep" carries the current value.
        # The users upgrade's four STEWARDSHIP_USERS_* variables (#952) are
        # passed through as Production's laptop half passes them; the host
        # half validates them.
        schema_change=0 rollback=0 bulk=keep latency=keep
        users_env=$(users_env_prefix)
        while [ $# -gt 0 ]; do
            case "$1" in
                --schema-change) schema_change=1 ;;
                --rollback) rollback=1 ;;
                --bulk)
                    [ $# -ge 2 ] || usage
                    case "$2" in on|off) bulk=$2 ;; *) usage ;; esac
                    shift ;;
                --smtp-latency-ms)
                    [ $# -ge 2 ] || usage
                    # Base 10 whatever the leading zeros (0600 is 600, not octal).
                    if ! [[ $2 =~ ^[0-9]{1,5}$ ]] || [ "$((10#$2))" -gt 5000 ]; then usage; fi
                    latency=$((10#$2))
                    shift ;;
                *) usage ;;
            esac
            shift
        done
        [ "$schema_change$rollback" != 11 ] || usage
        # A rollback never converts.
        if [ "$rollback" = 1 ] && [ -n "$users_env" ]; then usage; fi
        # A rollback renders nothing new: it takes neither rendering flag.
        if [ "$rollback" = 1 ] && [ "$bulk$latency" != keepkeep ]; then usage; fi
        require_running
        if [ "$rollback" = 1 ]; then
            upload_host_script
            run_remote rollback "$host_remote"
        else
            pack_checkout
            run_remote build "$tag"
            upload_host_script
            # Only the deploy itself carries the users upgrade's inputs.
            remote_env=$users_env
            if [ "$bulk$latency" = keepkeep ]; then
                run_remote deploy "$tag" "$schema_change" "$host_remote"
            else
                run_remote deploy "$tag" "$schema_change" "$host_remote" "$bulk" "$latency"
            fi
            remote_env=""
        fi ;;
    rehearse)
        # BG-12's rehearsal on a seeded deployment (developer guide,
        # "Rehearsing a bulk send"): the VM half adds the Reminder, samples
        # the work-order lock, waits for the send and prints the report.
        due_in=5 send_only=0 limit=60 label=run
        while [ $# -gt 0 ]; do
            case "$1" in
                --due-in) [ $# -ge 2 ] || usage; due_in=$2; shift 2 ;;
                --timeout) [ $# -ge 2 ] || usage; limit=$2; shift 2 ;;
                --label) [ $# -ge 2 ] || usage; label=$2; shift 2 ;;
                --send-only) send_only=1; shift ;;
                *) usage ;;
            esac
        done
        if ! [[ $due_in =~ ^[0-9]{1,3}$ ]] || [ "$((10#$due_in))" -lt 2 ] || [ "$((10#$due_in))" -gt 180 ]; then
            refuse "--due-in must be a whole number of minutes from 2 to 180."
        fi
        if ! [[ $limit =~ ^[0-9]{1,3}$ ]] || [ "$((10#$limit))" -lt 1 ] || [ "$((10#$limit))" -gt 480 ]; then
            refuse "--timeout must be a whole number of minutes from 1 to 480."
        fi
        due_in=$((10#$due_in)) limit=$((10#$limit))
        [[ $label =~ ^[A-Za-z0-9._-]{1,40}$ ]] || refuse "--label must be 1-40 letters, digits, '.', '_' or '-'."
        require_running
        mkdir -p "$state"
        run_remote rehearse "$due_in" "$send_only" "$limit" "$label" 2>&1 | tee -a "$state/rehearse.log" ;;
    spike)
        # The launch-day spike check on a seeded deployment (developer guide,
        # "Checking the launch-day spike"): the VM half starts the shard
        # containers, collects their documents and prints the report.
        spike_families=200 sources=8 during_send=0 wait=20 label=spike
        while [ $# -gt 0 ]; do
            case "$1" in
                --families) [ $# -ge 2 ] || usage; spike_families=$2; shift 2 ;;
                --sources) [ $# -ge 2 ] || usage; sources=$2; shift 2 ;;
                --wait) [ $# -ge 2 ] || usage; wait=$2; shift 2 ;;
                --label) [ $# -ge 2 ] || usage; label=$2; shift 2 ;;
                --during-send) during_send=1; shift ;;
                *) usage ;;
            esac
        done
        if ! [[ $spike_families =~ ^[0-9]{1,4}$ ]] || [ "$((10#$spike_families))" -lt 1 ] || [ "$((10#$spike_families))" -gt 5000 ]; then
            refuse "--families must be a whole number from 1 to 5000."
        fi
        if ! [[ $sources =~ ^[0-9]{1,2}$ ]] || [ "$((10#$sources))" -lt 1 ] || [ "$((10#$sources))" -gt 32 ]; then
            refuse "--sources must be a whole number from 1 to 32."
        fi
        if ! [[ $wait =~ ^[0-9]{1,3}$ ]] || [ "$((10#$wait))" -lt 1 ] || [ "$((10#$wait))" -gt 240 ]; then
            refuse "--wait must be a whole number of minutes from 1 to 240."
        fi
        spike_families=$((10#$spike_families)) sources=$((10#$sources)) wait=$((10#$wait))
        [[ $label =~ ^[A-Za-z0-9._-]{1,40}$ ]] || refuse "--label must be 1-40 letters, digits, '.', '_' or '-'."
        require_running
        mkdir -p "$state"
        run_remote spike "$spike_families" "$sources" "$during_send" "$wait" "$label" 2>&1 | tee -a "$state/spike.log" ;;
    snapshot)
        # The two original forms keep overwriting their fixed names.
        case "$#:${1-}" in
            0:) save_snapshot post-setup 1 ;;
            1:--seeded) save_snapshot seeded 1 ;;
            2:save) check_snapshot_name "$2"; save_snapshot "$2" 0 ;;
            3:save)
                [ "$3" = --replace ] || usage
                check_snapshot_name "$2"
                save_snapshot "$2" 1 ;;
            1:list)
                require_running
                kb=$(mac_free_kb) || exit 1
                run_remote snapshot-list "$kb" ;;
            2:delete) delete_snapshot "$2" ;;
            2:restore) switch_snapshot "$2" ;;
            *) usage ;;
        esac ;;
    switch)
        [ $# -eq 1 ] || usage
        switch_snapshot "$1" ;;
    reset)
        # A flag of its own, so a snapshot named "reinstall" never means --reinstall.
        name=post-setup reinstall=0
        case "$#:${1-}" in
            0:) ;;
            1:--seeded) name=seeded ;;
            1:--reinstall) reinstall=1 ;;
            1:-*) usage ;;
            1:*) check_snapshot_name "$1"; name=$1 ;;
            *) usage ;;
        esac
        if [ "$reinstall" = 1 ]; then
            do_reinstall
            exit 0
        fi
        require_running
        if run_remote has-snapshot "$name"; then
            run_remote reset "$name"
        elif [ "$name" = seeded ]; then
            refuse "No seeded snapshot. Seed first, then: $0 snapshot --seeded"
        elif [ "$name" != post-setup ]; then
            refuse "No $name snapshot; '$0 snapshot list' lists them."
        else
            echo "No post-setup snapshot: the root will be removed and installed again."
            do_reinstall
        fi ;;
    seed)
        scale=1 testing=0
        case "$#:${1-}" in
            0:) ;;
            1:--testing) testing=1 ;;
            2:--response-scale) scale=${2-} ;;
            *) usage ;;
        esac
        require_running
        mkdir -p "$state"
        if [ "$testing" = 1 ]; then
            upload_host_script
            remote_env=$(users_env_prefix)
            run_remote seed-testing "$host_remote" 2>&1 | tee -a "$state/seed.log"
        else
            run_remote seed "$scale" 2>&1 | tee -a "$state/seed.log"
        fi ;;
    reseed)
        scale=1
        case "$#:${1-}" in
            0:) ;;
            2:--response-scale) scale=${2-} ;;
            *) usage ;;
        esac
        require_running
        run_remote has-snapshot post-setup || refuse "No post-setup snapshot to restore; run: $0 snapshot"
        run_remote reset post-setup
        mkdir -p "$state"
        run_remote seed "$scale" 2>&1 | tee -a "$state/seed.log" ;;
    wizard)
        [ $# -eq 0 ] || usage
        require_running
        run_remote wizard ;;
    status)
        [ $# -eq 0 ] || usage
        limactl list "$vm" 2>/dev/null || echo "No Lima instance '$vm'."
        if [ "$(vm_status)" = Running ]; then
            # status still reports without the Mac's figure.
            run_remote status "$(mac_free_kb 2>/dev/null || true)"
        fi ;;
    down)
        [ $# -eq 0 ] || usage
        require_running
        run_remote down ;;
    sign-in)
        if [ "$#:${1-}" != "2:--email" ] || [ -z "${2-}" ]; then usage; fi
        require_running
        run_remote sign-in "$2" ;;
    ca)
        [ $# -eq 0 ] || usage
        require_running
        mkdir -p "$state"
        # Written to a temporary name and checked before it replaces the
        # kept copy, so a refusal never leaves an empty certificate file.
        tmp=$(mktemp "$state/caddy-root.XXXXXX")
        if run_remote ca >"$tmp" && grep -q -- '-----BEGIN CERTIFICATE-----' "$tmp"; then
            mv "$tmp" "$state/caddy-root.crt"
        else
            rm -f "$tmp"
            refuse "Could not fetch Caddy's root certificate; is the deployment up?"
        fi
        echo "Saved Caddy's local root certificate to $state/caddy-root.crt"
        echo "To trust it in the login keychain (macOS), run:"
        echo "  security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db $state/caddy-root.crt"
        echo "Accepting the browser's certificate warning works too." ;;
    *) usage ;;
esac
