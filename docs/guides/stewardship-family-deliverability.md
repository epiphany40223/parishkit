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

## Implemented preparation

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
ownership. The chronological validation and three dual-source review/fix rounds
are recorded below. Full BG-06 dispatch and activation remain separate work.

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

## Round 2 review and corrections

Pika session `20260916-050420-4cf3e7` reviewed the correction delta from
`38e21733c4ef2ff51137b9a3052bef1139d429e8` to
`80416f2b1dc91cd54500198b1cce2c03cd428d4d`, tree
`f1d8256df7fea8453f96c921672df03866691708`, with surrounding owner/spec context.
Both sources completed successfully, with no degradation, mismatch or salvage.
Claude covered all sixteen manifest files; Codex approved with no findings.
Raw Claude findings were one Medium and six Low; finalize validated the Medium.

| Source / finding | Disposition |
| --- | --- |
| Claude M1: shared-address isolation test | Added two active Families sharing an address; refusal affects only its Family, and correcting that Family resolves it even while the other retains the address. |
| Claude L1: late event/current Family effect test | Extended real annual rollover to record an old event before successor population and after it; the former is applied at promotion, the latter immediately affects only the successor. |
| Claude L2: resolution and mutation privileges | Extended exact-role checks across both tables, INSERT/UPDATE/DELETE and column UPDATE. Only source WORKER may append resolution; real promotion tests prove that permitted path. |
| Claude L3: immutable guard search path | Added the standard fixed search path; re-audited the fresh-install fingerprint. |
| Claude L4: actor evidence mismatch | Added the matching Python actor check and documented retained attribution; fresh/replay mismatched actors are rejected before insertion. |
| Claude L5: three identity lookup queries | Retained explicit bounded identity lookups: snapshot identity is a UUID reference, not an ORM relation, and collapsing it requires more complex subqueries. Missing retained evidence remains a fail-closed internal invariant error, not a public API. This path is once per actual refusal, not once per history entry during every promotion. |
| Claude L6: repeated batch constant | Named the shared source batch limit and removed redundant insert batch arguments; each list remains capped before insertion. |

### Complete validation at the second reviewed head

The frozen `80416f2` head passed all eight PostgreSQL shards: 3,032 database
tests, no skips or missing partitions. The combined baseline/database coverage
gate passed at **94.17% lines and 85.58% branches**; the slowest database shard
took 630.91 seconds. Evidence is in
`/tmp/parishkit-family-quality-final.ZMl8g5` (local, not committed).
The rebuilt Docker image passed both configured runtime profiles in 155.15
seconds and both build-context checks. This complete result does not stand in
for affected-check validation of the subsequent round-two corrections or the
required final-head CI. No retained development databases were deleted.

Round-two corrections pass 91 PostgreSQL refusal/source-role/recovery/schema
tests and 40 focused unit tests. Ruff, formatting, tracked Markdown, whitespace
and Django model-state checks pass. The independently audited schema differs
from the preceding correction only in the immutable function's fixed search
path; all object counts and other fingerprints are unchanged.

## Round 3 review and corrections

Pika session `20260916-051829-4c3e85` reviewed the correction delta from
`80416f2b1dc91cd54500198b1cce2c03cd428d4d` through
`d2bfc0c4baf2b0ff82011718df3ddfb9611aa987`, tree
`65839158428f227d7a4d5d6891e07629521451f9`, with surrounding integration context.
Both sources completed successfully; Codex reported no findings. Claude covered
all five files and reported two Low test-coverage items. Finalize approved with
no validated findings, degradation, failed agent, mismatch or salvage. There
were no raw Medium, High or Critical findings.

- Claude L1: added explicit before/after equality of the archived Family's
  version, deliverability and reason for every annual-rollover timing. Late
  evidence affects only the current population; it cannot rewrite the old row.
- Claude L2: added a closed mutation-grant matrix over every installed runtime
  identity and each credential-installer target, including column grants and
  TRUNCATE. Only WORKER can append resolution. Reserved backup/key-rotation
  profiles remain rejected; migration/provisioning schema-owner authority is
  deliberately outside the online-role claim. This supplements the real
  four-role PostgreSQL ACL assertions rather than pretending unimplemented
  services already exist.

The third reviewed head passes the complete baseline: 5,756 tests, 3,845
environment-gated skips and two warnings in 56.67 seconds. Its rebuilt image
passes both configured runtime profiles in 111.74 seconds. The final corrections
change regression assertions only; no application or schema behavior changed
after the third reviewed head. Exact-head CI and protected queue evidence remain
required before delivery.

Both final test corrections pass: sixteen PostgreSQL refusal/lifecycle tests
and 27 runtime-grant unit tests, plus formatting, Ruff, Markdown and whitespace
checks. All three rounds therefore have passing post-correction evidence, with
no unresolved accepted Medium-or-higher finding. The following protected PR
run must validate the final complete head, including the added grant test.
