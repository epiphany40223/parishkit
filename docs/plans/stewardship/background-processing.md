# Background-processing implementation plan

Task status: [Background processing checklist](../../tasks/stewardship/background-processing.md).

This plan implements the
[background-processing specification](../../specs/stewardship/background-processing/spec.md).
PostgreSQL is authoritative; Celery/Valkey only delivers execution hints.

## Work packages

### BG-01: Durable task, scheduler, lease, and recovery substrate

1. Implement PostgreSQL TaskRun/occurrence claiming with idempotency keys,
   leases, heartbeats, bounded phases/progress, retries, safe cancellation, and
   abandoned-claim recovery.
2. Configure one scheduler scan loop and service-specific Celery queues without
   treating queue routing as authorization.
3. Implement transactional campaign-work admission checks for restore, purge,
   go-live, mode, lifecycle, and delivery-pause gates at both creation and claim.
4. Add task status/progress APIs consumed by authorized Admin pages.
5. Test broker loss/duplicate hints, worker crash, lease expiry, shutdown,
   cancellation boundaries, and upgrade restarts.

### BG-02: Campaign boundary occurrences

1. Materialize unique start/close occurrences from resolved UTC boundaries.
2. Implement locked scheduled-to-active and active-to-closed transactions with
   state/mode/gate rechecks and intended/actual/lag audit.
3. Replace future close occurrences atomically on end-date edits and make races
   fall through to the guarded reopen workflow.
4. Recover overdue boundaries on scheduler restart while portal/mail gates
   remain independently date-authoritative.
5. Add exact-boundary, DST, duplicate-scan, outage, and lock-race tests.

### BG-03: Production-transition cleanup worker

1. Enforce the go-live gate before old Testing work can mutate state.
2. Delete only inventoried test submissions, workflows, sensitive audit, and
   `testing_override` outbox detail plus Testing-only occurrence/fulfillment
   rows in stable bounded batches with atomic high-water checkpoints.
3. Verify no sensitive inventoried detail remains before `cleanup_complete`.
4. Implement retry/cancel semantics that never restore deleted data and never
   change global mode, including a CRITICAL `cleanup_failed` state after
   automatic retry exhaustion.
5. Test interruption between batches, stale hints, concurrent submissions,
   incorrect ownership/routing, and final readiness races.

### BG-04: Schedule revision, fulfillment, and mode routing

1. Implement scheduler evaluation in the immutable campaign timezone with
   persisted UTC due instants and deterministic gap/fold behavior; draft
   timezone changes recompute only draft previews and resolved boundaries.
2. Implement revision-specific occurrence and stable semantic fulfillment keys
   for initial, reminder, receipt, and digest work.
3. Implement replacement/removal locking, safe cancellation, provider-unknown
   blockers, cross-revision fulfillment, no recall of sent messages, and atomic
   multi-schedule reconciliation with an end-date shortening.
4. Implement Testing override, production, and operational routing as immutable
   classifications; operational notifications never inherit Testing rerouting.
5. Implement missed-work recovery and Family/digest coalescing with accurate
   skipped/coalesced outcomes.
6. Test every schedule/mode/race/restart combination.

### BG-05: ParishSoft delta and full refresh

1. Add shared ParishSoft v2 change-feed capability and durable watermark where
   absent from general ParishKit.
2. Implement 15-minute delta indication handling with affected Family reload,
   ambiguity/discontinuity fallback, and no partial promotion.
3. Implement nightly/configurable/manual full refresh with active/inactive
   transition data and giving periods for the sole current campaign through
   closed reconciliation.
4. Claim SourceMutationLease with fencing, validate tenant/pagination/counts/
   relationships, build derived data, and atomically promote through DAT-03.
5. Coalesce manual requests and prohibit overlapping refresh/publication source
   mutations.
6. Test invalid/empty/large-loss data, retries, stale owner, takeover, manual
   coalescing, new/inactive/reactivated Families, and source window selection.

### BG-06: Family invitations and reminders

1. Materialize one Family occurrence per eligible target/semantic slot with
   current head-recipient and deliverability evaluation at send time.
2. Create distinct, idempotent initial-recovery occurrences when an eligible
   nonresponder becomes deliverable after its initial occurrence, while sharing
   the initial semantic fulfillment slot so only one delivery can succeed.
3. Render versioned templates with eligible names, low-sensitivity manual code,
   opaque secure link, generic URL, parish/campaign values, and mode banner.
4. Persist redacted message/recipient data, seal credential substitutions to the
   token public key, and route provider submission only to `mail-dispatch`.
5. Implement provider idempotency, accepted/failed/unknown outcomes,
   reconciliation, bounded retry, authorized resend, and terminal sealed-value
   scrubbing including cancellation.
6. Implement delivery pause pre-provider recheck, holds, close cancellation,
   and resume coalescing.
7. Test recipient/privacy/routing, repeat rendering, provider timeouts,
   suppression clearing, contact correction, repeated deliverability
   transitions, source changes, pause races, and systemic failure.

### BG-07: Submission confirmations and Admin digests

1. Create one idempotent confirmation occurrence in the submission transaction;
   sending remains asynchronous and does not affect accepted response state.
2. Implement daily post-midnight digest with previous-local-day statistics and
   participation chart artifact from the exact ready CampaignDailyFactSet
   shared with reports; wait/retry rather than substituting another generation.
3. Implement weekly actionable additional-information digest plus correction
   section for previously mailed superseded/withdrawn items.
4. Apply Testing/production routing, delivery-pause holds, campaign-close rules,
   and archive prerequisites. Implement the shared post-close obligation
   inventory and durable semantic skip-resolution handling used by ADM-06,
   including future/unmaterialized daily and final weekly coverage. Make
   creation/claim/revision checks honor skips without covering newer inputs.
5. Test local-day boundaries, fact-build delay/failure, empty/no-recipient
   behavior, missed/coalesced digests, pinned chart parity, corrections, and
   repeat-safe delivery.

### BG-08: Export and graph workers

1. Implement requester-scoped durable export jobs carrying campaign, filters,
   sort, selected IDs, source snapshot, browser timezone, format, and authorized
   scope.
2. Recheck authorization at creation, claim/query, file publication, and
   download; integrate purge/restore/go-live admission gates.
3. Generate atomic opaque temporary files below the reports root with retention
   metadata and no unsafe path/symlink behavior.
4. Implement shared deterministic chart rendering used by UI download and
   digest email.
5. Test large exports, cancellation, role revocation, purge races, partial
   files, formula injection, and retention expiry.

### BG-09: ParishSoft publication worker

1. Claim SourceMutationLease and execute only confirmed immutable publication
   plans produced by ADM-09/DAT-09.
2. Before each entity PUT, recheck fencing, fetch uncached full payload, repeat
   canonical merge/digest conflict evaluation, and use conditional write where
   supported.
3. Group fields per entity, use stable idempotency, bounded retry, uncached
   read-after-write verification, and never replay successful entities.
4. Record partial outcomes and queue final targeted/full reconciliation refresh.
5. Test stale plan/lease, external changes, timeout ambiguity, partial failure,
   retry, and capability-registry shapes with redacted fixtures.

### BG-10: Critical notification and service shutdown

1. Convert systemic failures, stale snapshots, scheduler lag, distributed abuse,
   backup RPO breaches, publication ambiguity, and purge inconsistency into
   deduplicated WARNING/CRITICAL events.
2. Send operational Admin email and optional Slack independently of campaign
   mode without Family data/links/secrets.
3. Implement resolved notifications, repeat suppression, and escalation after
   sustained windows.
4. Make scheduler/workers stop claiming, finish/cancel at safe points, preserve
   leases/checkpoints, and recover after upgrade.
5. Add failure-injection and graceful/forced-shutdown tests.

### BG-11: Exceptional purge worker

1. Execute only a confirmed, current ADM-10 purge request after independently
   rechecking the campaign work gate, quiescence, inventory, backup evidence,
   authentication freshness, and confirmation evidence.
2. Delete the inventoried campaign-owned data in stable, resumable batches with
   durable high-water checkpoints and idempotent retry behavior.
3. Permit rollback only before the first destructive checkpoint; after deletion
   begins, expose recovery and cleanup retry without implying data restoration.
4. Finish by deleting derived files/cache, retaining the minimum tombstone and
   audit evidence, and verifying that no campaign-sensitive inventory remains.
5. Convert every inconsistency or cleanup failure into CRITICAL operational
   state and test crashes, stale hints, lease loss, retry, and terminal cleanup.

## Review handoffs

- Review Gate 2 covers BG-01 and BG-05 source atomicity.
- Review Gate 3 covers BG-02 through BG-08 and the notification/shutdown subset
  of BG-10, with focused idempotency, email privacy, service-key, and restart
  review.
- Review Gate 4 covers BG-09, BG-11, and the purge-recovery additions to BG-10.

## Completion criteria

- Duplicate hints, worker restarts, and scheduler downtime cannot duplicate a
  semantic external action or expose partial source truth.
- Every externally ambiguous state requires explicit reconciliation.
- Every long task is observable, retryable/cancellable only where safe, and
  protected by persistent authorization/admission checks.
