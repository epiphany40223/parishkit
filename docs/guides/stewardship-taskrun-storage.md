# Stewardship TaskRun storage foundation

This delivers DAT-01.05's base record/claim metadata and retry-chain foundation.
The [data contract](../specs/stewardship/data/spec.md#job-outbox-audit-and-purge-records)
and [canonical task transitions](../specs/stewardship/background-processing/spec.md#durable-scheduling-and-task-execution)
remain authoritative. [Task evidence](../tasks/stewardship/data.md#dat-01-storage-conventions-and-base-records)
and [review milestones](../tasks/stewardship/milestones.md#taskrun-storage-increment)
track this bounded Phase 1A increment.

## Identity and history

The initial TaskRun is its own protected retry root; that root UUID is the
logical-operation identity. Its optional execution idempotency key is opaque
UUID text. The same task-type/key cannot create another execution, even after
terminal completion. A repeated enqueue checks the original domain-request and
initiator binding and returns that original run, not a later retry.

An explicit retry targets the latest failed run, creates a new linked run under
the root lock, and uses `retry:<root UUID>:<sequence>` as its execution key.
The sequence increases monotonically, the domain-request UUID and task type
remain unchanged, and a partial unique constraint admits at most one nonterminal
run per chain. Retry-command UUIDs are permanently bound to a parent and initiating
actor; exact repeated commands return the allocated run's current state.
Failed originals are never reopened. Automatic retries instead reuse their run
and increment the attempt number on the next claim.

TaskRunEvent records every version with previous/new state, coded action/reason,
numbered attempt, fence, worker/actor/correlation UUIDs, UTC timestamp, heartbeat,
lease deadline, retry time and progress counters. Claim events begin attempts;
outcome/expiry events preserve their endings. The database inserts the event and
a safe AuditEvent in the same transaction as each task write. SQL and ORM guards
reject rewritten event history; direct event INSERTs must match the task and its
next history version. New functions use trusted schema resolution and explicit
public audit targets, including when callers create temporary shadow tables.

## Internal API boundary

`jobs.storage.enqueue`, `retry_failed` and `change_run` are internal transaction
primitives, not web endpoints, queue consumers or authorization services. Every
call requires a trusted admission callback, including exact command retries.
The callback receives an immutable TaskStatus and must raise on failed current
authorization, configuration, lifecycle, restore/purge or task-specific safety
checks. It runs under the transaction/root lock and must not perform network,
filesystem or provider side effects. It may compose related database writes;
any exception rolls those back with task state/history.

For a prospective enqueue the callback sees the generated operation/type/request
identity before insertion. Existing runs serialize independently under their root
row lock. Enqueue allocation uses one short deployment-level transaction advisory
lock to handle absent-key races. Database uniqueness remains the final arbiter
for direct SQL callers. No untrusted operational caller or permissive production
callback is wired by this increment.

Worker mutations require the expected row version, owner UUID and current fence.
Claims increment attempt and fencing values; heartbeat renews a live lease.
Lease expiry permits only abandonment and advances the fence to invalidate the
old claim. Abandoned work stays nonterminal until the owning callback proves a
safe retry, completion, failure or cancellation. Expiry is never evidence that
an external operation failed, and generic task success never implies semantic
mail delivery or fulfillment.

All timing uses PostgreSQL's clock. Internal claim/heartbeat durations are bounded
to 1–300 seconds and retry waits to 1–86,400 seconds; BG-01 will select defaults
and renew leases for long jobs. Progress is a monotonic bounded `(current, total)`
pair. These records accept no task argument payload, free-form summary/error,
credential, URL, submitted value or provider response. Coded actions provide
initial outcome/reason information; BG-01/DAT-07 own registered task-specific
phases, sanitized summaries/errors and operational status presentation.

## Migration and validation

The jobs schema and guards use separate ordered migrations, matching existing
storage conventions. Empty databases can reverse and reapply both migrations.
Once any task history exists, guard reversal refuses before removing protection
or its migration marker. Downgrades are not a retention mechanism; future
reviewed purge/retention services own any required historical deletion.

The [PostgreSQL test profile](stewardship-database-tests.md) verifies migration
reversal, raw-write denials, exact retry identity, stale workers, genuine lease
expiry, concurrent claims/retries, callback/audit rollback and shadow-table safety.
Pure tests reject invalid/private-shaped parameters before any database access.
All identities and admission proofs are synthetic; no real provider credentials
or operational queues are used.

## Remaining integration

BG-01 owns scheduler scans, lost broker-hint recovery, Celery/Valkey dispatch,
registered task types/queues, task-specific retry budgets and safe-point evidence,
restore/purge/campaign admission and operational APIs. DAT-07 and later workflows
add occurrence/outbox/request links and preserve their separate uncertainty and
fulfillment rules. ARC-04/OPS-02 own real identities and runtime SQL grants.
Production startup and reserved worker services remain disabled. This increment
does not complete DAT-01.02/.03/.06, Phase 1 or Gate 1.
