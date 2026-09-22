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
`ghcr.io/<owner>/<repository>/parishkit:<version>` and `:<commit>` (the
commit the annotated tag points to), and
records the pushed digest in the GitHub Release, so the operator copies the
exact `ghcr.io/…/parishkit@sha256:…` reference into the deployment YAML. The
human still pushes every release tag. A new command,
`pk-stewardship retarget-image --config CONFIG --image IMAGE`, points a
provisioned deployment at a newer approved image, re-rendering its generated
documents and nothing else.
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

### Retargeting re-renders the generated documents

Provisioning is create-only and records its inputs and image in a completion
marker; a completed root refuses another run. Retargeting re-derives every
generated document, the three rendered Compose topologies, the per-service
configurations and the ingress document, from the recorded inputs with the
new image, by the code that is running, and refuses unless the recorded
deployment inputs are unchanged, the passwords and broker ACL are present
and private, and the image is one the profile admits. Documents that already
match are left alone and the rest are rewritten, so a release whose renderer
changed reaches a deployment through the same command as one that only
changed the image; generated documents are never edited by hand, so a stray
edit is simply replaced. It holds the startup interlock exclusively, so no
online service can observe a half-written document, writes the documents and
then the marker, and changes the marker only when the image changes. So
running the same command again finishes an interrupted retarget, running it
with the recorded image undoes one, and a repeat is no change. It starts
nothing and connects to nothing: migrations, grants and service restarts
stay the operator's separate upgrade steps. (The first delivery of this
command changed only the topologies and refused any other differing
document; the [v1 backup increment](stewardship-backup.md) widened it as the
deployment runbook required.)

### The image carries the matching PostgreSQL client

The [v1 backup](stewardship-backup.md) dumps the database from inside the
application image, so the image installs `postgresql-client-18` from the
PostgreSQL project's repository, pinned to the exact build that matches the
server image's `postgres:18.6` digest in `runtime_topology.py`. That
repository keeps only the newest build of each major version: when 18.7, or a
rebuild of 18.6, is published, the pinned build disappears and every image
build, the release workflow's included, fails with "version not found" until
the pin is moved. That failure is expected, not a defect. Bump the client pin
and the server digest together, in one change, so `pg_dump` never runs against
a newer server than itself.

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
  its contents and a refused image with one generic message; a production
  root moves from one digest of the repository's image to another with its
  Caddyfile and service documents unchanged and refuses a tag-form or
  development image under the real renderer; the release workflow's image
  job depends on validation, holds only the package scope, tags the
  lowercase repository path with the version and the commit the annotated
  tag points to, and the release notes carry the digest.
- The Compose contract, build contract, provisioning and release workflow
  suites pass with the scaffold removed; the development Compose merge and
  the pinned Caddy template validation pass under the opt-in Compose checks.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation, focused validation and the
[review rounds](stewardship-release-image-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment; the first image publication happens only when the human pushes a
release tag.

## Protected delivery

PR #92 delivered candidate `d95a3d29`, three logical commits plus the PR #91
receipt, whose tree `063ca959` is identical to the retained commit-by-commit
review history on `pr/stewardship-release-image-reviewed` (`c73fefe7`) and to
the landed tree. The five
[review/fix rounds](stewardship-release-image-reviews.md), three full rounds
and two correction checks, were single-source under the exemption, with
every accepted finding fixed and the last check validating nothing. The pull
request was marked ready before the candidate was pushed, and the candidate
was pushed once the ready-for-review run for the previous head was in
progress, so that run was cancelled by the candidate's own. Exact-head
ready-candidate CI `35686468593` and DCO passed all 25 checks, from 04:19:48
to 04:38:39 UTC on September 22, 2026 (18 minutes 51 seconds). `origin/main`
had no intervening commits since the candidate's base `09c5b4ac`. Protected
auto-merge landed as `f29f9ef1` at 04:38:57 UTC and was verified on freshly
fetched `origin/main`, whose second parent's tree is the candidate's, before
the next increment started. This used the standing delivery authority,
without deployment or release: no release tag was pushed and no image was
published. The cancelled runs are not counted as acceptance.

The release image and image retargeting increment is delivered. The
production deployment item continues with the
[deployment runbook](stewardship-deployment-runbook.md).
