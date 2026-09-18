# Stewardship source health

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from [PR #54's protected delivery](stewardship-ci-bootstrap-reuse.md#protected-delivery)
on `pr/stewardship-source-health`, based on verified main `17d5d4d9`.
The [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
continues to require three completed dual-source rounds and exact-head CI/DCO.

## Coherent outcome

Deliver source failure/staleness observations and actual-success recovery,
including specific wrong-tenant and unexpected-data-loss classification. Keep
provider/due-work service observations, remaining BG-10 acceptance and ADM-05
explicitly open. No actual ParishSoft calls or production-readiness work is
authorized by this increment.

Reuse the existing minute-keyed, task-fenced operational collector for periodic
source observations. The scheduler allocates opaque work; only the general
worker records incidents. Each sample and its completed Task commit together.
Do not introduce another scheduler, provider probe or mutable health cache.

Configured freshness defaults to thirty minutes, allowing the fifteen-minute
refresh cadence and normal two-to-three-minute full load. Intentional restore
or purge holds produce neither failure nor recovery. A successful current-scope
snapshot is required for recovery; task completion, old-window snapshots,
elapsed cooldowns and unobserved state cannot establish health. Delayed critical
log intake must finish before resolving its source incident, and a newer failed
read must prevent an older successful snapshot from clearing that incident.

Set `deployment.operational_alerts.source_stale_seconds` in YAML; the equivalent
environment setting is `PARISHKIT_STEWARDSHIP_OPERATIONAL_SOURCE_STALE_SECONDS`.
Values are integers from 60 to 86,400 seconds. Freshness uses the current
snapshot's pre-read start time, not its later promotion time; before the first
snapshot, the runtime configuration creation time anchors the initial grace.
Changing the threshold does not rewrite existing suppression/escalation policy.
Delta ambiguity still requests its existing full fallback; definitive full-load
loss and actual tenant mismatch produce the specific critical classification.

## Fresh-install schema audit

Independent empty PostgreSQL databases installed immutable base `17d5d4d9`
and this increment. Complete catalog comparison found exactly two changes:
`operational_event_safe` accepts the two new diagnostic literals, and
`stewardship_ops_log_receipt_binding_v1()` maps each to its matching incident.
Full definitions were compared after removing precisely those additions; all
other objects, permissions, indexes and model state are unchanged. Counts stay
182 relations, 2,098 columns, 2,979 constraints, 898 indexes, 510 functions,
487 triggers and 28 policies. Only the two audited fingerprints were updated.
No retained database was altered, upgraded or deleted.

## Evidence

The initial checkpoint passes 272 focused source/deployment/shared-client tests
and 91 real PostgreSQL checks covering source health, collector ownership, read
failure settlement, SQL event admission and the audited catalog fingerprint.
That PostgreSQL group takes 39.87 seconds; lease/provider deadlines are unchanged.
Coverage includes actual restore admission, bounded delayed-log pages, rollback
after notice creation, current-window proof and a concurrent newer failure.
Existing scheduler/general-worker database identities execute the actual
periodic producer/consumer tests. Full exact-head CI runs on the draft PR,
alongside three independent dual-source reviews, rather than being repeated
locally. These delivery requirements remain pending at this checkpoint.
