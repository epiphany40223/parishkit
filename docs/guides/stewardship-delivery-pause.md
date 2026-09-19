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
