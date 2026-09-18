# Stewardship mail-provider health

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #55's protected delivery](stewardship-source-health.md#protected-delivery)
on `pr/stewardship-mail-health`, based on verified main `7b5c43b6`.
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [critical notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Coherent outcome

Connect actual campaign-mail provider outcomes to durable critical notification
and verified recovery. Systemic failures alert immediately; three consecutive
observed unavailability results for the same provider configuration also alert.
Persist critical intent atomically with the owning outbox result so interruption
or delayed collection cannot lose a short-lived outage. Existing process-local
delivery circuits remain admission controls, not durable notification evidence.

Recovery requires an actual healthy SMTP observation against the current
provider configuration, begun after newer failed observations. Recipient refusal
can still establish healthy transport; unknown or unobserved outcomes cannot.
Consume pending critical receipts before resolving, preserving one recovery
notice and immutable history. Operational-email failure must not recursively
generate email-failure notifications. No new provider probe or external write
is authorized; tests use synthetic transports and real PostgreSQL ownership.

Due-work service monitoring and the final current-phase BG-10 acceptance remain
the following increment. This independently testable mail-outcome boundary keeps
review scope separate from cross-process scheduler/worker health observation.
No gate, deployment, release or production-readiness approval is implied.

## Evidence

Implementation and three dual-source review/fix rounds are in progress. Do not
mark BG-10 complete until all remaining current-phase owners pass acceptance.
