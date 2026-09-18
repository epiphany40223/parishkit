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

Configure the threshold through the shared [operational policy settings](stewardship-operational-alerts.md#operational-policy-configuration).
Freshness uses the current
snapshot's pre-read start time, not its later promotion time; before the first
snapshot, the earliest non-bootstrap activation of the configured organization
anchors the initial grace; unrelated configuration edits cannot restart it.
Changing the threshold does not rewrite existing suppression/escalation policy.
Delta ambiguity still requests its existing full fallback; definitive full-load
loss and actual tenant mismatch produce the specific critical classification.
Destructive-loss recovery additionally requires a successful full refresh begun
after the failure; a later delta may retain that exact full-refresh anchor.
Source sampling runs in a savepoint: failure rolls back its partial effects and
leaves a safe critical diagnostic, without discarding unrelated intake receipts.
The outer Task transaction still owns successful sample and intake completion.

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

## Review corrections

Round 1 (`20260918-091321-8f4fb2`, `17d5d4d9..0d339cf`) completed both vendors:
four Medium and six Low raw findings, no High/Critical. Accept all four Medium
findings: commit the incident before the concurrency test's later failure and
prove the sampler actually waits on the work-order lock; include terminal
CRITICAL `SOURCE_HELD` records in recovery fencing while ordinary INFO holds
remain non-failures; anchor initial grace to actual configuration activation;
and distinguish intentional admission holds from invalid configured source scope.
Invalid configured scope now produces a safe critical log inside the sample's
transaction, consumed by the next collector page. Missing technical-bootstrap
source configuration is not an outage. Repair alone cannot clear an incident
using source data read before that failure.

Also accept the Low malformed-organization classification correction and both
reviewers' overlapping delta-path test requests. A malformed response is invalid
source data, not proof of another tenant. Tests pin feed mismatch propagation and
ambiguous slice fallback. Avoid history queries entirely when no source incident
is open; an open incident still requires exact pending receipts, not a time cursor
that could skip delayed intake. Existing sibling private scope helpers remain
unchanged in naming; a public API refactor is unnecessary for their existing
internal contract.

Periodic collection retains up to 1,440 sample Tasks per day plus their normal
fenced history. [OPS-07](../tasks/stewardship/operations.md#ops-07-housekeeping-and-retention-jobs)
owns later retention/housekeeping; this increment does not silently prune durable
evidence or claim that cleanup is already implemented. No additional scheduler
or mutable health table is introduced.

Post-correction validation passes 19 focused real PostgreSQL source-health
tests in 29.88 seconds and 177 source-adapter/shared-client tests in 0.33 seconds.
The initial exact-head CI run remains separate evidence; corrected-head CI and
the next two completed review/fix rounds are required before delivery.

Round 2 (`20260918-092735-27fc1d`, `0d339cf..9edc91c`) completed both vendors:
one Medium and six Low raw findings, no High/Critical. Fix the Medium initial
grace reset using the earliest actual activation for the configured tenant,
with an unrelated configuration-edit regression. Also prevent a looser stale
threshold from resolving an incident without a later successful promotion.

Accept four Low corrections: compare positive numeric tenant evidence before
optional descriptive-name validation; test that a receipted terminal failure
still blocks recovery against an older success; use the shared work-lock key
in the concurrency assertion; and distinguish configuration failures with safe
action/version context, without adding another event or logging private values.
Document the remaining Low growth concern: prolonged invalid configuration adds
up to 1,440 CRITICAL logs and receipts per day, in addition to sample Tasks.
Repeated observations preserve the latest failure timestamp; incident notices
remain suppression-controlled. OPS-07 owns retention, not this observer.

Defer the Low request to repeat integration reinstallation in the health test:
actual missing-integration intake is covered here, configuration-repair recovery
is covered for tenant mismatch, and generic source-failure recovery is covered
for provider and terminal failures. Reinstallation must continue through the
credential installer, not a health-test bypass of credential ownership.

Post-round-2 validation passes all 21 real PostgreSQL source-health tests in
32.92 seconds and 88 focused source/shared-client checks in 0.18 seconds.
The third dual-source round and consolidated-head CI remain required.

Round 3 (`20260918-094018-0b2213`) reviews the complete consolidated
`17d5d4d9..5eb149c` diff (tree `58e4d576b7ce7c87e1690019134c05156a5ead43`).
Both vendors completed successfully: two Medium and six Low raw findings,
no High/Critical, failures, degradation or verdict mismatch. Fix both Medium
findings: destructive-loss recovery requires a later successful full anchor,
including when a newer delta is current; isolate source sampling in a savepoint
so a persistent sampler exception cannot block unrelated critical intake.
Tests exercise real delta/full promotion, repeated sampler failures including
an actual SQL error, partial-effect rollback and outer completion rollback.

Accept the Low INFO-hold test and canonical operator-reference updates. Retain
the deliberately once-per-tenant initial grace across A/B/A edits: switching
away and back is not successful refresh evidence or a fresh grace budget.
Retain the old-runtime regression, which would fail if code reverted to using
runtime birth; it tests independence from that obsolete input, not its use.
The explicit optional expected-name guard remains a strict identity assertion
alongside numeric identity, preserving the shared client's configured contract;
missing names remain invalid, while conflicting explicit assertions fail closed.
Decline the Low cosmetic guard-nesting suggestion: the adjacent shape and value
checks are short and independently describe their classifications.

All 34 source-health and collector PostgreSQL checks pass together in 35.75
seconds with one schema bootstrap; Ruff, formatting, Markdown and diff checks
pass. No SQL definition or permission changes follow the recorded schema audit.
These corrections are part of round 3, not an incomplete fourth round. The
controlling delivery cycle requires passing correction tests and final-head
CI/DCO before protected merge. The PR handoff records those final receipts;
the successor records verified delivery without a receipt-only CI rerun.

## Protected delivery

PR [#55](https://github.com/epiphany40223/parishkit/pull/55) merged as
`7b5c43b64f38a550bfb3cae6a62f66dd75266ad3` at 14:07 UTC on September 18, 2026,
verified on freshly fetched `origin/main`. Final head
`9ab4814ddce6d3d3a087305049605b4cc7936fe1` passed all 24 CI jobs in
run `35352549899`, plus DCO. All 3,812 database tests were accounted for across
12 shards; combined coverage was 93.92% line and 84.90% branch. The whole run
took 14 minutes 56 seconds, including scheduling; the slowest PostgreSQL
partition took 12 minutes 22 seconds. Two logical signed-off commits landed
through normal protected merge, without a queue or protection bypass.

The [mail-health successor](stewardship-mail-health.md) starts from that verified
main tip. Broader BG-10 acceptance, due-work monitoring and ADM-05 remain open.
