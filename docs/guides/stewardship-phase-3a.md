# Phase 3A: Minimal Family response

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3a-minimal-family-slice) ·
[Controlling scope](../plans/stewardship/overall.md#3a-submission-data-and-minimal-vertical-slice) ·
[Data tasks](../tasks/stewardship/data.md#dat-06-immutable-submissions-and-proposal-overlay) ·
[Family tasks](../tasks/stewardship/parishioner-portal.md)

## Starting point and boundary

Branch `pr/stewardship-family-response` starts at the verified PR #22 merge,
`7b2b1dd4478ae4014b167d6c0c203127c9a0ceb9`. The target is one independently
testable minimal Family flow, not a PR for each model or helper. Phase 3B owns
the remaining fields/modules and the integrated Gate 2 demonstration. Live
provider dispatch remains Phase 4; this increment does not deploy or release.

## Internal checkpoints

1. DAT-06: trusted, session-bound form baselines and immutable atomic responses,
   including source pins, mode isolation and concurrency regressions.
2. DAT-07 submission/follow-up subset and DAT-08 effective-value reconciliation,
   preserving immutable Family provenance and local transactional side effects.
3. FAM-01/02 and minimal FAM-03/06 integration: login, Testing acknowledgment,
   in-memory census page, review, final Submit, Thank You/logout and revisit.
4. DOM-05 minimal executable acceptance scenario, targeted browser/privacy
   verification, and the three-round dual-model review/correction cycle.

These are internal checkpoints within the same coherent increment. Task boxes
remain unchecked until their complete owned scope and verification pass.

## Current evidence

DAT-06/DAT-08 are in progress. The typed comparison, capability mapping,
three-way merge and minimal census dependency projection have 148 passing
unit tests. The source-backed baseline owner, answer-free metadata, pin
guards and session cleanup pass 32 PostgreSQL/Family-auth regressions in
24 seconds. Issuance, replacement and cancellation also pass under the exact
restricted web SQL login (20 baseline/operational-auth tests in 18 seconds).

The first census schema includes read-only Family name/envelope information
and active Members' first, middle and last names and email. One shared field
registry supplies UI definitions and concurrency dependencies. Additional
fields and Ministry/financial adapters remain Phase 3B; the current input owner
refuses unsupported enabled modules rather than accepting an incomplete
aggregate. Source fields not supplied by the verified loader are not invented.

The local final-submit owner now writes guarded immutable responses, field
proposals, live-only follow-up/participation effects, and a stub confirmation
occurrence, then revokes the Family session. The receipt stub claims neither
preparation nor provider delivery; actual dispatch remains Phase 4. The census
validator adds 28 unit tests (176 focused unit tests total). Fifteen PostgreSQL
submission/revisit tests pass in 31 seconds, including Production submission
under the exact restricted web login, repeat-response supersession, follow-up
replacement/withdrawal, and coherent source catch-up/conflict reconciliation.

The census browser flow is now connected: `/family/` is an answer-free shell,
`POST /family/form` accepts only Testing entry consent, and `POST /family/submit`
is the only answer-bearing endpoint. The tab retains edits only in memory and
performs a separate review/Submit after a relevant source or response change.
Shared CSRF, no-store, CSP, session, presence and keepalive boundaries apply.
Final responses log out; revisit renders only effective values, never hidden
competing source/Admin values. Rehearsal cleanup is bounded and cannot delete
active or live responses, and activation independently rejects retained test
detail even when credential cleanup has already finished.

The integrated 66-test PostgreSQL suite passes in 75 seconds, including current
model-versus-baseline DDL, exact web/source-worker roles, two-adult response
ordering, epoch separation, late-owner rollback, and HTTP source refresh/review.
The first 18 new browser tests pass across Chromium, Firefox and WebKit, with
320/1,280-pixel views, axe accessibility, keyboard focus, separate Testing
acknowledgments, stale edit preservation and no draft persistence. The complete
default profile passes 4,533 tests in 44 seconds (2,674 explicit opt-in skips).
The first real cross-domain Family acceptance node now marks scenario 5 as
partial in the [acceptance manifest](../development/stewardship-acceptance.yaml).

Broader validation and the remaining dual-model review rounds are in progress;
[review/correction evidence](stewardship-phase-3a-reviews.md) records the first
two completed reviews and their regression checks. No review gate or whole Family/data package
is claimed complete; remaining census fields/modules belong to Phase 3B.

## Schema audit

A separate new preserved database was installed from the exact PR #22 merge's
SQL and first verified against the pre-change checked-in fingerprint. A fresh
current installation was then compared per catalog object. No old object was
removed; no existing column, constraint, index, relation/ACL, or row policy
changed. The delta is five response tables, 80 columns, 124 constraints,
36 indexes, 12 response functions, and 11 triggers. Only two existing
functions change: source-pin admission/protection and activation's requirement
that invalidated Testing response details have been removed. Current model DDL
independently matches the fresh SQL tables, including types/defaults/constraints
and indexes; the fingerprint was updated only after reviewing this delta.

The current inventory totals 128 relations, 1,467 columns, 2,098 constraints,
660 indexes, 298 functions, 309 triggers and 28 unchanged policies. Existing
retained development databases were neither upgraded nor deleted.
