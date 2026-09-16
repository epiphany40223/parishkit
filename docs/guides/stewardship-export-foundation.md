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
Exhausted cleanup records one durable critical operational signal per failed run;
an Admin service can explicitly retry the same root after repairing the cause.
BG-10 owns notification transport; Phase 5 owns the additional report-job controls.
Download grants and uses are retained audit-adjacent parish security metadata,
not temporary report bytes. The exceptional purge owner controls their eventual
disposition; seven-day artifact cleanup does not erase access history.

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

## Review round 1

Pika session `20260916-013854-043719` reviewed full branch base `e1e575dd`
through `d16f18df2852c1c09b616bedef29a75c4489bef4`, tree
`8393d5d3b724a95e1428d9defa78d81adb298dea`. The exact-path Claude permission
preflight passed. Both manifest-generated Claude shards and Pika's single Codex
reviewer completed; finalize reported no failed/degraded sources, mismatch or
salvage. Raw severities were **1 High, 8 Medium and 27 Low**; eight findings
survived the configured confidence/severity filter. The tables retain raw
severities rather than treating filtering as disposition.

`C` denotes Codex; `A` and `B` denote Claude shards 1 and 2, respectively.
Numbers are one-based positions in each source's raw finding list.

| Finding | Severity | Disposition and evidence |
| --- | --- | --- |
| C1 | High | Fixed: include `exports.sql` in both Docker allowlists and build-context inventories; rebuild and configured Compose validation required. |
| C2 | Medium | Fixed: immutable root binds the requester, while retry actor can be Admin; explicit retry/replay service and Staff-request/Admin-retry worker regression. |
| C3 | Medium | Fixed: CSV carries overall pinned `input_source_as_of`, separately from per-day cutoffs; parity assertion. |
| A1 | Medium | Fixed: bounded cleanup exhaustion records CRITICAL atomically; Admin explicit same-root retry, replay and actual restricted-role crash/failure regressions. |
| A2 | Medium | Rejected: retained export requests must preserve their input generations under the controlling fact-retention contract. File expiry cannot release their pins. |
| A3 | Medium | Rejected for this bounded compiled renderer: the finite 60-second guard deliberately limits a slow filesystem/renderer before purge can drain. Scale tests render PNG and PDF for 366 days in 0.39 seconds and 3,653 days in 2.53 seconds locally; cold read-only image rendering also completed in under a second. Preserve fail-closed hard-stop and recovery tests rather than introduce a second, divergent lifetime policy. New report types must establish their own bounded query/render acceptance evidence. |
| B1 | Medium | Rejected even though filtered at confidence 38: `execution.effect()` enters `handler.scope=work_transaction` before eligibility/unlink/receipt. It already holds advisory key `(736220,1)` for the complete destructive effect; publication cannot interleave. |
| B2 | Medium | Fixed: actual HTTP CSV/PNG/PDF streaming uses distinct restricted web and download logins and executes the real session/revocation callback. No privileges broadened. |
| B3 | Medium | Fixed after reproducing missing aliases: SQL validates through the existing pinned alias resolver; exhaustive catalog/Python/PostgreSQL compatibility test and actual `US/Eastern` worker regression. |

| Finding | Severity | Disposition and evidence |
| --- | --- | --- |
| A4 | Low | Fixed N+1 with `select_related`; broader historical scan optimization deferred with B6 to report-volume tuning, not claimed solved by a query-order change. |
| A5 | Low | Rejected: verifying the entire bounded artifact before sending bytes intentionally prevents disclosure of corrupt/substituted content. |
| A6 | Low | Fixed: remove unused single-file remover; tests exercise actual attempt cleanup. |
| A7 | Low | Fixed with C2/A1: separate Admin retry actor, explicit same-root cleanup retry. |
| A8 | Low | Deferred performance tuning: global work order is required; exclusive drain waits at most five seconds, then retries without unlinking. Active-reader regression verifies it. |
| A9 | Low | Fixed: oversized artifact writes raise the owning `ConfigError`; regression follows the real failure contract. |
| A10 | Low | Retained deliberately: one empty private directory per campaign is harmless; exceptional campaign purge owns directory removal, not artifact expiry. |
| A11 | Low | Fixed: column grants compose additively. |
| A12 | Low | Fixed: scheduler no longer receives worker-only extra PortalUser columns. |
| A13 | Low | Added exhausted cleanup recovery/failure and retry coverage; existing crashed-render cancellation test already exercises real recovery dispatch. |
| B4 | Low | Fixed: allow Django's conventional CSRF body field while rejecting duplicate/unknown business fields; form regression. |
| B5 | Low | Fixed: malformed cancellation is 400; completed-export conflict uses a specific exception and 409. |
| B6 | Low | Deferred with A4: immutable historical scan performance deserves measured tuning; existing indexed TaskRun anti-join already excludes every scheduled cleanup root. |
| B7 | Low | Rejected: the common work-order lock coordinates lifecycle/purge admission across owners. A per-export replacement would violate established lock ordering. |
| B8 | Low | Fixed: immutable SQL guard pins search path consistently. |
| B9 | Low | Fixed: quote complete generated table identifiers, not suffixes. |
| B10 | Low | Fixed: remove redundant capability test after `_principal`. |
| B11 | Low | Retained clear closure/sentinel pattern; no functional issue and changing it would add another state variable. |
| B12 | Low | Rejected formatter-only suggestion; Ruff owns wrapping, and invalid-scope tests cover membership semantics. |
| B13 | Low | Deferred negligible shallow-copy optimization; full annual/multi-year rendering measurements now exist. |
| B14 | Low | Fixed: hoist constant CSV metadata conversion out of the row loop. |
| B15 | Low | Fixed: immutable timestamp and visible label say `requested_at`/Requested; PDF dates describe logical document origin, not retry time. |
| B16 | Low | Documented retained audit-adjacent grant/use history above; no unapproved retention sweep. |
| B17 | Low | Fixed: actual HTTP format/header/byte validation covers CSV, PNG and PDF. |
| B18 | Low | Deferred richer denied-redemption audit to Phase 5 report-access UX/security integration; current uniform denial remains, and started/finished/failed streams are audited. |
| B19 | Low | Documented: this compiled participation report requires parish-wide `CAMPAIGN_REPORT`; generic/ministry export permission composition belongs to Phase 5 and must not broaden this endpoint. |
| C4 | Low | Artifact limitation: default Pika finalization removed Codex raw output after retaining its three validated findings. The fourth raw Low count survives, but its body does not; no invented disposition. Subsequent rounds retain artifacts explicitly. |

The first full validation attempt exposed one real fresh-schema deparse mismatch,
now corrected without weakening the model/schema comparison. The disposable
PostgreSQL service for one shard also suffered an OOM crash; its replacement
completed all 743 tests. These partial results are not final combined coverage.
Final integrated validation must use one frozen source tree and successful
receipts from every shard. Retained development databases were not modified.
