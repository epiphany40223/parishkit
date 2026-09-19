# Stewardship pre-start Production withdrawal

Continue [ADM-05.04/.05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
after [PR #60's protected delivery](stewardship-production-confirmation.md#protected-delivery).
Branch `pr/stewardship-production-withdrawal` starts at verified `origin/main`
`6bc3238`. The [Production-transition specification](../specs/stewardship/admin-portal/spec.md#production-transition)
and [ADM-05 implementation package](../plans/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
control this increment; the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
controls validation, reviews and protected delivery.

## Increment boundary

Deliver the current-Admin pre-start withdrawal workflow: fresh Google
authentication, reason and irreversible-cleanup acknowledgement; exact future
live-work inventory; safe cancellation and blockers; atomic return to draft and
Testing with structural unlock, readiness invalidation and immutable audit.
Reject after start even when the boundary worker is delayed. Preserve all prior
Testing cleanup and activation history. Repeated go-live must use a new complete
readiness/test/cleanup/authentication cycle, not revive former evidence.

## Checkpoints

1. Inspect the lifecycle, reconciliation and credential/boundary owners; reuse
   their cancellation and uncertainty rules through a narrow durable command.
2. Implement current-session, version/time-bound preview and atomic withdrawal,
   with exact replay and no arbitrary web lifecycle/outbox mutation authority.
3. Expose native accessible forms and clear blocked/success states. Link only
   from eligible current scheduled campaigns; never imply cleanup is restored.
4. Test exact roles, CSRF/stray input, stale readiness, failed/unknown delivery,
   rollback, competing confirmations, withdrawal/start races and repeated go-live.
   Audit only a fresh schema baseline and preserve all retained databases.
5. Complete three dual-source review/fix rounds, focused validation and final-head
   CI/DCO, then protected delivery. Delivery pause and Gate 3 remain later work.

No task is completed by this initial checkpoint. No real-provider writes,
deployment, release or retained database deletion are part of this increment.
