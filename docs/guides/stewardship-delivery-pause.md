# Stewardship live delivery pause

[Review ledger](stewardship-delivery-pause-reviews.md)

Continue [ADM-06.02](../tasks/stewardship/admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive)
after [PR #61's protected delivery](stewardship-production-withdrawal.md#protected-delivery).
Branch `pr/stewardship-delivery-pause` starts at verified `origin/main`
`4be1ce09`. The [live delivery pause specification](../specs/stewardship/admin-portal/spec.md#live-delivery-pause),
[pre-provider admission contract](../specs/stewardship/background-processing/spec.md#durable-scheduling-and-task-execution),
[ADM-06 implementation package](../plans/stewardship/admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive)
and [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
control this work.

## Increment boundary

Build current-Admin, freshly authenticated pause and resume controls with exact
impact previews, durable reason/history and atomic unsent-message holds.
Preserve Family access and submissions, distinct receipts, immutable Testing/
operational routing and already-submitting/unknown delivery reconciliation.
Show pause duration, actor/reason and held/uncertain counts in the Admin shell
and background view. Resume requires current provider/sender proof and complete
coalescing/coverage before any selected mail is released. Closing while paused
skips Family invitations/reminders but retains accepted receipts and completed-
day digests for explicit reasoned release/cancellation; it never reopens access.

Restore release, campaign reopen, archive/unarchive, Return to Testing and purge
remain separate later-phase owners. The delivery-pause part of ADM-06 does not
complete that entire package or release Gate 3.

## Checkpoints

1. Reuse the durable pause control, outbox holds, current-session authorization,
   ordered locks, provider evidence and shared recovery/coalescing policy.
2. Add exact immutable Admin intent and its private SQL effects; preserve narrow
   runtime grants and independently enforce scope, session, clocks and versions.
3. Add accessible native preview/confirmation/status pages and persistent chrome
   without exposing recipients, message bodies or credentials in count views.
4. Exercise actual restricted roles, replay/revocation, stale previews,
   submission/dispatch/close races, backlog coalescing, unknown delivery,
   failed resume rollback and distinct receipt release. Audit fresh schema/model
   parity and browser behavior without upgrading or deleting retained databases.
5. Finish current-phase service/queue handoff evidence, three dual-source review/
   fix rounds and final-head CI/DCO before protected delivery. No real-provider
   dispatch, deployment or release is authorized by this increment.

Implementation and acceptance are not complete at this initial checkpoint.

## Initial pause checkpoint

The signed, five-minute current-Admin preview binds campaign/runtime versions,
reason and the exact live-message inventory. A private SQL effect independently
checks the fresh session, scope, clocks, ordered locks and inventory, then
records the control and attaches all unsent live-message holds atomically.
Already-submitting/unknown delivery remains separate; message bodies, encrypted
substitutions and routing do not change. The native page and Admin banner show
the held counts, uncertainty, actor, reason and elapsed pause duration.

The first actual restricted-Web test passed in 25.02 seconds. The expanded
HTTP/CSRF/stray-input/banner scenario passed alongside the strict fingerprint
and 15 other schema checks; model equivalence identified a literal-array cast
difference, corrected in the fresh SQL rather than normalized in the test.
Independent catalogs `/tmp/parishkit-delivery-pause-before-a.json` (verified
merged `4be1ce09`) and `/tmp/parishkit-delivery-pause-after-b.json` add only the
command table/count view, 18 columns, 22 constraints, four indexes, two functions
and two triggers. No existing object/ACL/policy changes or database upgrades are
in this checkpoint. Model state agrees under `makemigrations --check --dry-run`.
The corrected strict fingerprint and model equivalence both passed in 14.77
seconds. Repository lint/formatting and the guide's Markdown check passed.

Resume, exact recovery/coalescing, post-close resolution, next-due projections,
the remaining negative/race/browser coverage and all review rounds remain open.
This checkpoint is not ready for protected delivery.

## Forecast and provider-check checkpoint

The control page now forecasts the earliest configured due instant and counts
the tied schedule slots, explicitly distinguishing those from future recipient
counts. It uses the ordinary civil-time resolver, including DST gaps/folds,
skipped civil days and the final daily report after close. The focused schedule,
forecast and runtime-grant checks passed: 69 tests in 0.27 seconds.

Paused Production campaigns can use the existing explicit fictional test-mail
workflow. The provider check requires accepted mail under the current applied
configuration and credential, submitted after this pause and within five
minutes. A later unsuccessful/unresolved test or current-provider failure
invalidates the proof. Passive pages perform no provider request, and neither
testing nor acceptance releases a live message. The actual restricted-Web and
mail-worker scenario exercised not-sent, accepted and uncertain outcomes while
retaining the live pause and unchanged held payload; it passed in 27.69 seconds.

The independent `after-c` catalog adds only the two-column health projection and
changes the explicit test-mail admission function. Existing object permissions,
constraints, indexes, triggers and policies are unchanged. This remains a fresh
installation baseline; no retained database was upgraded or deleted.
All 17 strict baseline/model-equivalence checks passed in 19.80 seconds.

Initial draft CI found the newly added count view missing from the SQL-only
grant-test inventory; the registry assertion is corrected. Its reference-load
scenario also exceeded the two-minute diagnostic threshold; the focused local
run passed in 52.59 seconds. Final-head CI and all review rounds remain required,
and resume/post-close recovery remain in progress.

## Pre-start resume checkpoint

The native preview/confirmation path can now resume a paused Scheduled campaign
before its start, where the overdue plan is empty. It binds current sender proof
and the exact inventory, rechecks both under the ordered locks, and atomically
records resume with release of every held unsent message. Payload, due time,
routing and attempt history remain unchanged. SQL independently rejects due
work, uncertainty, stale health and any Active/Closed use of this narrow path.
This is not yet the complete overdue or post-close recovery implementation.

The combined actual-role scenario passed in 27.90 seconds: unordered/forged
SQL commands, wrong session/actor/authentication, stale versions/counts,
invalid deadlines/selections, changed health, explicit resume and exact replay.
Thirteen negative SQL variations share one genuine setup. Independent catalog
`after-d` changes only the delivery-command guard/effect bodies from `after-c`.
The reference-load test also passed with coverage enabled in 77.10 seconds;
its production confirmation and concurrent Family-submit latency assertions
remain unchanged.

Complete Family planning now includes current due definitions with no occurrence
or outbox, alongside retained work and semantic/restore coverage. Its public
projection exposes counts and a fingerprint only; the restricted Web role cannot
read private planning rows. The real setup scenario now includes two later
reminders. A transactionally rolled-back pause proves that the initial wins and
both unmaterialized reminders are counted for coalescing, with no durable pause
or release. The expanded test passed in 27.79 seconds. Applying that complete
plan, digest recovery and post-close resolution are still pending.

The independent `after-e` delta adds only the private Family planning view and
its public aggregate view (29 columns total). The current CI run also identified
the delivery command's nonstandard immutable-guard name missing from the guard
registry test; it is now registered with its exact trigger/function, not exempted
from immutability checks.

## Family recovery checkpoint

Resume now applies complete Family recovery for an in-interval campaign without
digest schedules or unfinished activation catch-up. Its exact plan fingerprint
includes due unmaterialized work, current Family/source/response state, semantic
coverage and retained work versions. The private effect freezes identities,
creates missing original occurrences, cancels redundant unsent messages,
coalesces/skips originals and records fulfillment before releasing the pause.
Waiting redundant tasks are cancelled; running tasks keep their real lease and
drain at their next safe point. Distinct receipts are not coalesced.

The expanded single-setup scenario uses three later reminders: a queued held
message, a running-but-not-submitting held message, and an unmaterialized slot.
All three coalesce into the original invitation, both redundant outboxes cancel,
the running task remains running, and original coverage is retained. It passed
in 28.91 seconds. The earlier two-reminder effect scenario passed in 28.58
seconds. Full digest/post-close recovery, failure/race/receipt/browser acceptance
and all three review rounds remain required before protected delivery.

Catalog `after-f` changes only the aggregate fingerprint expression to aggregate
fixed-width row hashes rather than a large JSON array. `after-g` adds the private
13-column transaction-bound effect table, its primary key and the private Family
effect function; it changes only the command guard/effect and the narrow,
proof-backed running-occurrence coalescing branch. No existing object is removed
and no existing ACL or policy changes. All catalogs remain separately retained.
All 18 schema/model/immutable-guard checks passed in 19.32 seconds; the 123
focused forecast, policy and grant tests passed in 0.27 seconds. Repository
lint/formatting, guide Markdown and whitespace checks also passed.

## Digest recovery checkpoint

In-interval resume now includes complete daily and weekly groups, including
original dates not yet materialized by the bounded scheduler. A count-only
projection and fingerprint bind current work/preparation versions. Incomplete
preparation or uncertain delivery blocks release. Manual weekly requests keep
their separate intent and are not ordinary overdue coalescing candidates.

The private effect creates missing dates, selects an unowned current occurrence
or allocates a replacement aggregate, cancels redundant unsent fanout and
waiting delivery/finalizer tasks, and records every original slot's coverage
before clearing the pause. Previously accepted recipient evidence is unchanged.
The ordinary digest worker consumes the selected durable obligation and reuses
actual accepted coverage rather than treating cancellation as provider success.

Three actual-role PostgreSQL scenarios passed in 66.17 seconds, sharing setup
within each scenario. They cover complete unmaterialized daily/weekly ranges,
prepared report replacement, blocked incomplete preparation, accepted-recipient
deduplication, unknown-provider refusal, forged SQL impact, a newly due date
invalidating an otherwise unexpired preview, and transaction rollback after
the real recovery effects but before commit. The preceding prepared-recipient
scenario passed in 29.86 seconds. Thirty-four focused unit tests and repository
lint passed. Checkpoint `344ead3` CI `35431663969` completed successfully.

The independent `after-i` catalog delta from `after-g` adds one private effect
table, three projections, 30 columns, 11 constraints, one index and one private
function. Only the control guard/effect and proof-bound occurrence coalescing
branch change among existing objects; existing ACLs, policies and triggers are
unchanged. All 17 strict schema/model-equivalence checks passed in 18.64 seconds.
Catalogs remain separately retained, with no database upgrades or deletions.
Post-close resolution, remaining integration/browser acceptance,
three review rounds and final-head CI remain required; the PR stays draft.

## Closed-message resolution checkpoint

The native closed-campaign workflow previews exact selected receipt/digest
counts, semantic coverage and current versions. Release requires the current
sender check; cancellation requires a reason but no working provider. A private,
immutable per-message decision permits only the selected message to cross its
current pause. Another type remains held, and a later pause invalidates that
permission. Ordinary invitation/reminder close admission still cancels Family
mail without provider transport or reopening access. The same transaction clears
the pause only after held and uncertain obligations are resolved.

New live outboxes now receive their durable pause hold before insertion,
including receipts created by final Family submission and worker-created reports.
Cancellation retains exact receipt UUIDs or schedule-slot/item-version/daily-range
coverage in the existing post-close resolution journal. It does not insert
provider-delivery fulfillment. Scheduler and digest discovery consult these
semantic resolutions; weekly history advances the resolved interval without
claiming that cancelled actionable content was reported. Unfinished preparation
and selected uncertain mail prevent cancellation.

The actual restricted-role Family-submit, receipt/weekly cancellation, selected
daily release, closed invitation cancellation and new-report hold scenarios
passed together with all schema/model and immutable-guard checks: 20 tests in
58.16 seconds. Thirty runtime-grant checks passed in 0.14 seconds, and
`makemigrations --check --dry-run` found no model-state changes. Nine real-template
Chromium/Firefox/WebKit checks passed in 16.61 seconds across mobile/desktop
layouts, WCAG checks, keyboard forms and JavaScript-disabled resolution.
The extended weekly scenario also passed in 29.14 seconds: a real schedule
replacement and restarted restricted scheduler do not recreate a cancelled slot.

Independent catalog `after-k`, compared object-by-object with `after-i`, adds
the immutable per-message journal, three views, 22 columns, 19 constraints,
seven indexes, six functions and two triggers. Ten existing function bodies
change for narrowly proven release, immediate holds and semantic cancellation.
No existing object is removed; existing ACLs, row policies and triggers remain
unchanged. Both catalogs and all development databases remain retained.
Checkpoint `946bfdd` CI `35432537356` passed. Current-head CI, remaining handoff
evidence and all three review rounds remain required; PR #62 stays draft.

## Phase 4 service and milestone handoff

This is the current-phase recheck required by the
[master plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications),
not an integrated Gate 3 review or production approval. Final review/CI acceptance
and protected delivery below must precede Phase 5.

| Milestone | Evidence and retained boundary |
| --- | --- |
| M4.01: Testing and operational routing | [Family dispatch](stewardship-family-mail-dispatch.md), [receipts](stewardship-submission-receipts.md), [daily](stewardship-daily-digests.md) and [weekly](stewardship-weekly-digests.md) exercise immutable campaign routing. [Operational notifications](stewardship-due-work-health.md#current-phase-bg-10-acceptance-map) retain their separate classification and pause exemption. |
| M4.02: Cleanup and atomic activation | [Readiness/cleanup](stewardship-go-live-readiness.md), [final confirmation](stewardship-production-confirmation.md) and [withdrawal](stewardship-production-withdrawal.md) retain restart/cancellation, exact version checks, bounded catch-up and reference-load evidence. |
| M4.03: Scheduled communication and recovery | The Family/receipt/digest guides above record durable occurrences, accepted-recipient deduplication, failure/unknown outcomes, worker/scheduler recovery and current exact-role tests. This increment adds full overdue Family/digest recovery, including unmaterialized slots. |
| M4.04: Pause, uncertainty and close | This guide and its [review ledger](stewardship-delivery-pause-reviews.md) record actual restricted-role commands, Family submissions, in-flight retry holds, selective release, exact cancellation, rollback and three-engine browser checks. |
| M4.05: Actual service boundaries | The integration matrix below checks the real compiled registries, queue identities, SQL grants and kernel mounts. Providers are synthetic; no live parish delivery or deployment is claimed. |

The general worker's compiled registry now owns source refresh, token preparation,
cleanup, Family preparation, facts/exports, daily/weekly preparation and completion,
and operational collection/Slack. It has token public keys, never private token
or Workspace credentials. Scheduler admission is metadata-only and consumes no
worker queue. Mail dispatch owns setup/campaign test mail and outbox delivery;
it alone receives token private keys and the Workspace credential. Its bootstrap
exception never silently substitutes a missing configured credential. The
configuration and target credential installers retain their separate authority.

| Integration evidence | What it proves |
| --- | --- |
| `test_runtime_background.py` | Actual compiled registries, key inventories, startup ordering, absent/mismatched credential refusal and fresh authority checks before domain effects. |
| `test_background_grants_postgresql.py` and the delivery/digest database suites | Actual narrow SQL identities: scheduler enqueues but cannot claim; workers prepare but cannot dispatch; MAIL cannot obtain general private records or fabricate provider capability. |
| `test_operational_compose.py` | Non-root commands, real Linux kernel mount admission, bootstrap/complete/abort profiles, restricted SQL, configured consumer recreation and durable state under both development and production-shaped overlays. |
| `test_broker_valkey_container.py`, `runtime_broker_probe.py`, `test_worker_lifetime_postgresql.py` | Real broker queue/ACL behavior and signal handling, plus separately tested SQL claims, independent lease renewal, no new claims after stop and truthful in-flight drain. The broker transport probe intentionally substitutes its domain handler; it is not claimed as a full campaign demonstration. |

CI runs these existing current-functionality checks together with this increment;
do not repeat the full container/database suites locally merely for handoff.
ARC-06.03–.07 and OPS-01.02 remain mixed-phase tasks: target replacement,
backup/escrow, token-key retirement and remaining offline commands are not completed
by enabling mail. OPS-02's existing isolation remains intact; no broader mount
or shared credential was introduced for routing convenience. Full BG-07.04/.05
archive inventory/report parity and ADM-06 restore/reopen/archive/Return still
belong to their documented later owners. Gate 3, Gate 5, real-provider smoke
tests, deployment and release remain separate boundaries.

## Acceptance and delivery boundary

ADM-06.02 and the current-phase M4 demonstrations are implemented and locally
validated. The [review ledger](stewardship-delivery-pause-reviews.md#round-3-and-review-exit)
records all three completed dual-source rounds, accepted fixes, rejections and
regression evidence. No accepted Medium+ finding remains and the final round
contains no High/Critical finding. Earlier incomplete checkpoint notes above
are historical, not the current implementation status.

PR #62 remains subject to final-head CI/DCO and the normal protected merge path.
Standing human merge/continue authority applies once those checks pass; verify
the merge on refreshed `origin/main` before creating the next reporting branch.
Record the merge receipt and M4 release in that successor handoff. Integrated
Gate 3 follows the complete Phase 5 report/RBAC work, not this Phase 4 boundary.

## Protected delivery

[PR #62](https://github.com/epiphany40223/parishkit/pull/62) merged through the
normal protected path at 10:27:25 UTC on September 19, 2026, as
`2c3151e62ee74dad644657d21065ab32a0171346`, verified on refreshed `origin/main`.
Final implementation `101fb6d7b065c21fe525807c18af72e038ed6368` passed all
24 CI jobs in run `35436664988` plus DCO. The aggregate accounted for all
3,936 database tests across 12 shards; stewardship line coverage was 93.88%
and branch coverage was 84.74%. The three-round review exit above is complete.

M4.01–.05 are complete for their admitted fake-backed environment scope.
Phase 5 begins on fresh-main branch `pr/stewardship-report-workspace`; its
[handoff](stewardship-report-workspace.md) retains later report, integration,
production-readiness and deployment boundaries. No live provider, deployment,
release, or integrated Gate 3 approval is implied.
