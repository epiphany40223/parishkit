# Stewardship go-live readiness and cleanup

Continue [ADM-05](../tasks/stewardship/admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal)
from [PR #57's protected delivery](stewardship-due-work-health.md#protected-delivery)
on `pr/stewardship-go-live-readiness`, based on verified main `17f5f2fc`.
Follow the [controlling Phase 4 sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [Production-transition contract](../specs/stewardship/admin-portal/spec.md#production-transition).

## Coherent outcome

Give an authenticated Admin a current, exact readiness/impact preview and a
guarded path into the existing bounded Testing-cleanup worker, with durable
status, safe retry and cancellation. Reuse configuration, source, test-delivery,
coalescing, cleanup inventory and request owners; a signed preview alone is
never authorization or proof of current readiness. Passive status must not
renew an abandoned login. Staff and Ministry leaders have no access.

Keep the irreversible cleanup acknowledgement explicit, invalidate rehearsal
access with gate acquisition, and preserve completed cleanup on cancellation.
No endpoint may nominate arbitrary deletion targets, inventory counts, actor
identity or privileged callbacks. No GET sends a message or starts cleanup.

This increment stops before Production activation/withdrawal. Those later
ADM-05 commands must recheck current readiness under short final locks and
exercise the required direct-activation load/catch-up handoff. Keep their SQL
and application guards closed until that owner is complete. This is a
reviewability split, not removal of ADM-05.03/.04/.05 or Gate 3 acceptance.

## Checkpoints

1. Current readiness, exact impact/inventory and stale-input binding, including
   explicit blockers and existing remediation links.
2. Admin preview/acknowledgement, real cleanup request/gate integration, progress,
   retry/cancel and response-time authorization checks.
3. Actual-role, stale input, boundary, interrupted cleanup and browser acceptance
   tests; independent fresh-schema audit if the schema changes.
4. Three completed dual-source review/fix rounds, final-head CI/DCO and protected
   merge before the next fresh-main activation/withdrawal increment.

Implementation is in progress; no ADM-05 checkbox is complete yet. Tests use
synthetic providers and disposable databases. No real provider call, retained
database deletion, deployment, release or historical upgrade is authorized.
