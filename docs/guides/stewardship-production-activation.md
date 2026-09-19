# Stewardship Production activation and withdrawal

Continue [ADM-05.03/.04/.05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #58's protected delivery](stewardship-go-live-readiness.md#protected-delivery)
on `pr/stewardship-production-activation`, based on verified main `47ec3599`.
The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
owns behavior; the [Phase 4 implementation plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
owns dependencies, review gates and the synthetic/disposable-only restriction.

## Intended coherent outcome

An Admin can finish a cleaned-up campaign's go-live process with fresh Google
authentication and explicit typed confirmation. Prepare inactive Family links
as bounded background work before the short final transaction; never enumerate
Families or construct their messages while holding final activation locks.
Preserve stable manual Family codes. Select the prepared generation atomically
with the campaign state, global mode, cleanup request and gate release.

Before the start instant, the result is scheduled. Inside the half-open campaign
interval it is active, with one durable catch-up demand/task and visible mail
preparation progress. At or after close, confirmation fails without a partial
mode change. Only scheduled pre-start campaigns may be withdrawn through the
reasoned, freshly authenticated workflow. Completed Testing deletions remain
irreversible; a later go-live attempt needs fresh evidence and cleanup.

## Implementation checkpoints

1. Bind resumable general-worker token preparation to completed cleanup, current
   source/population/configuration, credential epoch and public-key inventory.
   Provide current-Admin progress, retry/cancel, stale-input rejection and safe
   inactive-generation disposal. Reuse the maintained task runtime and existing
   token-generation primitives; do not expand private-key mounts or turn the
   deletion-only cleanup worker into a Production writer.
2. Build post-cleanup readiness and exact impact preview, using the immutable
   pre-cleanup aggregate only for intentionally deleted test evidence. Establish
   complete version/as-of guards for short final confirmation, not just a tuple
   that omits delivery, coverage, hold or newly due work changes.
3. Implement final confirmation and atomic scheduled/direct-active activation,
   current/fresh authorization, current readiness, boundary checks and existing
   catch-up integration. Keep submission availability separate from mail holds.
4. Implement guarded pre-start withdrawal and future-work cancellation, blocking
   provider-submitting/uncertain effects and losing safely to the start boundary.
5. Exercise actual restricted SQL roles, interrupted preparation, stale inputs,
   role/session revocation, concurrent confirmations and boundary/withdrawal
   races. Measure the final transaction at 5,000 Families and verify catch-up
   recovery plus responsive submissions. Include no-JavaScript and three-engine
   mobile/keyboard/accessibility acceptance.
6. Complete three dual-source review/fix rounds, final-head CI/DCO and protected
   delivery before the delivery-pause slice. Audit any fresh-schema change
   independently against the immutable predecessor. Gate 3 remains separate.

## Current status

Investigation and implementation are in progress; none of ADM-05.03/.04/.05 is
complete. Existing token-generation storage has no runtime preparation caller,
so that dependency is included before opening activation authority. No real
provider calls, retained-database deletion, deployment, release or historical
upgrade compatibility is authorized by this work.
