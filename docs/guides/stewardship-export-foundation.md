# Stewardship export and chart foundation

Branch `pr/stewardship-export-foundation` starts from verified PR #35 merge
`e1e575ddb4971de9bfd5247a54dd2d95180af28f` on September 16, 2026 UTC. See the
[preceding protected-delivery evidence](stewardship-activation-catchup.md#protected-delivery).

## Scope and controlling contracts

Begin [BG-08](../tasks/stewardship/background-processing.md#bg-08-export-and-graph-workers)
in the order required by the [Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).
This increment supplies the authorized export-job substrate and shared
deterministic participation rendering needed by later delivery/report owners.
The controlling contracts are [export workers](../specs/stewardship/background-processing/spec.md#exports-and-graph-rendering),
[shared report behavior](../specs/stewardship/reports/spec.md#shared-report-behavior),
[fact materialization](../specs/stewardship/reports/spec.md#participation-fact-materialization),
[read guards](../specs/stewardship/data/spec.md#campaign-read-guards), and
[artifact retention](../specs/stewardship/operations/spec.md#temporary-retention-and-housekeeping).

Do not duplicate calculations in a renderer. The first compiled report consumer
uses a single ready participation fact generation. It must reject unavailable
exact inputs instead of substituting the interactive pointer. RPT-03 retains
fact calculation/materialization and its priority-build integration; Phase 5
retains the complete report catalog, interactive screens and export workflows.
Existing Admin-only operational job endpoints must not become Staff endpoints.

## Implementation checkpoints

1. Add immutable render inputs, exact table/CSV values and deterministic PNG/PDF
   rendering with unavailable/empty states, scope, as-of metadata and distinct
   count/dollar axes. Test parity and repeated rendering without live providers.
2. Add requester-scoped durable requests and canonical general-worker tasks,
   pinned inputs and fresh policy/admission at each protected operation. Preserve
   cancellation, retry ownership and fact pins independently of artifact expiry.
3. Add owner-only atomic artifact storage, bounded cleanup and application-only
   downloads using the existing dedicated pool and response-lifetime read guard.
4. Verify actual restricted-role behavior, cross-requester denial, revocation,
   crash/retry, cancellation and purge races. Audit only fresh-install schema
   differences in new disposable databases; never delete retained databases.
5. Run local validation and three completed dual-source review/fix rounds, then
   require exact-head and protected merge-group CI before merging and continuing.

These are internal implementation steps, not requests for routine human approval.
No formal gate, deployment, release or production-readiness boundary is released
by this increment.

## Implemented substrate

The first compiled consumer accepts only CSV, PNG or PDF participation exports
of one ready fact generation. Immutable requests bind the requester, campaign,
configuration, timezone and closed parameters. Each fenced render attempt owns
an opaque private file; publication follows fresh authorization and cancellation
checks. Requests pin their calculations independently of seven-day file expiry.

Admin and Staff use requester-scoped endpoints, not the operational job portal.
Staff cannot read another requester's job; Admin can. Ministry-only roles cannot
request this parish-wide report. Downloads require the current Google session,
current policy, a 60-second single-use grant, a dedicated pool slot and a
response-lifetime campaign read guard. No proxy redirect exposes a file path.

The compiled cleanup worker drains readers before removing only a recorded
attempt's expired/crashed files. A five-second drain timeout schedules bounded
retry, not deletion. Requests, publications, cleanup receipts and fact pins remain
retained. A renderer exceeding its read-guard deadline hard-stops the isolated
solo worker; Compose restart and lease recovery preserve the unfinished outcome.

PNG/PDF share immutable exact inputs with CSV/table values. Matplotlib 3.11.2
uses fixed fonts/style, separate count/dollar axes and deterministic metadata;
unavailable observations stay gaps. Rendering is byte-repeatable within the
pinned runtime, not across library/font changes. The read-only non-root image
can build its non-sensitive font cache on its existing temporary filesystem.

## Validation before review

Focused PostgreSQL storage/read-guard/export tests passed (75 tests), restricted
authorization and TaskRun regressions passed (76 tests), runtime assembly tests
passed (181 tests), and rendering/artifact tests passed (50 tests). Additional
cleanup races and final integrated validation follow before acceptance.

Fresh-schema comparison against merged `e1e575dd`, performed in separately
created disposable databases, adds seven tables, 57 columns, 91 constraints,
31 indexes, 11 functions and 15 triggers. No existing object or policy changed.
The baseline fingerprint records that inspected fresh-install result. No upgrade
path was introduced and no retained development database was modified.

Full local validation, three dual-source review/fix rounds and protected delivery
are still pending. BG-08 remains in progress; the complete report catalog,
priority materialization, interactive screens and ministry-scoped export owners
remain assigned to their later tasks.
