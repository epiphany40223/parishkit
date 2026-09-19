# Stewardship live delivery pause

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
