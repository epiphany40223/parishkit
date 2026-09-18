# Stewardship operational health and notification completion

Continue [BG-10](../tasks/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown)
from verified [PR #50 delivery](stewardship-operational-alerts.md#protected-delivery)
on branch `pr/stewardship-operational-health`, based on `1895949`.
Follow the [controlling Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [critical notification contract](../specs/stewardship/background-processing/spec.md#critical-errors-and-notification).

## Scope

Delivery subdivision for review cost: PR #51 owns independent Slack, its
notification-shutdown evidence, the adjacent admission-hold corrections and
the separate fixture-efficiency checkpoint. Remaining current-phase health
producers and healthy-window recovery follow in a fresh successor PR from
verified main. This keeps an independently testable channel boundary without
mixing a new health-sampling contract into the same review. The overall BG-10
scope below is unchanged; no task is marked complete prematurely.

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

## Compatible fixture checkpoint

The four shared web, Task, configuration-installer and credential-target role
helpers now use a common fixture-only grant compiler. It groups tables only when
their complete privilege sets match and retains each table's exact column lists.
One short setup transaction replaces per-GRANT commits. Each context still
creates its own role, uses actual session authorization, handles reconnects,
checks admission where previously required, and destroys only that fixture role.
There is no cross-test role cache or production permission change.

The same profiled six-outcome mail test passes with 2,490 SQL executions instead
of 3,559. Its 14 role contexts fall from 0.803 to 0.480 seconds and its test body
from 2.12 to 1.71 seconds. These are local representative measurements, not a
whole-suite or CI saving claim. Both profiles use identical application code;
only current-worktree fixture helpers differ. The editable local test install
is then rebound to this worktree before subsequent implementation changes.

Six validation-only test families now override their mixed modules' transaction
marker with rollback isolation: member-source and boundary diagnostic privacy,
invalid coverage manifests, operational event vocabulary, credential-consumer
vocabulary and sealed key-ID grammar. All parameter cases remain; neighboring
real-commit, out-of-transaction rejection and concurrency checks retain full
transaction semantics. All 227 tests across the eight affected helper/contract
modules pass together in 70.67 seconds with one schema bootstrap. Ruff passes.
Full CI remains required for the wider shared-helper consumer set.

The pushed checkpoint `7b9735e` passes all 24 CI jobs plus DCO in run
`35305933366`. PostgreSQL shards take 10m31s–15m37s individually, compared with
the predecessor's slowest 18m34s shard. Runner scheduling still stretches the
whole run to approximately 21 minutes; those queue delays and the remaining
test costs are not claimed as solved by this change.

## Independent Slack checkpoint

The general Worker now owns optional operational Slack delivery separately
from the mail cohort and isolated MAIL consumer. One stable Task per notice
retains intent; append-only submission/result records pin current channel,
configuration, mode, credential fingerprint and exact worker fence. A committed
35-second provider/drain window precedes private external IO. Accepted and
uncertain outcomes never authorize automatic resend; only definitive non-send
may retry within the bounded preparation budget. Channel/configuration holds
do not spend that budget. Both scheduler and Worker retain metadata recovery
even when the optional credential mount disappears.

SQL independently rejects forged notice, Task, fence, worker, actor,
configuration, channel, fingerprint and mode values. Result ownership,
immutability and recovery deadlines are also SQL-enforced. Failure records are
ERROR/WARNING, never recursive CRITICAL notifications. No real Slack or email
provider is contacted by tests.

Eight initial PostgreSQL cases pass in 51.42 seconds, including one real
35-second forced-drain deadline test, direct SQL forgery probes, all three
provider outcomes during graceful stop, independent delivery without email,
and configuration/channel holds. Runtime registry/producer checks pass all
226 cases in 1.11 seconds. The fresh baseline/model/grant/routing group passes
48 cases in 30.57 seconds; Django detects no model-state changes.

### Fresh-install schema audit

Independent fresh PostgreSQL databases install immutable predecessor `1895949`
and this checkpoint. The predecessor matches its committed fingerprint.
Per-object comparison adds only two Slack tables, 20 columns, 32 constraints,
nine indexes, three functions and four triggers. Every preexisting object is
unchanged, including all 28 policies. The new fingerprint records 182 relations,
2,094 columns, 2,976 constraints, 898 indexes, 510 functions and 487 triggers.
The model/SQL equivalence checks pass. No retained database is changed, deleted,
upgraded or downgraded; this remains the unreleased fresh-install baseline.

The additional preparation-exhaustion case passes in 15.26 seconds including
schema bootstrap. It confirms five real local failures terminate and produce
one durable ERROR, with no provider call or recursive notification.

Current-phase health producers/recovery remain in the successor slice. The
three required dual-source review rounds are still pending. This is a backed-up
implementation checkpoint, not BG-10 acceptance or Gate 3 approval.

## Admission-hold integration

Configuration mismatches no longer abort a mixed scheduler hint page, including
ordinary authority-wrapped owners; SQL/transport outages still abort normally.
Operational and Family mail classify pre-submission authority loss as a hold.
Mail and operational fanout reset their attempt phase before actual preparation,
so a previous held attempt cannot exempt subsequent real failures forever.
Regression tests use the actual composite MAIL owner, scheduler and PostgreSQL
journals; only normal retry delays are shortened, not leases/provider deadlines.
All 50 focused hold, hint-scan, operational dispatch/fanout and Family-worker
PostgreSQL cases pass together in 89.73 seconds with one schema bootstrap.
