# Stewardship due-work health

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #56's protected delivery](stewardship-mail-health.md#protected-delivery)
on `pr/stewardship-due-work-health`, based on verified main `96a80fc2`.
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [critical notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Coherent outcome

Connect admitted overdue work and actual scheduler/worker progress evidence to
durable operational alerts and verified recovery. Preserve the bounded fair
scanner, immutable task/occurrence history, exact ownership and campaign gates.
Intentionally held work is not an outage; normal lengthy work is not a lost
worker heartbeat. A missing sample or incomplete scan cannot prove recovery.

Reuse existing notification suppression, critical-log intake and non-recursive
delivery. No new credential-bearing alert daemon or provider probe is implied.
Be explicit about total-outage limits: durable notifications can resume only
when their workers/dependencies can run; broader external operations monitoring
retains its OPS-08 owner. Reuse the existing genuine shutdown/drain evidence
instead of repeating unchanged long-running tests locally.

Finish the current-phase BG-10 acceptance only after these remaining service
observations and their actual-role, interrupted/restarted-work and recovery
checks pass. Then proceed to ADM-05 under the controlling sequence. Backup,
publication, purge, deployment/release and Gate 5 retain their later owners and
approval boundaries. Fresh-install policy still excludes historical upgrades
and grants no permission to delete retained development databases.

## Evidence

Implementation is in progress. No new acceptance checkbox is complete yet.
Focused tests, independent schema audit if needed, three dual-source review/fix
rounds and final-head CI/DCO remain required before protected delivery.
