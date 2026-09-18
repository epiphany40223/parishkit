# Stewardship operational health and notification completion

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from verified [PR #50 delivery](stewardship-operational-alerts.md#protected-delivery)
on branch `pr/stewardship-operational-health`, based on `1895949`.
Follow the [controlling Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [critical notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Scope

1. Complete independent optional Slack submission and durable outcomes using the
   existing worker credential mount and fixed private transport. Email absence
   or failure must not prevent this channel; current channel/credential and
   exact Task ownership remain mandatory before provider submission.
2. Complete current-phase health producers and observed healthy-window recovery,
   including existing authentication incidents. Silence or missing cached health
   observations must never manufacture recovery. Later backup, publication and
   purge producers remain with their owning packages.
3. Prove graceful and forced notification shutdown through actual maintained
   execution, finite provider drain and durable recovery. Unknown provider
   outcomes must not become automatic resends or invented success.
4. Retain the requested test-efficiency work as a separate logical checkpoint:
   reduce repeated grant setup and unnecessary full-schema resets where the
   application contract needs only rollback isolation. Preserve every case and
   all real identity, fencing, commit and concurrency tests.
5. Complete the required three dual-source review/fix rounds, exact-head CI/DCO
   and protected delivery before advancing to ADM-05. No Gate 3, live provider,
   deployment, release or historical-upgrade authority is implied.

## Test-efficiency baseline

A focused profile of the six-outcome operational-mail PostgreSQL test measured
14 restricted-role contexts taking 0.803 seconds within its 2.12-second test
body; column admission accounts for 0.193 seconds of those contexts. The
instrumented fresh schema bootstrap took 22.71 seconds separately and is not
comparable to uninstrumented timings. Role/grant setup is repeated, whereas
database schema creation is already session-scoped. Measure any improvement
against the same application test before claiming a saving.

The current whole-project acceptance remains incomplete. This guide records
checkpoints as they pass; it does not mark BG-10 complete merely because its
email predecessor merged.
