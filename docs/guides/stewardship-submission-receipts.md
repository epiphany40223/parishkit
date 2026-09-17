# Submission confirmation delivery

## Scope and dependencies

This Phase 4 increment begins on `pr/stewardship-submission-receipts` from
PR #45's verified merge `3bfec17a`. It delivers
[BG-07.01](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
as a coherent submission-to-delivery flow, including its receipt-specific
Testing, pause, retry, resolution and cleanup integration. Follow the
[confirmation contract](../specs/stewardship/background-processing/spec.md#submission-confirmation),
[final submission contract](../specs/stewardship/parishioner-portal/spec.md#review-and-submission),
and [content contract](../specs/stewardship/data/spec.md#content-and-email-templates).
Daily/weekly digest ownership, complete post-close inventory/resolution UI and
Gate 3 remain with their existing later owners; this increment does not claim
the full BG-07 package complete.

## Internal acceptance checkpoints

1. Deliver credential-free content, deterministic confirmation-template
   selection and atomic submission/outbox creation. No provider or broker I/O
   belongs inside final Submit. No deliverable head address means a non-error
   audit, not a blocked response or empty-recipient message.
2. Connect receipt dispatch to existing fenced task/provider journal ownership.
   Keep direct receipt identity independent of invitation/reminder schedules;
   preserve Testing routing, current-source checks, pause holds and truthful
   provider uncertainty without copying the SMTP attempt state machine.
3. Integrate current Admin metadata, evidence, explicit retry and duplicate-risk
   resolution. Preserve exact submission/epoch/root bindings, immutable attempt
   history and post-close admission without inventing successful fulfillment.
4. Verify fresh-install schema/model/grant equivalence, actual runtime-role
   adversarial tests, cleanup, submission rollback and race coverage. Retained
   development databases are neither upgraded nor deleted.
5. Complete full local validation, three successful dual-source review/fix
   rounds, exact-head CI/DCO, protected merge-group checks and fresh-main
   verification before advancing to the digest increment.

## Execution evidence

Implementation is in progress. The checkpoints above are acceptance targets,
not claims of completion. Task status remains unchecked until implementation
and its applicable verification pass.

The initial content checkpoint implements credential-free rendering with fixed
required facts and campaign-zone timestamps, a separately authored confirmation
block, singleton direct-mail template selection, and shared Testing routing.
It passes 231 focused pure tests, 50 PostgreSQL content/actual-role editing tests
in 55.56 seconds, and the full baseline: 6,141 passed, 4,233 explicit profile
skips and two existing warnings in 58.20 seconds. Ruff and changed Markdown pass.
That first checkpoint alone did not send receipts.

The integrated implementation now creates each concrete receipt, task root,
immutable allocation seed and delivery history inside final Submit's transaction. The
database independently checks the exact response, Family, current source,
configuration and Testing routing. A current-source no-recipient outcome keeps
the accepted submission and records the closed non-error audit reason. Proposed
contact changes and opt-outs do not change transactional receipt routing.
Web cannot supply receipt prose, either at Submit or through Admin retry. The
database-owned seed is explicitly ineligible for provider submission; MAIL
renders the actual template and public facts without access to response answers.
Template failures remain retryable delivery failures after the response commits.

Receipts use the existing fenced MAIL worker and commit-before-provider journal,
without loading Family access keys or reading response answers. They retain
distinct identities through pauses, remain admitted after actual campaign close,
and cannot send from an invalidated Testing epoch. Existing Admin delivery
metadata, uncertainty warnings, evidence and explicit retry actions include
receipts; they do not invent schedule occurrences or fulfillment. Cleanup of a
receipt-bearing response uses the journaled transition workflow, not the older
response-only helper that cannot drain outbox history.

Pre-review checks pass 56 PostgreSQL submission/cleanup/Ministry tests in 121.56
seconds, 39 receipt/Admin recovery tests in 95.37 seconds, and 45 worker,
submission and audit tests in 49.17 seconds. Actual MAIL credentials cannot read
answers; fake providers observe the committed attempt before I/O. Tests cover
partial refusal retries, unknown outcomes, abandonment, current-source routing,
atomic rollback, pause/resume and keyless Admin recovery. These focused runs
overlap and must not be summed as a distinct-test count. The pure baseline
passes 6,150 tests, 4,268 explicit profile skips and two existing warnings in
75.02 seconds. Final frozen-tree coverage and the review cycle remain open.

## Fresh-install schema audit

The predecessor's `3bfec17a` installer and this increment's installer were run
in separate newly created disposable databases. The predecessor exactly matches
its committed schema fingerprint. The audited delta adds two receipt columns,
four constraints, one index, seven functions and two triggers; it removes no
objects. Existing changes are limited to receipt disposition/closed audit
context checks, the singleton confirmation index, recipient projection and the
delivery, resolution, cleanup, archive and safe-context functions. Relations and policies
are unchanged. New command/binding functions retain restricted execution and
fixed search paths. The refreshed fingerprint passes all 17 schema/model tests
in 17.56 seconds. No retained database was deleted or upgraded.

The current baseline contains 162 relations, 1,871 columns, 2,683 constraints,
819 indexes, 433 functions, 435 triggers and 28 policies. This is a fresh-install
baseline, not an upgrade compatibility promise. Schema artifacts are included in
both Docker build allowlists.

## Review and correction evidence

Round 1 reviewed the complete `3bfec17a..e4ababef` diff in Pika session
`20260917-020013-74ba0f`. Both sources completed successfully, with no failed or
degraded agent or verdict mismatch. Raw severities were two High, two Medium
and nine Low; four findings met the configured validation cutoff.

- Codex High, Web-authored receipt bodies crossing into MAIL: accepted. Final
  Submit and Admin retry now accept only closed allocation commands; SQL owns
  the non-sendable seed. Actual-role adversarial tests reject private answer
  injection at both entry points and refuse direct submission of the seed.
- Claude High, rendering/configuration failures rolling back final Submit:
  accepted for rendering and runtime-origin coupling, removed by the allocation
  seed. Existing initial-setup validation requires an email integration and the
  canonical Testing recipient before portal readiness; incomplete low-level
  fixtures do not establish an optional-email product contract. Setup and web
  runtime prerequisite tests pass (27 tests). A worker-rendering failure test
  verifies the accepted response survives and no seed reaches the provider.
- Claude Medium, archived or superseded receipt holds: automatic cancellation
  is rejected because accepted receipts are post-close obligations, not expired
  invitations. Archive now rejects undelivered Production receipts. An actual
  lifecycle test permits archive only after acceptance. Full explicit skip
  inventory remains owned by BG-07.04; this increment cannot silently waive it.
- Claude Medium, source recipient disappearance: automatic cancellation is
  rejected for the same obligation contract. Tests promote both missing-email
  and inactive-head snapshots, then a correction; the same receipt resumes.
  Submit-time absence creates no obligation, unlike a later delivery hold.
- Claude Low: remove duplicated preparation, restrict the deferred binding
  trigger to receipts, centralize the Family display-name fallback, remove
  preview variable shadowing and an unnecessary lint suppression, and add
  boundary tests. Retain the system actor on the automatic no-recipient audit;
  the separate submission audit owns the Family actor, while Testing remains
  anonymized.

Pika's default finalization cleanup removed the raw Codex artifact containing
two additional below-cutoff Low findings. Their content is not recoverable from
the finalized result and is not represented as resolved. Subsequent rounds
retain raw artifacts. Round 1 post-fix validation passes 46 receipt/submission/
statistics PostgreSQL tests in 81.95 seconds, 49 schema/invitation/Admin recovery
tests in 81.20 seconds, the 27 setup/runtime tests, Ruff, formatting, changed
Markdown, diff whitespace checks and model-drift checks. The 49-test run includes
all 17 refreshed schema-baseline checks. The remaining two review rounds and
frozen-tree full coverage are still open.
