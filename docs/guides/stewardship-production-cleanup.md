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

Implementation is in progress. No BG-03 task or review gate is claimed complete.

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
Rebuilt-image operational validation is still running, and rounds 2/3 plus
final-head and merge-group CI remain mandatory before delivery.
