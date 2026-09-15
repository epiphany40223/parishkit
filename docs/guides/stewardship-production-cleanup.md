# Production-transition cleanup implementation checkpoint

[Tasks](../tasks/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker) ·
[Work package](../plans/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker) ·
[Cleanup contract](../specs/stewardship/background-processing/spec.md#production-transition-cleanup) ·
[Controlling delivery plan](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

## Scope and starting point

Branch `pr/stewardship-production-cleanup` starts from verified PR #32 merge
`5c85d26ff586cad5539ff2c324c89cd621cbcf9d` on refreshed `origin/main`.

Deliver BG-03's five items as one independently testable general-worker
increment: exact Testing inventory and non-sensitive aggregate, atomic gate and
epoch invalidation, bounded checkpointed deletion, completion verification,
retry/recovery and safe cancellation. The existing Production journal is a
foundation, not evidence that these operational owners already exist.

Keep the later ADM-05 readiness/activation web workflow, BG-04 schedule owners
and BG-06 dispatch owners disabled. Cleanup never changes mode or lifecycle.
Normal validation uses synthetic data, disposable databases and fake providers;
no retained database deletion, real provider writes, deployment or release is
authorized by this increment. Gate 3 remains after complete Phase 4/5 integration.

## Validation checkpoints

- Exact inventory/category ownership and aggregate privacy, including mixed
  Testing/live/operational and other-campaign preservation.
- Bounded transactions, checkpoint/deletion atomicity, interruption/replay,
  superseded claims, cancellation and verified completion.
- Rehearsal invalidation and denial of concurrent Testing work; stable Production
  codes and anonymous rehearsal code reservations remain intact.
- Actual restricted runtime roles, SQL denial tests, worker configuration
  authority and fresh-install schema/catalog validation.
- Credential-free baseline, Ruff, formatting, Markdown and relevant Compose
  checks, followed by the required three dual-source review/fix rounds.
- Exact-head PR CI and all merge-group checks, then protected delivery and
  verification on refreshed `origin/main` before the next increment.

BG-03 implementation and local review acceptance are complete. Final-head PR and
protected merge-group CI remain required before delivery; Gate 3 is not released.

## Implemented working-tree checkpoint

The compiled general worker now captures a sealed, independently SQL-verified
17-category inventory, deletes dependency-ready bounded batches with atomic
checkpoints, and verifies completion against both retained membership and actual
Testing data. Private transaction proofs authorize only the inventoried deletes;
runtime roles cannot read private target membership or forge those proofs.
Terminal Testing mail detail is removed while Production/operational mail,
stable Production identities and anonymous rehearsal reservations are preserved.

Cancellation is a durable Admin intent honored at worker/recovery boundaries;
partial deletion is never rewound. Exhausted retries retain the gate and emit a
deduplicated non-sensitive critical event. Explicit retry creates a journaled
child task. Internal request ports remain unavailable as web activation/readiness
workflows until ADM-05 supplies its authoritative checks and UI.

Local synthetic-data validation before the first independent review:

- 61 cleanup tests passed in 65.65 seconds; scoped coverage is 91%.
- 5,554 credential-free baseline tests passed in 56.43 seconds; 3,553
  environment-gated tests skipped, with two existing warnings.
- 138 journal/schema/storage/schedule regressions passed in 69.68 seconds.
- An independent fresh-schema comparison against PR #32 passed before accepting
  the updated catalog fixture: four added tables, 29 columns, 44 constraints,
  21 functions, 12 indexes and 30 Stewardship triggers, with no removed objects.
  Changes to existing objects are limited to one event constraint and nine
  functions for deletion authorization and private control metadata. Django
  session deletion protection is checked separately. No retained database was
  upgraded or deleted.
- Django reports no missing migration state; this remains a fresh-install
  baseline, not an upgrade contract.

The first Claude permission probe appended a stray period to its validator
command and was denied. The exact-command retry passed with no permission
denials, byte-identical fixture delivery and parent validation. Neither probe
reviewed code or counts toward the three required rounds.

Independent review rounds, additional race/admission acceptance evidence,
operational validation and final-head/merge-group CI remain outstanding.

## Round 1 review and corrections in progress

Pika session `20260915-153244-e57701` reviewed full branch commit
`1c61b59529aafcd84467c41f08d590b8bd6990cd` against `5c85d26f`.
Both generated Claude shards and the Pika-managed Codex reviewer completed;
finalization has no failures, degradations or verdict mismatches. Result:
`REQUEST_CHANGES`, 17 validated findings (two High, 15 Medium) from 43 raw
findings. The round is not complete until corrections and validation pass.

The following identifiers use each source's order in the retained finalized
artifact. Routine technical triage is delegated by the controlling plan.

| Finding | Raw severity | Disposition and evidence owner |
| --- | --- | --- |
| Claude 1 | High | Accepted: honor cancellation in failure handling and replay after task terminalization; regression validation in progress. |
| Claude 2 | High | Accepted: remove repeated whole-inventory materialization, index target lookup and validate production-size batches; performance acceptance in progress. |
| Claude 3 | Medium | Accepted: add `(category, target_id)` protection lookup index and catalog regression. |
| Claude 4 | Medium | Duplicate of Claude 3; same index correction. |
| Claude 5 | Medium | Accepted: add actual two-connection gate/batch/cancellation and fencing coverage. |
| Claude 6 | Medium | Duplicate of Claude 2; same inventory/planner performance work. |
| Claude 7 | Medium | Accepted with Claude 1: completion acknowledgment must honor cancellation intent atomically. |
| Claude 8 | Medium | Accepted: invoker-role guards reject manifest-less runtime checkpoint/completion; owner-only foundation fixtures remain nonoperational. |
| Claude 9 | Medium | Accepted: failed initial binding needs a valid domain binding before durable retry; constraint denials must not be mislabeled transient errors. |
| Claude 10 | Medium | Accepted: deduplicate exhaustion alerts per run, not across subsequent explicit Admin retry chains. |
| Claude 11 | Medium | Duplicate of Claude 8; same manifest-less runtime denial. |
| Claude 12 | Medium | Accepted: private exception checks actual schema ownership, not incidental manifest INSERT grants. |
| Claude 13 | Medium | Accepted: reject external occurrence references at capture before gate/epoch changes commit. A real restore-hold/resolution regression preserves the history and verifies complete rollback. |
| Codex 1 | Medium | Duplicate coverage concern of Claude 5; test both actual connection orderings. |
| Codex 2 | Medium | Accepted: completed historical tombstone gates must not block current cleanup; keep active purge gates blocking. |
| Codex 3 | Medium | Duplicate completion race of Claude 7; shared atomic acknowledgment correction. |
| Codex 4 | Medium | Accepted performance concern alongside Claude 2; candidate-scanning and lease-budget evidence remains open. |

Independent local validation also found that the default-deny Docker build
context omitted the new SQL file. All eight operational cases failed at schema
loading for that same missing file, before runtime startup. Both context
allowlists and the build-export regression now explicitly include `cleanup.sql`;
successful rebuilt-image operational evidence is still required.

New real-connection tests exposed an additional open-form defect: deleting a
source pin before its still-open baseline violates the deferred protection
guard. Cleanup now treats the baseline and its pin as a coupled transaction,
without relaxing the guard. Ten focused race/batch tests pass after this fix.
Cancellation tests also retain the existing stale-version rejection contract;
an Admin must refresh a request changed by a just-committed batch.

The correction uses one private transaction-local batch plan, verifies its exact
campaign/routing membership once before deletion, and uses a closed indexed
primary-key existence check for final membership removal. Every target still
requires its unforgeable proof and live claim; each DELETE must affect exactly
one row. Source-protection companions are deleted together. Candidate dependency
filtering occurs before the bounded procedural window, and each batch renews
only an already-current lease after admission/lock waits.

A disposable 5,000-Family benchmark (10,000 rehearsal targets, 20 default-size
batches) completed cleanup in **6.924 seconds**, with a maximum batch duration
of **0.356 seconds**. Earlier intermediate approaches took 91.251–113.523 seconds
and are not the accepted implementation. Fixture generation plus the final
benchmark took 33.12 seconds. Normal CI uses a smaller 501-Family/1,002-target
regression to cross the real 500-row budget without repeating large allocations.

The updated independent baseline audit passes with 23 added functions, 13 added
indexes and 31 added Stewardship triggers; the same four tables, 29 columns and
44 constraints are added, and unchanged catalog families still match PR #32.
Two Docker build-context export tests pass, including explicit inclusion of the
new SQL and continued exclusion of synthetic private files. Rebuilt-image
operational validation and the complete correction regression runs are pending.

Post-correction validation now passes 83 combined cleanup tests in 112.81
seconds, followed by all eight cancellation tests (including the added
fifth-attempt failure race) in 24.04 seconds; 69 strict-schema/storage/journal
regressions in 40.07 seconds; and 5,554 credential-free baseline tests in 58.37
seconds (3,575 environment skips and two pre-existing warnings). Ruff, formatting,
Markdown, whitespace and Django model/migration-state checks pass. The five
duplicate findings above are consolidated into their accepted corrections;
all 12 remaining findings have implementation and regression coverage.
The intermediate rebuilt image passed all eight operational Compose cases in
767.32 seconds. That image predates the final batch performance correction, so
it proves SQL packaging but is not final-head runtime evidence. The later full
cleanup coverage run passed 84 tests with 93% scoped coverage in 136.41 seconds.
Round 1 corrections are complete; later rounds and final-image/CI validation
remain mandatory.

## Round 2 correction review

Pika session `20260915-161604-dcc885` reviewed `4800dd3b` against `1c61b595`.
The generated Claude reviewer and Pika-managed Codex reviewer both completed
without failures, degradations, mismatches or salvage. Finalization returned
`COMMENT`: three validated Medium findings, no High/Critical, from 11 raw
findings (four raw Medium observations, seven below-cutoff Low observations).
The exact-command permission preflight passed independently with byte-identical
fixture delivery and zero denials.

| Finding | Severity | Disposition |
| --- | --- | --- |
| Agreed 1 | Medium | Accepted: cap examined candidates using an indexed durable numeric inventory cursor, not LIMIT after dependency filtering; add the source-pin parent lookup index and response-heavy regression/benchmark. |
| Claude 1 | Medium | Partially accepted: capture now rejects inventoried occurrences referencing retained Production/operational outbox messages, with both actual guarded states tested. Other proposed cross-mode/campaign references are already impossible: `stewardship_fulfillment_guard_v1` requires matching mode and campaign, while baseline/submission guards select the predecessor from the same Family, mode and rehearsal epoch. Those ownership fields are immutable. |
| Codex 1 | Medium | Accepted: check public namespace ownership rather than ownership of the request table. Restricted-role regressions assign the request table to the worker while leaving schema ownership separate, then restore fixture ownership before role cleanup. |

Checkpoints now retain scanned count, numeric position and scan round. SQL owns
these values, including scan-only progress over blocked prefixes, and rejects a
full sweep without deletions. A 21-Family submitted-response regression exercises
independent baselines, pins and receipts, advances past a zero-deletion window,
replays it without rescanning, and revisits parents on subsequent sweeps. The
ordinary worker also completes with two-row scan/deletion budgets. Regression
tests verify the two new lookup indexes and rejection of a no-progress loop.

Additional integration validation reproduced a Family logout HTTP 500 after
inventory capture: ordinary logout attempted to delete the captured baseline's
source pin. Logout now revokes access and detaches the browser while leaving
Testing baseline/pin detail under the go-live gate for its cleanup worker;
ordinary session retention skips those gated Testing sessions. The actual HTTP
logout, subsequent retention run and final cleanup are exercised together.

The independent PR #32 schema audit passes before accepting the new strict
catalog fixture: four added tables, 33 columns, 53 constraints, 24 functions,
15 indexes and 31 Stewardship triggers, with no removed objects. Existing
changes remain the nine previously recorded functions, event constraint and
the checkpoint progress constraint. PostgreSQL's named NOT NULL constraints
are included in those counts. No retained database was upgraded or deleted.

Post-correction validation passes 79 cleanup database tests in 160.91 seconds
and 14 unit tests in 0.22 seconds, with combined scoped coverage of 93%; 86
schema/storage/journal tests in 48.04 seconds; 42 adjacent authentication,
baseline and source-promotion regressions in 47.24 seconds; and 5,554 baseline
tests in 60.77 seconds (3,585 environment skips, two existing warnings). Ruff,
formatting, Markdown, whitespace and model/migration-state checks pass. Strict
model/schema validation caught equivalent-but-differently-ordered constraint
expressions; the model/state now exactly match the installed constraint.

The 501-submitted-Family benchmark captures 4,010 targets, including actual
response baselines, submission pins and receipts. The real worker completed in
5.526 seconds over 15 batches; its slowest batch took 0.513 seconds. Synthetic
fixture allocation plus cleanup took 126.35 seconds. The normal regression uses
21 submitted Families and a ten-row budget to exercise the same blocked-prefix
behavior without this larger allocation cost.

All three Round 2 findings have evidence-backed dispositions and passing
correction validation. Current-image Compose validation, Round 3 and final-head/
protected merge-group CI are still outstanding. The local runtime image predates
only the equivalent model/state constraint-expression ordering correction;
final CI must build the actual delivered head.

## Round 3 review and correction

Pika session `20260915-165018-330939` reviewed `15d2c08a` against `4800dd3b`.
Both reviewers completed successfully after the exact permission preflight.
Codex returned `APPROVED` with no findings; its review took 409 seconds without
a timeout or stall. Claude reviewed all 19 manifest files. Finalization returned
`COMMENT`, with one validated Medium finding from 12 raw observations (11 Low
observations filtered out); no High/Critical, failed agents, degradations,
verdict mismatches or salvage remain. Reviewer-local PostgreSQL tests were
unavailable, so passing execution evidence comes from the independent local
validation recorded here, not from the reviewers' statements.

Claude's Medium reverse-reference finding is accepted defensively: capture now
also rejects a retained occurrence or post-close resolution pointing at an
inventoried Testing outbox message. The stated operational-routing example is
already prohibited by `schedule_occurrence_routing`; post-close lifecycle and
same-occurrence binding are also guarded. Nonetheless, a malformed retained
UUID-only pointer must fail capture rather than become dangling. Regression
fixtures explicitly install malformed historical pointers as the disposable
schema owner, restore all guards, and verify atomic capture rejection without
history/message changes. A valid same-inventory occurrence/message pair remains
deletable through the ordinary worker. No runtime guard is weakened to arrange
those fixtures.

Correction commit `2d351f4` also exercises invalid request/command UUIDs and
incapable/unbounded budgets. All 35 correction/capture/authority tests pass in
52.25 seconds; the independent schema audit passes with unchanged object counts,
and all 17 strict schema tests pass in 14.57 seconds after accepting only the
reviewed manifest-guard function fingerprint change. These corrections complete
the third review/fix round under the controlling delivery policy; a fourth round
is not required solely because this round included its regression-tested fix.

All eight intermediate-image operational Compose cases passed in 762.49 seconds.
The final image was rebuilt after the reverse-reference correction; its
provisioning/ingress checks, complete cleanup coverage run, final baseline,
exact-head PR CI and protected merge-group checks supply the remaining delivery
validation. No merge or next-increment start is claimed yet.

## Local acceptance and delivery boundary

The final fresh run passes all 100 cleanup tests in 160.04 seconds, with
93.57% scoped line coverage (320/342 statements) and 81.15% branch coverage
(99/122 branches). Both separate coverage floors exceed 80%; the displayed
combined statement/branch metric is 90.30%. Final baseline validation passes
5,554 tests in 51.75 seconds (3,592 environment skips, two existing warnings).
The final rebuilt image passes provisioning and ingress checks in 3.20 seconds.
Ruff, formatting, Markdown, whitespace and model/schema state checks pass.

All three dual-source rounds are complete, the last has no High/Critical
finding, and every accepted Medium-or-higher finding is resolved. Scope remains
BG-03 only: cleanup does not activate Production, publish provider data, deploy,
release or waive the later integrated gate. The owning checklist is locally
complete; exact-head PR CI and every merge-group job remain mandatory before
using the standing protected-merge/continue authority.

## PR #33 CI correction

The initial PR head `f34c809c611103a583b1bc9e672ea5860130a3bc` was consolidated
from nine development commits to four signed-off commits. It is tree-identical
to `0a2a527496adbb389e85f4669677089683aad9c8`, retained on review-only branch
`jsq/pr/stewardship-cleanup-review-evidence-20260915`; both trees are
`9c6feba8eed373519c098cf2c060dd85ffc14e17`. Never merge that backup history.

[PR #33](https://github.com/epiphany40223/parishkit/pull/33) CI run
`35024319557` completed with 22 successful jobs and one underlying test failure
in database shard 5; the aggregate database gate correctly failed as a result.
All browser engines and runtime configurations passed. The stale pre-BG-03
delivery-grant matrix still denied every cleanup-control write. It now asserts
only the compiled worker's exact 11 request command columns and checkpoint
INSERT grant, while still rejecting generic delivery writes, private data
mutation and protected inventory-field updates. The existing actual-row cleanup
admission/denial tests remain intact. No runtime grants, application code or SQL
guards changed for this CI correction, so this is a test-contract update rather
than a material implementation fix requiring an additional review round.

All 71 delivery-boundary and cleanup authority/worker regressions pass in
81.28 seconds. The test correction was folded into the implementation commit;
the four-commit structure remains. The post-fix unsquashed/squashed trees match
(`638f300495ff42d2768316c2cb00369a6b56f6ed`) before this documentation update,
and the runtime source/deployment diff against the original reviewed PR head is
empty. Fresh final-head CI and all merge-group checks are still required.
