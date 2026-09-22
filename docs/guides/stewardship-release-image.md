# Stewardship release image and image retargeting

This guide records the first production-deployment slice of the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
(item 3): the release workflow publishes the immutable application image the
[operational runtime](stewardship-runtime.md) requires, a provisioned
deployment can be pointed at a newer image, and the pre-production Compose
scaffold that could never start is retired. It contributes to OPS-01.02 and
supplies the single-architecture tagged image the launch scope substitutes
for the [cut](../plans/stewardship/v1-launch.md#cut-from-v1) OPS-09.06, under
the [operations specification](../specs/stewardship/operations/spec.md#compose-files-and-images),
and follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

Pushing a release tag now builds the single-architecture `linux/amd64`
application image from the tagged commit, pushes it to GHCR as
`ghcr.io/<owner>/<repository>/parishkit:<version>` and `:<commit>`, and
records the pushed digest in the GitHub Release, so the operator copies the
exact `ghcr.io/…/parishkit@sha256:…` reference into the deployment YAML. The
human still pushes every release tag. A new command,
`pk-stewardship retarget-image --config CONFIG --image IMAGE`, points a
provisioned deployment at a newer approved image and changes nothing else.
The `compose.production.yaml` scaffold, whose service commands refused to
start, is removed in favour of the provisioner's rendered topologies.

Multi-architecture images, SBOM and provenance attestations (OPS-09.03's
release extras) and key rotation are cut from v1 by the launch scope. The
deployment runbook is the next slice.

## Design

### The image is published by the release tag, and only then

The release workflow already refuses a tag that is not on `main` or whose
version does not match `pyproject.toml`, then runs the full validation before
publishing distributions. The image job runs after that validation on the
same tagged commit, with the workflow's own registry permission and the
repository's package scope; no long-lived registry credential is stored. The
digest, not a tag, is what the runtime admits: the release notes carry it, so
the value the operator deploys is the value the workflow pushed.

### Retargeting changes the image and nothing else

Provisioning is create-only and records its inputs and image in a completion
marker; a completed root refuses another run. The only artifacts that name
the image are the three rendered Compose topologies and that marker.
Retargeting re-derives every document from the operator's current inputs
with the new image and refuses unless the recorded deployment inputs are
unchanged, every other generated document is byte-identical on disk, the
passwords and broker ACL are present and private, and the image is one the
profile admits. A topology itself is admitted only in the two states an
interrupted retarget can leave, the same inputs rendered with the recorded
image or with the one image it names instead; anything else is a hand edit
and is refused. It holds the startup interlock exclusively, so no online
service can observe a half-written topology, writes the topologies and then
the marker, and changes the marker only when the image changes. So running
the same command again finishes an interrupted retarget, running it with
the recorded image undoes one, and a repeat is no change. It starts nothing
and connects to nothing: migrations, grants and service restarts stay the
operator's separate upgrade steps.

### The scaffold is gone, not fixed

The pre-production `compose.production.yaml` described services whose
`service` commands refuse every production role and hid Caddy behind a
pending profile although its hardening is complete. The provisioner has
rendered the complete production topology since Phase 1C, with every online
service on the operational runtime command and Caddy without a profile, so
the scaffold and its tests are removed and the Compose guide says where
production Compose really comes from.

## Schema

No schema change.

## Focused validation

- Database-free: retargeting changes the three topologies and the record to
  the new image while every other document and password stays; a repeat
  changes nothing and provisioning still refuses the completed root; an
  interrupted retarget, topologies written but record not, is finished by
  repeating it and undone by asking for the recorded image; a topology
  naming neither image is refused whichever image is asked; a changed
  deployment input, a hand-edited document, an unapproved image and an
  online service holding the interlock are refused with nothing written; an
  unfinished provisioning and a root never provisioned are refused; the
  console command reports the change as JSON and answers a missing option,
  an unreadable configuration, a configuration whose raw error would name
  its contents and a refused image with one generic message; the release
  workflow's image job depends on validation, holds only the package scope
  and tags the lowercase repository path.
- The Compose contract, build contract, provisioning and release workflow
  suites pass with the scaffold removed; the development Compose merge and
  the pinned Caddy template validation pass under the opt-in Compose checks.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete and the first of three
[review rounds](stewardship-release-image-reviews.md) is corrected; the
remaining rounds, full exact-head CI, DCO and protected delivery remain
open. No deployment,
release, live-provider write or database deletion is authorized by this
increment; the first image publication happens only when the human pushes a
release tag.
