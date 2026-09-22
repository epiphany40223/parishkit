# Stewardship release image and image retargeting reviews

This ledger records the independent review/fix rounds of the
[release image increment](stewardship-release-image.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, since the increment touches the release workflow's registry
authority and the deployment's upgrade path. The Codex reviewer has been out
of quota since September 20, 2026; under the human's exemption, extended
through October 30, 2026, a completed Claude-only pass counts as a round, and
each round records which sources answered.

## Round 1

Claude only (Codex produced no output). Eleven raw findings, three
validated, all corrected:

- Medium: the equal-image branch returned before the three topologies were
  compared with the rendered documents, so a hand-edited topology was
  accepted when the recorded image was requested, and an interrupted
  retarget could only be finished, never undone. Every topology is now
  compared; one is admitted only when it is the same inputs rendered with
  the recorded image or with the one image it names instead, is brought to
  the requested image under the lease, and anything else is refused. Cases
  cover undoing an interrupted retarget with the recorded image and
  refusing a topology naming neither image, whichever image is asked.
- Medium: the operations specification, which the Compose guide now cites,
  still described checked-in production overlays and a multi-architecture,
  attested release build. It now says production Compose is rendered by the
  provisioner and records the v1 single-architecture release with its
  deferred remainder.
- Medium: both CLI cases failed on the missing-option check, so the
  dispatch was never exercised. A case now drives a provisioned root
  through the console command to the JSON result and asserts the generic
  message for a missing option, an unreadable configuration, a
  configuration whose raw error would name its contents and a refused
  image.

The eight findings the validation step did not confirm were not carried
forward.

## Round 2

Claude only (Codex produced no output). Thirteen raw findings, two
validated, both corrected:

- Medium: release tags are annotated, so `GITHUB_SHA` on a tag push may
  name the tag object rather than the commit it points to, and the image's
  commit tag would not have been the immutable commit tag the specification
  promises. The image job now resolves the commit as the validation job
  does and uses it for the second tag; the workflow test asserts the
  derivation and that `GITHUB_SHA` is not used.
- Medium: every retarget case ran under the development profile with the
  renderer patched, so the production admission path was never driven. A
  case now provisions a production root with one digest, moves it to
  another, asserts the Caddyfile and service documents are unchanged, and
  asserts a tag-form image and a development image are refused under the
  real renderer.

The eleven findings the validation step did not confirm were not carried
forward.
