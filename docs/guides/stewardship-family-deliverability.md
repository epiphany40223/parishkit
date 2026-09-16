# Stewardship Family deliverability increment

## Scope and checkpoints

Branch `pr/stewardship-family-deliverability` starts at verified PR #36 merge
`7d9a3b0b819e384739e53ed11a53fa541af51282` on `origin/main`. It continues
[Phase 4](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery)
with [BG-06](../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders).

The coherent outcome is current Family recipient evaluation and durable
deliverability-recovery preparation, integrated with source promotion and the
bounded scheduler. Shared recipient projection, durable suppression ownership,
recovery allocation and their restricted-role/concurrency regressions are
internal checkpoints, not separate PRs. Source eligibility remains distinct
from provider deliverability. Recovery attempts retain the initial invitation's
semantic fulfillment slot; an ambiguous provider outcome is never permission
to send another initial invitation.

The controlling behavior is the
[Family invitations contract](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders).
Personalized rendering, isolated provider dispatch, ambiguous-outcome resolution,
the Admin verified-refusal-clear workflow, and the remaining pause/resume
delivery integration continue in the next BG-06
increment. Preparation grants no provider permission, enables no Production
activation, and does not release Gate 3. Full BG-06 checkboxes remain open until
their complete acceptance criteria pass.

## Work in progress

The shared source recipient projection now supplies both identity status and
sorted, deduplicated eligible/deliverable head addresses. Its focused unit tests
cover publication flags, non-head Family addresses, suppression, inactive and
non-parishioner Families, immutable inputs and private-value error handling.
The current focused checks pass: 145 unit tests, 41 PostgreSQL schedule/recovery/
activation tests, and 13 restricted-source/refusal tests. Refusals now update
deliverability immediately, remain Family-scoped, and resolve atomically when
the source address changes. Merely inactivating a Member does not clear a
refusal. Recovery references immutable eligibility-history versions, preserves
the initial semantic slot, and works under both scheduler and activation-worker
ownership. These are internal checkpoints: complete validation and the three
dual-source review/fix rounds remain open.

## Fresh-install schema audit

Two new empty disposable databases compare the exact PR #36 baseline with this
increment. The reference fingerprint matches the committed fixture before the
delta is inspected. No retained development database is changed or deleted.

The intentional delta adds two append-only refusal/resolution tables, eighteen
columns, twenty-seven constraints, eight indexes, six functions and five
triggers. The existing occurrence semantic-revision constraint and backing
index include the recovery generation; semantic fulfillment remains unchanged.
Three existing guards change: occurrence insertion validates recovery evidence,
immutable identity includes the generation, and activation preparation accepts
its canonical generation-bound key. No existing columns, relation definitions,
ACLs, triggers or row policies change; no objects are removed.

The audited current totals are 154 relations, 1,781 columns, 2,543 constraints,
780 indexes, 390 functions, 393 triggers and 28 row policies. The strict
fingerprint is updated only after that exact object-level comparison. Django
reports no model-state drift, and model-versus-installed-schema checks pass.

## Round 1 review and corrections

Pika session `20260916-043923-a7b7e0` reviewed base
`7d9a3b0b819e384739e53ed11a53fa541af51282` through head
`38e21733c4ef2ff51137b9a3052bef1139d429e8`, tree
`9f9ee0117c13f0ffe648fa51bff989df06ba0eb5`. Both reviewers completed normally;
there were no degraded sources, failed agents, mismatch or salvage. All 29
manifest files were covered. Finalize returned COMMENT with three validated
findings. The complete raw inventory is twelve findings: five Medium and seven
Low, with no High or Critical. Filtered findings are also dispositioned below.

| Source / finding | Disposition |
| --- | --- |
| Claude M1: partial recipient refusal | Assigned to the existing BG-06.05 dispatch owner; regression proves another head remains deliverable. See the explicit dispatch prerequisite below. |
| Claude M2: refusal lost across campaigns | Fixed stable organization/DUID suppression, retaining original Family/event provenance; real archive/new-campaign regression. |
| Claude M3: timestamp order | Rejected for the compiled owners: they acquire work order before mutation statements, not inside a waiting mutation; terminal metadata-only updates are forbidden. Existing unresolved-attempt tests prove earlier edges cannot authorize resend. The owner invariant is now documented in code. |
| Claude M4: refusal write privileges unproven | Added exact WEB, WORKER, SCHEDULER and MAIL_DISPATCH denial tests. No current role receives the future dispatch write capability prematurely. |
| Claude L1: SQL/Python predecessor mismatch | Fixed SQL to use the current revision and highest recovery generation, preserving cross-revision unresolved-sibling blocking. |
| Claude L2: optimistic update count ignored | Fixed affected-row assertion; regression proves a lost version rolls back fulfillment as well as occurrence allocation. |
| Claude L3: intended/routed checks differ | Fixed the Python intended-address check and added a mismatched-routing regression. |
| Claude L4: per-refusal presence queries | Fixed one annotated presence query and bounded resolution batches. |
| Claude L5: nonstandard immutable-field check | Fixed the standard quoted immutable-field OR chain. |
| Claude L6: stale callback name | Renamed to `source_refusal_suppressions`, including caller tests. |
| Codex M1: recovery resurrects closed skips | Fixed Python and SQL authorization to accept only eligible recovery reasons; actual close/reopen/source-edge regression also rejects forged SQL recovery. |
| Codex L1: per-refusal queries/inserts | Duplicate of Claude L4; covered by its bounded-query correction. |

The first complete eight-shard run exposed two additional schema-inventory
failures: the occurrence immutable-field shape above, and combined insertion/
immutability guards on the new tables. The latter now uses separately named
insertion proofs and the standard unconditional immutable UPDATE/DELETE guard,
with explicit inventory ownership. Neither inventory assertion was weakened.
That run had 3,021 passing and two failing tests and is not passing evidence;
the corrected frozen head must pass a fresh complete run.

Post-correction targeted validation passes 76 PostgreSQL refusal/source-role/
schema-inventory tests and ten schedule-recovery tests. The annual rollover uses
real close/archive/Testing/new-draft operations, retaining both campaigns and
the original refusal. Exact-role source correction remains atomic with source
promotion. Ruff, formatting, tracked Markdown and Django model-state checks
also pass. The full baseline rerun passes 5,756 tests (3,840 explicitly skipped,
two warnings) in 57.74 seconds. The remaining review rounds and fresh complete
PostgreSQL validation are still required.

### Partial-refusal dispatch prerequisite

If one head address is refused while another remains usable, there is no Family
false-to-true deliverability transition. BG-06.05 must retain the initial semantic
slot and continue only definitively unaccepted work with the remaining eligible
recipients, through the existing outbox retry/re-render ownership. It must not
fabricate a Family boolean edge, resend to an accepted recipient, or treat an
ambiguous outcome as failure. The next dispatch increment must test that path,
its exact-role refusal/effect grants and provider outcome handling before any
Production activation. This preparation still exposes no provider dispatch.
