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

The intentional delta adds two append-only refusal/resolution tables, sixteen
columns, twenty-two constraints, seven indexes, five functions and three
triggers. The existing occurrence semantic-revision constraint and backing
index include the recovery generation; semantic fulfillment remains unchanged.
Three existing guards change: occurrence insertion validates recovery evidence,
immutable identity includes the generation, and activation preparation accepts
its canonical generation-bound key. No existing columns, relation definitions,
ACLs, triggers or row policies change; no objects are removed.

The audited current totals are 154 relations, 1,779 columns, 2,538 constraints,
779 indexes, 389 functions, 391 triggers and 28 row policies. The strict
fingerprint is updated only after that exact object-level comparison. Django
reports no model-state drift, and model-versus-installed-schema checks pass.
