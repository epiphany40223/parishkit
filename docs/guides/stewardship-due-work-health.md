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

Implementation, focused tests, independent schema audits and three dual-source
review/fix rounds are complete. Final-head CI/DCO remains required before
protected delivery. The current-phase acceptance map below reconciles the
preceding notification/shutdown increments without claiming later-phase work.

## Implemented observation boundary

The existing singleton scheduler records one bounded health checkpoint using its
existing SQL connection. Only freshly admitted tasks contribute overdue evidence:
queued/retry work uses its due time; expired running work uses lease expiry;
abandoned work uses its recovery transition time. A maintained, live task is
not overdue merely because its original queue time is old. Held or unreadable
work makes a scan inconclusive, not healthy. Unknown task types are outside the
compiled registry's observation scope and never dynamically loaded.

Lateness begins after the existing 90-second scheduler-health threshold. It
must remain unresolved across negative observations for the configured operational
escalation window (default 15 minutes). SQL derives these times and retains a
critical log at most once per minute while the condition persists, even if the
worker/collector is unavailable. Existing intake receipts, episode suppression
and notification routing consume these fixed-content logs when workers return.
No per-tick history, additional daemon, thread, connection or provider probe is
introduced. Healthy idle scans do not allocate collector tasks.
Unchanged checkpoints are written at most once per 30 seconds; a changed verdict
is immediate. Negative observations retain unresolved lateness across gaps of
at most five minutes, allowing slow scans but not bridging a prolonged outage.
Inconclusive scans invalidate positive proof but neither erase nor renew prior
negative evidence. They never emit failure logs by themselves. A new negative
observation after a longer gap starts a new escalation window, without resolving
an existing incident. The write throttle does not skip the serialization lock;
each observation still checks ownership and serializes with the collector.

Recovery requires complete successful sweeps over five minutes with no gap over
90 seconds, no held/broken records, and no unconfirmed hint publication. Partial
or overlong sweeps cannot certify recovery. The collector serializes with the
checkpoint writer, requires fresh evidence and checks that no newer or
unconsumed lag log can be overtaken. All sampling times are PostgreSQL UTC.

This is due-work health, not an independent machine/process monitor. A total
scheduler/database outage cannot self-report while its observation process is
absent; existing local health checks and later OPS-08 external monitoring retain
that boundary. The current BG-10 acceptance review remains open until the full
current-phase producer/shutdown evidence is reconciled.

## Fresh-install schema audit

Independent empty databases install immutable main `96a80fc2` and this baseline.
The delta adds one eight-column checkpoint table, eleven constraints, one index,
three functions and one trigger. Existing objects are unchanged except the
single `due_work_lag` diagnostic admission and its receipt classification.
Counts are 183 relations, 2,107 columns, 2,991 constraints, 901 indexes,
516 functions, 490 triggers and 28 policies. Owners and ACLs are included.

The strict model/schema comparison caught a literal-array cast rendering
difference in the new signal check. Correcting its fresh-install definition
changed exactly that one new constraint, independently verified before updating
the fingerprint. No retained database was upgraded, reset or deleted.

Initial focused validation: 28 scanner/collection/new-health database tests
passed in 19.65 seconds; 11 health/scheduler-process tests passed in 14.93
seconds. Ruff check and formatting pass; Django reports no migration-state
drift. Strict schema revalidation and the review/delivery cycle remain pending.

## First review and corrections

Round 1, `20260918-114701-d624d1`, independently reviewed `96a80fc2` through
`f50994da` (tree `edf79ad5`). Both vendors completed; raw findings were five
Medium and nine Low, with no High/Critical and no degraded source.

Three Medium findings are corrected:

- The actual scheduler SQL trigger now has a regression test proving its
  CRITICAL log, `last_failure_at`, minute throttle and fenced collector intake.
  Only fixture setup ages the prior checkpoint with its guard temporarily
  disabled inside a transaction; all guards are restored before the real role
  performs the tested action. No production clock override or long sleep exists.
- Slow negative samples retain an unresolved late window; positive samples
  still require gaps of at most 90 seconds. Round 2 below refines the treatment
  of inconclusive samples and bounds negative gaps; old negative evidence can
  never prove recovery.
- Recovery returns before reading the checkpoint or log history when no
  scheduler-lag incident is active. A query regression verifies this idle path.

Two Medium suggestions are not adopted, with explicit policy rationale:

- Holds remain inconclusive. This global episode has no retained task-specific
  cause, so a hold on the failing work must not produce a false resolved notice.
  An unrelated long-lived hold can consequently delay resolution until release.
  It does not open an incident or generate repeated failure notices by itself.
- Sustained backlog is intentionally alertable as **scheduled work overdue**,
  not proof of a missing worker. The default window is 15 minutes after the
  initial 90-second lateness threshold; a normal 2–3 minute source refresh does
  not satisfy it. Busy workers do not make long-unserved admitted work timely.
  Reducing the configured escalation window deliberately makes this stricter.

Low findings: bound checkpoint lock/statement waits to two/five seconds; assert
late observation does not mutate an episode; document the SQL/Python 90-second
coupling; reuse the per-row SQL instant; explain defensive critical-boundary
recovery fencing; preserve fair cursor progress while invalidating interrupted
health proof; throttle unchanged checkpoint writes; and prove serialization
using real scheduler/worker connections. Keep the local ownership check in
`finish`: it protects callers independently of `scan_once` and SQL remains the
final defense. No unresolved accepted Medium-or-higher finding remains.

Focused correction validation: 18 new-health/scheduler-process database tests
passed in 16.89 seconds. The independent schema re-audit changes only the
negative-continuity function; all counts, other definitions, owners and ACLs
match. Strict schema/scanner revalidation passed all 25 cases in 22.66 seconds.
The remaining rounds and final-head CI/DCO remain required. Initial head
`f50994da` passed all 24 CI jobs and DCO in run `35364532026`; this does not
substitute for final correction-head CI.

## Second review and corrections

Round 2, `20260918-131337-ca3719`, reviewed the correction delta `f50994da`
through `0702c4f9` (tree `389f8d9b`) with full observation/collector context.
Both vendors completed without degradation: three Medium and eight Low raw
findings, no High/Critical.

All three Medium findings are corrected together: inconclusive prefixes and
scheduler interruptions preserve, but never refresh, existing negative evidence;
negative continuity has a five-minute maximum gap. This avoids both suppression
by repeated held prefixes and immediate escalation after hours without scans.
Actual-role regression coverage verifies no hold-generated log, unchanged
negative timestamps, subsequent real escalation, and exact gap boundaries.

Low dispositions:

- Added process-loop regression coverage for retained non-null cursors, failed
  suffix retries, invalidated proof, and failure of the checkpoint write itself.
  This addresses the overlapping request from both reviewers.
- Replaced the brittle idle query count with assertions against the specific
  expensive table reads. Corrected an inaccurate fixture comment.
- Documented that the 30-second write throttle does not eliminate lock attempts.
- Retain bounded lock-timeout failure reporting/retry: the observer cannot claim
  success after failed persistence, and replayed hints are already idempotent.
  Extra error messages during an unavailable database are bounded diagnostic
  noise, not extra CRITICAL notices. No new special-case exception classifier.
- Do not duplicate PostgreSQL's timeout implementation with wall-clock tests.
  The application sets transaction-local two/five-second limits; the existing
  actual-role contention test covers its lock interaction. Its start event is
  already set after scheduler ownership is established, contrary to the flaky
  startup claim; the worker lock is released before awaiting completion.

The independent fresh-install audit changes exactly the two due-work timing
functions. Other definitions, counts, owners and ACLs match the prior audit.
No retained database was changed or deleted. All 38 focused due-work, process-loop
and strict schema cases passed in 27.21 seconds; Ruff and guide lint pass. The
third review round and final-head CI/DCO remain pending.

## Third review and delivery handoff

Round 3, `20260918-132705-8db88e`, reviewed `0702c4f9..34942e7c` (tree
`c9fdc5bb`) with the surrounding scanner, collector and actual-role contracts.
Both vendors completed successfully: zero Critical/High/Medium and one raw Low.
The Low is corrected by moving the suffix-proof assertion outside the scheduler
exception handler, so a regression reports its actual failed invariant.
No accepted Medium-or-higher finding remains. All five scheduler-process cases
pass in 11.64 seconds after the correction; Ruff, formatting, changed Markdown
and diff checks pass. Consolidated-head CI/DCO still controls protected delivery;
the PR handoff records its exact receipt without a receipt-only CI push.

The feature and review fixups are consolidated into one signed commit, separate
from the predecessor delivery/scope receipt. Preserve the correction checkpoint
and verify identical trees after consolidation. The successor records verified
merge delivery and begins ADM-05 from refreshed `origin/main`, not this branch.

## Current-phase BG-10 acceptance map

The Phase 4 notification/shutdown subset is implemented. Its delivery remains
conditional on this PR's final exact-head checks; Gate 3 is not released here.
Earlier pending notes in predecessor guides are superseded by their protected
delivery receipts and this map, not by a claim that every later producer exists.

| Task/scope | Implementation and executable evidence |
| --- | --- |
| BG-10.01, current-phase critical intent | [Typed intake/episodes](stewardship-operational-alerts.md#protected-delivery), [authentication](stewardship-periodic-health.md#protected-delivery), [source](stewardship-source-health.md#protected-delivery), [mail](stewardship-mail-health.md#protected-delivery), and this due-work trigger; actual-role and atomic rollback tests |
| BG-10.02, Admin email and optional Slack | [Email delivery](stewardship-operational-alerts.md#protected-delivery), [independent Slack](stewardship-operational-health.md#protected-delivery); current recipients/configuration, Testing exception, private content and unknown-outcome tests |
| BG-10.03, current-phase suppression/recovery | SQL-derived episode policy, exactly-once intake, periodic authentication proof, successful source/provider evidence and this bounded queue recovery; stale/held/pending-receipt/concurrent-failure regressions |
| BG-10.04, graceful scheduler/worker shutdown | `test_worker_lifetime_postgresql.py` proves no new claims/effects after stop, maintained independent renewal, lost-fence rejection and safe completion; `test_scheduler_process_postgresql.py` proves singleton loss, mid-page stop and interrupted fair progress |
| BG-10.05, notification failure/interruption | `test_operational_dispatch_postgresql.py` covers lost acknowledgement and abandoned delivery; `test_operational_slack_postgresql.py` covers all graceful outcomes, actual forced-drain deadline, independent channel failure and no ambiguous resend; these remain in full CI |

BG-10.01/.03 remain unchecked for their explicitly later backup-RPO (OPS-05),
publication-ambiguity (BG-09), and purge-inconsistency (BG-11) producers/recovery.
Generic typed CRITICAL intake already classifies other system/integrity and
Production-cleanup failures. A total SQL/observer outage cannot persist or send
its own notification while unavailable; OPS-08 owns external monitoring and
runbooks. Historical upgrade compatibility remains excluded by pre-production
policy. No deployment, live provider write or release is authorized by this map.

After protected delivery, ADM-05 readiness/cleanup/activation/withdrawal is the
next dependency-ready package. Its direct-activation load/recovery handoff and
later delivery-pause/Gate 3 integration checks remain mandatory.
