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
#   snapshot [--seeded]              save the root as the post-setup or seeded snapshot
#   reset [--seeded]                 restore that snapshot; with no post-setup
#                                    snapshot, wipe the root and run `up` again
#   reset --reinstall                build from this checkout, then wipe the root
#                                    and run `up` again, keeping the snapshots
#   seed [--response-scale M]        seed the campaign (the fake-clock seeder)
#   reseed [--response-scale M]      reset to the post-setup snapshot, then seed
#   rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]
#                                    add a Reminder due in MIN minutes (default 5),
#                                    measure its send and print the BG-12 report
#   wizard                           complete the setup wizard unattended
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
#   LIMA_HOME                      where Lima keeps its instances (default ~/.lima)
#
# This half runs under macOS's own bash 3.2; it needs only Lima
# (brew install lima), git, tar and ssh. The script never targets a host
# other than the Lima instance's ssh alias. It packs the checkout that
# contains the current directory, so a pull request is tested by running it
# from that pull request's checkout.

set -euo pipefail

vm=${PARISHKIT_LOCAL_VM:-parishkit-local}
admin=${PARISHKIT_LOCAL_ADMIN_EMAIL:-admin@example.test}
families=${PARISHKIT_LOCAL_FAMILIES:-100}
debug=${PARISHKIT_LOCAL_DEBUG_LOGGING:-1}
root=${PARISHKIT_LOCAL_ROOT:-/opt/parishkit}
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
    local args
    args=$(printf '%q ' "$root" "$project" "$yaml" "$envfile" "$build" "$snapshots" "$@")
    ssh_vm "f=\$(mktemp) && cat > \"\$f\" && sudo bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$remote"
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
        schema_change=0 rollback=0 bulk=keep latency=keep
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
            if [ "$bulk$latency" = keepkeep ]; then
                run_remote deploy "$tag" "$schema_change" "$host_remote"
            else
                run_remote deploy "$tag" "$schema_change" "$host_remote" "$bulk" "$latency"
            fi
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
    snapshot|reset)
        name=post-setup
        case "$command:$#:${1-}" in
            *:0:) ;;
            *:1:--seeded) name=seeded ;;
            reset:1:--reinstall) name=reinstall ;;
            *) usage ;;
        esac
        if [ "$name" = reinstall ]; then
            do_reinstall
            exit 0
        fi
        require_running
        if [ "$command" = snapshot ]; then
            run_remote snapshot "$name"
        elif run_remote has-snapshot "$name"; then
            run_remote reset "$name"
        elif [ "$name" = seeded ]; then
            refuse "No seeded snapshot. Seed first, then: $0 snapshot --seeded"
        else
            echo "No post-setup snapshot: the root will be removed and installed again."
            do_reinstall
        fi ;;
    seed)
        scale=1
        case "$#:${1-}" in
            0:) ;;
            2:--response-scale) scale=${2-} ;;
            *) usage ;;
        esac
        require_running
        mkdir -p "$state"
        run_remote seed "$scale" 2>&1 | tee -a "$state/seed.log" ;;
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
            run_remote status
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
