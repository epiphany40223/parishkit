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

hex=${image#"${repo}@sha256:"}
if [ "$hex" = "$image" ] || ! [[ $hex =~ ^[0-9a-f]{64}$ ]]; then
    echo "STEWARDSHIP_IMAGE must be ${repo}@sha256:<64 lowercase hex>; refusing." >&2
    exit 1
fi
if [ "$mode" = rollback ] && [ "$schema_change" != 0 ]; then
    echo "A rollback never migrates; unset STEWARDSHIP_SCHEMA_CHANGE. Refusing." >&2
    echo "A release that changed the schema needs the runbook's database-restore rollback." >&2
    exit 1
fi

# As in the dev deploy tool: upload the host half
# (tools/stewardship-upgrade-host.sh) to a file and run it from there,
# because `docker compose run` would otherwise read the rest of the script
# from ssh's stdin.
args=$(printf '%q ' "$repo" "$image" "$root" "$project" "$yaml" "$uuid" "$schema_change" "$mode")
host_script=$(CDPATH='' cd "$(dirname "$0")" && pwd)/stewardship-upgrade-host.sh
[ -f "$host_script" ] || { echo "Missing $host_script; refusing." >&2; exit 1; }
# shellcheck disable=SC2029 # $args is meant to expand here, %q-quoted for the host
ssh "$host" "f=\$(mktemp) && cat > \"\$f\" && bash \"\$f\" $args; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$host_script"
