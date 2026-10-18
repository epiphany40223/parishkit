#!/usr/bin/env bash
# Upgrade a stewardship deployment to a published release digest, following
# the deployment runbook's Upgrade steps 1-6 one for one
# (docs/guides/stewardship-deployment-runbook.md#upgrade), or with
# --rollback move it back to a previous release digest following the
# runbook's Rollback section. Unlike tools/stewardship-dev-deploy.sh it
# builds nothing, accepts only a release digest, requires a completed backup
# with its off-host copy before web stops, and runs on a deployment in
# Production as well as in Testing.
#
# Both modes carry the deployment's current bulk Family send switch and
# batch size over into the re-rendered documents, so neither silently turns
# the bulk send off, and both start every application service with debug
# logging off. Each step prints a UTC timestamp and how long the previous
# step took; the whole log is also kept on the host under /var/log.
#
# A rollback is image-only: it renders the previous image's upgrade check
# and refuses, before anything stops, unless the check proves that the
# schema and every runtime grant already match that image. Anything else
# (a release that migrated or added a grant) needs the runbook's
# database-restore rollback, which this script never attempts. The previous
# release's static tree comes from the copy an upgrade kept at
# cache/static.<digest>, or is collected again in the previous image.
#
# Usage: tools/stewardship-upgrade.sh [--rollback]
#
# Configuration (environment variables):
#   STEWARDSHIP_HOST        ssh destination (required)
#   STEWARDSHIP_UUID        deployment UUID (required)
#   STEWARDSHIP_IMAGE       release image to move to,
#                           ${STEWARDSHIP_IMAGE_REPO}@sha256:<hex> (required);
#                           with --rollback, the previous release's digest
#   STEWARDSHIP_ROOT        runtime root (default /opt/parishkit)
#   STEWARDSHIP_PROJECT     Compose project name (default stewardship)
#   STEWARDSHIP_YAML        deployment YAML on the host
#                           (default /etc/parishkit/stewardship-deployment.yaml)
#   STEWARDSHIP_IMAGE_REPO  image repository
#                           (default ghcr.io/epiphany40223/parishkit/stewardship)
#   STEWARDSHIP_SCHEMA_CHANGE  1 when the release notes announce a schema or
#                           grant change; otherwise the upgrade refuses,
#                           before anything stops, unless the advisory
#                           upgrade check proves migration and grants would
#                           change nothing. Refused with --rollback: a
#                           rollback never migrates.
#   STEWARDSHIP_USERS_OPERATION, STEWARDSHIP_USERS_OPERATOR,
#   STEWARDSHIP_USERS_REASON, STEWARDSHIP_USERS_ADMINISTRATORS
#                           all four, only for the release whose notes
#                           announce the users upgrade (#952): a new
#                           operation UUID, who runs it and why, and the
#                           comma-separated Administrators its preview
#                           lists. Step 4 then converts the sign-in rules
#                           between the release's two migrations (host
#                           half's header). Refused with --rollback.
#   STEWARDSHIP_PRUNE       on (default), dry-run or off: after a successful
#                           run, remove old release images from the host
#                           with tools/stewardship-prune-images.sh (#1661),
#                           keeping the started and the replaced release;
#                           dry-run only lists what it would remove. A
#                           pruning failure is a warning, never a failure.
#   STEWARDSHIP_KEEP_RELEASES  how many newest other release images the
#                           pruning keeps besides those (1-99, default 4)
#   STEWARDSHIP_KEEP_BUILD_CACHE  1 to leave Docker's build cache alone

set -euo pipefail

mode=upgrade
case "$#:${1-}" in
    0:) ;;
    1:--rollback) mode=rollback ;;
    *) echo "usage: $0 [--rollback]" >&2; exit 2 ;;
esac

host=${STEWARDSHIP_HOST:?set STEWARDSHIP_HOST to the ssh destination}
uuid=${STEWARDSHIP_UUID:?set STEWARDSHIP_UUID to the deployment UUID}
image=${STEWARDSHIP_IMAGE:?set STEWARDSHIP_IMAGE to the release digest}
root=${STEWARDSHIP_ROOT:-/opt/parishkit}
project=${STEWARDSHIP_PROJECT:-stewardship}
yaml=${STEWARDSHIP_YAML:-/etc/parishkit/stewardship-deployment.yaml}
repo=${STEWARDSHIP_IMAGE_REPO:-ghcr.io/epiphany40223/parishkit/stewardship}
schema_change=${STEWARDSHIP_SCHEMA_CHANGE:-0}
prune=${STEWARDSHIP_PRUNE:-on}
keep=${STEWARDSHIP_KEEP_RELEASES:-4}

hex=${image#"${repo}@sha256:"}
if [ "$hex" = "$image" ] || ! [[ $hex =~ ^[0-9a-f]{64}$ ]]; then
    echo "STEWARDSHIP_IMAGE must be ${repo}@sha256:<64 lowercase hex>; refusing." >&2
    exit 1
fi
case "$prune" in on | dry-run | off) ;; *)
    echo "STEWARDSHIP_PRUNE must be on, dry-run or off; refusing." >&2; exit 1 ;;
esac
[[ $keep =~ ^[1-9][0-9]?$ ]] || { echo "STEWARDSHIP_KEEP_RELEASES must be 1-99; refusing." >&2; exit 1; }
if [ "$mode" = rollback ] && [ "$schema_change" != 0 ]; then
    echo "A rollback never migrates; unset STEWARDSHIP_SCHEMA_CHANGE. Refusing." >&2
    echo "A release that changed the schema needs the runbook's database-restore rollback." >&2
    exit 1
fi

# The users upgrade's inputs reach the host half as an environment prefix
# (ssh forwards no variables), and only when set, so every other run's
# command line is exactly what it was. The host half validates them.
users_env=""
for name in STEWARDSHIP_USERS_OPERATION STEWARDSHIP_USERS_OPERATOR \
    STEWARDSHIP_USERS_REASON STEWARDSHIP_USERS_ADMINISTRATORS; do
    [ -z "${!name-}" ] || users_env+="$name=$(printf '%q' "${!name}") "
done
if [ -n "$users_env" ] && [ "$mode" = rollback ]; then
    echo "A rollback never converts; unset the STEWARDSHIP_USERS_* variables. Refusing." >&2
    exit 1
fi
[ -z "$users_env" ] || users_env="env $users_env"

# As in the dev deploy tool: upload the host half
# (tools/stewardship-upgrade-host.sh) to a file and run it from there,
# because `docker compose run` would otherwise read the rest of the script
# from ssh's stdin.
args=$(printf '%q ' "$repo" "$image" "$root" "$project" "$yaml" "$uuid" "$schema_change" "$mode")
host_script=$(CDPATH='' cd "$(dirname "$0")" && pwd)/stewardship-upgrade-host.sh
[ -f "$host_script" ] || { echo "Missing $host_script; refusing." >&2; exit 1; }
prune_script=${host_script%/*}/stewardship-prune-images.sh
[ "$prune" = off ] || [ -f "$prune_script" ] || { echo "Missing $prune_script; refusing." >&2; exit 1; }
# The host half's output is also kept here, so the pruning below can read
# which release it replaced (its last line) and keep that rollback target.
transcript=$(mktemp)
trap 'rm -f "$transcript"' EXIT
set +e
# shellcheck disable=SC2029 # $args is meant to expand here, %q-quoted for the host
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && ${users_env}bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$host_script" |
    tee "$transcript"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -eq 0 ] || exit "$rc"
[ "$prune" != off ] || exit 0

# Prune old release images only after a run that succeeded (#1661). The
# protected images are the release just started and the replaced one; the
# prune script also keeps every image a container uses and the newest
# STEWARDSHIP_KEEP_RELEASES others. Its failure never fails the upgrade.
previous=$(sed -n "s/^    Record in the operators' notes\. Replaced release: //p" "$transcript" | tail -1)
prune_args=(--repo "$repo" --keep "$keep" --protect "$image")
[ -z "$previous" ] || prune_args+=(--protect "$previous")
[ "$prune" = on ] || prune_args+=(--dry-run)
[ "${STEWARDSHIP_KEEP_BUILD_CACHE:-0}" = 0 ] || prune_args+=(--keep-build-cache)
args=$(printf '%q ' "${prune_args[@]}")
# shellcheck disable=SC2029 # as above
if ! ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$prune_script"; then
    echo "WARNING: pruning old release images failed (see above); the $mode itself succeeded." >&2
fi
