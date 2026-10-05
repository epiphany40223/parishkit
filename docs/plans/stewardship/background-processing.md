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
   Implement the authoritative TaskRun/ScheduleOccurrence transition tables,
   complete terminal predicates, and append-only attempt history. Automatic
   retries reuse nonterminal runs; explicit failed-run retries allocate a
   deduplicated linked run under a serialized retry chain without changing
   occurrence identity or semantic fulfillment keys.
2. Configure one scheduler scan loop and service-specific Celery queues without
   treating queue routing as authorization. Re-emit hints for due unclaimed
   durable work after broker loss, including existing occurrences/outbox rows,
   while preserving holds, leases, retry times, and uncertain-delivery handling.
3. Implement transactional campaign-work admission checks for restore, purge,
   go-live, mode, lifecycle, and delivery-pause gates at both creation and claim.
4. Add task status/progress APIs consumed by authorized Admin pages.
5. Test broker loss/duplicate hints, worker crash, lease expiry, shutdown,
   cancellation boundaries, and upgrade restarts.
   Exercise every allowed and forbidden transition and every state against
   archive/purge/schedule-replacement guards. Race manual retry commands,
   stale owners, and fulfillment; prove abandoned/unknown effects still block,
   failed history survives retry, and terminal failure never fulfills an
   outstanding reporting obligation.

### BG-02: Campaign boundary occurrences

Deliver start/close behavior in Phase 4; finish item 3's restore/reopen
preparation worker with OPS-06/ADM-06 in Phase 6. Record that partial scope until
both are tested.

1. Materialize unique start/close occurrences from resolved UTC boundaries.
2. Implement locked scheduled-to-active and active-to-closed transactions with
   state/mode/gate rechecks and intended/actual/lag audit.
3. Replace future close occurrences atomically on end-date edits, allocate fresh
   execution revisions when reusing dates (including A → B → A), and make races
   fall through to the guarded reopen workflow. Implement its resumable
   background token-preparation task on the general worker with public keys,
   pinned coverage, batch checkpoints, and stale/cancelled staging cleanup.
   Reuse it on the restore-general queue with restore-instance/credential-epoch
   fencing. Support atomic restore activation and dispatch rejection/resealing
   of stale substitutions only when existing delivery/hold rules authorize it.
4. Recover overdue boundaries on scheduler restart while portal/mail gates
   remain independently date-authoritative. Apply start before close under the
   same transaction when both are overdue, including restore release; never
   consume close as inapplicable solely because start has not yet run.
5. Add exact-boundary, DST, duplicate-scan, outage, and lock-race tests, including
   delivery of the overdue close hint before start with no visible active gap.

### BG-03: Production-transition cleanup worker

1. Enforce the go-live gate and rehearsal-epoch invalidation before old Testing
   work can mutate state.
2. Delete only inventoried test submissions, workflows, sensitive audit, and
   `testing_override` outbox detail plus Testing-only occurrence/fulfillment
   rows plus rehearsal credential/session detail in stable bounded batches with
   atomic high-water checkpoints; preserve only the specified non-sensitive
   invalidation evidence and unlinked code reservations.
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
   Implement the [activation catch-up workflow](../../specs/stewardship/background-processing/spec.md#activation-catch-up)
   over DAT-02's durable demand: bounded checkpointed batches, complete-group
   coalescing, scheduled-mail preparation hold, and recovery independent of
   activation HTTP success or broker-hint delivery.
6. Test every schedule/mode/race/restart combination.
   Include activation retry/hint loss, large single-Family/digest groups,
   concurrent scheduler/source producers, submission/eligibility changes,
   schedule edits, close/restore races, unfinished-demand archive exclusion,
   and release of only the catch-up hold after verified completion.

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
   Testing substitutions must use only current-epoch rehearsal credentials;
   persist namespace/epoch, recheck at dispatch, and scrub stale-epoch work
   without rebinding or falling back to Production credentials.
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
   Render the receipt from the confirmation email template described by the
   [content contract](../../specs/stewardship/data/spec.md#content-and-email-templates)
   (its former separate closing-note block is folded into it, #260), never as
   a second browser Thank You page.
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
   rechecking the campaign work gate, quiescence, inventory, backup and matching
   recovery-attestation evidence/freshness/dependency versions,
   authentication freshness, and confirmation evidence.
   Commit closed read admission, expose drain progress, acquire DAT-02's
   exclusive read guard without holding campaign/global row locks, and recheck
   prerequisites before the first deletion batch. Timeout leaves all data
   intact via the existing pre-delete failure path; restart repeats the barrier
   whenever no deletion checkpoint exists.
2. Delete the inventoried campaign-owned data in stable, resumable batches with
   durable high-water checkpoints and idempotent retry behavior.
3. Permit rollback only before the first destructive checkpoint; after deletion
   begins, expose recovery and cleanup retry without implying data restoration.
4. Finish by deleting derived files/cache, retaining the minimum tombstone and
   audit evidence, and verifying that no campaign-sensitive inventory remains.
5. Convert every inconsistency or cleanup failure into CRITICAL operational
   state and test crashes, stale hints, lease loss, retry, and terminal cleanup.

### BG-12: Faster bulk Family send

Implements
[bulk send work outside the lock](../../specs/stewardship/background-processing/spec.md#bulk-send-work-outside-the-lock),
refining [#447](https://github.com/epiphany40223/parishkit/issues/447). It is
post-launch work outside the phase and gate sequence, and it changes the live
send path while Production mails real Families.

- Each PR gets an independent review and full CI. PR 2 and PR 3 also get an
  adversarial concurrency review and a correction check before merge.
- No PR changes any Production credential or how it is looked up. The only
  schema change is PR 2's one-condition occurrence-guard migration for
  preparing ahead (decisions 9 and 13 below).
- Every Production deploy needs the Administrator's explicit approval at the
  time, and none happens during a reminder send (Tuesday and Thursday from
  08:00 ET until that send has settled). No further buffer applies; a window
  such as 02:00–05:00 ET on a send day is fine. Once preparing ahead lands,
  the send also covers its lead window (from 06:00 ET with two hours).
- Between PR 2's merge and PR 4's acceptance, `main` (which is Production)
  carries unproven send code while Production runs with the bulk send on.
  In that window no Production deploy is made from `main` unless the bulk
  send is turned off first, or a pinned earlier release image is deployed
  instead.
- Turning the bulk send off, by its quick stop or a re-render, never needs
  approval. The operator may do it at any time, including during a send,
  and reports it to the Administrator afterwards.
- The Administrator's decisions are recorded below under
  [Decisions](#bg-12-decisions).

**Evidence, and what it does not show.**

- **The launch send.** The launch invitation sent 1,102 messages in about
  24 minutes on the bulk send, about 46 a minute.
- **Preparation is lock-bound.** A 2026-10-02 run on the validation host
  measured preparation only, with mail-dispatch stopped, at about 49
  Families a minute:
  - every bulk preparation batch held a single Family;
  - each Family held the work-order lock for about 0.83 seconds, about 94%
    of it Python work while holding it (42% database round trips; 33% ORM,
    credential and rendering work; 25% configuration parsing, since cached
    in `cb297ab3`);
  - the preparation worker waited about 44% of its time to acquire the
    lock;
  - the SQL guards themselves cost about 50 milliseconds per Family.
- **Gmail is not the limit yet.** Gmail took a median of 0.63 seconds per
  message on a reused connection, so it sustains about 95 messages a minute
  per connection.
- **The send side has not been measured.** The bulk send's submission also
  re-renders and decrypts inside the lock (`begin_submission` in
  `jobs/family_mail_dispatch.py`), so PR 1 measures send-only throughput
  too.

**Design.** The bulk send (`jobs/family_mail_bulk.py`, #430, merged as PR
444, on the batched helper of #334) keeps its batches, its per-item
savepoints, its tickets and every guard. It changes in three ways, following
the spec:

- **Builds outside the lock.** Each batch gains a build step before its lock
  transaction (lookahead, carried builds, one `REPEATABLE READ` snapshot per
  build issued with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` first,
  fingerprint, rebuild under the lock on a mismatch). Builds run serially in
  each process, on its own connection, between its batches; there are no
  threads and no extra connections. `current_render`
  (`family_mail_rendering.py`), `load_family_mail_source`
  (`family_mail_inputs.py`), `seal_current_credentials`
  (`family_mail_credentials.py`) and `current_content`
  (`family_mail_dispatch_content.py`) each assert the work-order lock
  today, so each gets a variant for builds, which holds only the key-set
  lock where it decrypts or seals; the originals stay for the in-lock path.
  `begin_submission` is split into a build half and a commit half; the send
  batch runs the commit half (including `plan_family`).
- **Preparing ahead.** Preparation is serial in the one worker: moving the
  build out of the lock (about 0.45 seconds per Family on the validation
  host) frees the lock for the consumers, but the worker still spends about
  0.8 seconds per Family, about 15 minutes for 1,100. So, on the bulk path,
  the scheduler plans a Production reminder's Families a lead window of two
  hours before its due time (a code constant, matched by the migration's
  cap), and the worker prepares them then; only sending happens at the due
  time. Preparing needs a running claim on the occurrence before its due
  time, which `stewardship_occurrence_guard_v1` refuses today
  (`NEW.due_at>instant` in its fenced-claim branch), and
  `stewardship_family_mail_write_admitted_v1` needs that move, so PR 2 adds
  the one-condition migration described under schema impact. Python rules:
  - `create_message` enqueues the delivery Task with `not_before` at the due
    time (PR 2 adds a `not_before` parameter to `enqueue()`), so mail
    consumers do not claim a prepared reminder early and loop on admission
    holds (`_defer_held` would re-claim about 1,100 Tasks every 30 seconds,
    about 37 claims a second);
  - preparation-time planning (`plan_family` under a `family_mail_prepare`
    claim, today `through = scope.instant` in
    `campaigns/family_schedule_planning.py`, with `SchedulePlan.page` and
    `plan_recovery`'s cutoff in `schedule_recovery.py`) looks up to the
    current time plus the lead window for a Production reminder due within
    it, based on the occurrence rather than the bulk switch, so queued
    preparation tasks still complete if the bulk send is turned off
    mid-window; the dispatch-time `plan_family` keeps its horizon of the
    current time;
  - an older due-but-unsent reminder can merge into the newer one up to two
    hours earlier than today, and the Family is mailed once, at the newer
    due time;
  - the refresh hold (`source/send_hold.py`) counts preparation tasks as
    today but only messages whose occurrence is due; if a delta promotes
    during preparation, the population is dirty until rebuilt and
    preparation pauses for that rebuild;
  - the send progress panel's latest send (`_LATEST_SEND` in
    `jobs/send_progress.py`) and its upcoming list, and the due-work health
    check (`jobs/due_work_health.py`, `SCHEDULER_LAG`, which probably
    already does and needs only a test), ignore what is not yet due;
  - a WARNING flags a scheduled full refresh time (the nightly time, 02:00
    by default, `DEFAULT_TIME` in `source/cadence.py`, and any other
    `full_refresh_times`) that falls inside a lead window, once per
    scheduler process for each campaign and configuration; PR 4's deploy
    confirms the deployment's configured times. A dedicated event for it is
    [#584](https://github.com/epiphany40223/parishkit/issues/584);
  - only a reminder-only group plans ahead: a Family whose invitation is
    still owed plans at the current time, so an unsent invitation never
    absorbs a reminder early.

  The initial invitation at a campaign's start cannot be prepared ahead (no
  occurrence may be created before the configuration starts), so this
  campaign gains only on its remaining reminders. Testing plans at the due
  time as today.
- **Counting.** Every batch counts prebuilt items and items rebuilt under
  the lock, and logs both at DEBUG; the rehearsal reports them.

**Schema impact (PR 2).** One forward migration with a frozen SQL file,
following the [post-launch schema policy](../../specs/stewardship/operations
/spec.md#post-launch-schema-policy), named with the next free four-digit
prefix when its PR merges; the [Admin automation
interface](../../specs/stewardship/admin-automation/spec.md) plans its own
frozen files (its examples are `0004_automation_sessions.sql` and
`0005_automation_fresh_guards.sql`), so whichever lands first takes the
lower number. It re-creates `stewardship_occurrence_guard_v1` with `CREATE
OR REPLACE FUNCTION`, copied verbatim from the edited baseline
(`schema/functions.sql`), and ends with a `DO` block that raises unless the
installed definition contains what only the new one has: the
`family_mail_prepare` clause with its two-hour allowance. The only change is
in the branch that admits a running claim under a fenced task (a move to
`running`, or an update that stays `running`): `OR NEW.due_at>instant`
becomes a refusal only when `NEW.due_at` is later than `instant` plus two
hours for a `family_mail_prepare` task on a Production occurrence, and later
than `instant` for every other task type and for Testing, as before. Every
other condition of the branch and of the guard is unchanged, and no other
guard, view or table changes. Tests: the migration-file and upgrade-parity
tests; the guard's old and new definitions side by side (a preparation claim
1 hour 59 minutes before the due time refused by the old definition and
admitted by the new; 2 hours 1 minute before, and Testing, refused by both;
delivery and schedule-occurrence claims before the due time refused by
both); `stewardship_family_dispatch_live_v1` still refusing a prepared
message before its due time; and a test that pins the Python lead-window
constant at or below the SQL cap.

**Expected result (estimate, replaced by PR 1 and PR 4 measurements),** on
the validation host for 1,100 Families:

- **Preparation**, about 0.8 seconds of worker time per Family, of which
  about 0.3–0.4 under the lock: about 15 minutes, finished inside the lead
  window before the due time.
- **Sending** at the due time, per message and consumer: building about
  0.1–0.3 seconds (outside the lock), the commit and outcome holds about
  0.3–0.4 seconds (under it), and SMTP about 0.63 seconds; about 1.0–1.3
  seconds, so about 45–60 messages a minute per consumer and 90–120 with
  two. That is about 9–12 minutes from the due time to the last outcome,
  against 24 today.
- **Without preparing ahead** (the bulk send off, Testing, or the initial
  invitation), preparation runs alongside sending and is the limit: about
  15 minutes in all.

PR 1 measures build time per item alongside the commit-half and outcome
holds, to confirm where the time goes.

**Setting and fallback.** No new setting. The existing bulk switch
(`bulk_family_send`, with its render, carry-over by the scripted upgrade and
quick stop) already chooses between the bulk and one-at-a-time paths, and the
new behavior, including preparing ahead, is simply how the bulk path works.

- The bulk quick stop or a re-render without the switch returns to the
  one-at-a-time path, which prepares at the due time; reminders already
  prepared ahead wait for their due time and are sent by that path.
- Until PR 2's release, returning to the previous release is an ordinary
  image retarget. From PR 2's release on, turning the bulk send off is the
  immediate rollback (it stops planning ahead and building outside the
  lock; only the looser guard condition remains). Beyond that the
  Administrator chose to fail forward (#447): there is no corrective
  migration. A release from before the migration refuses the migrated
  database, so only in a catastrophic case is the pre-upgrade backup
  restored with the previous release (losing responses since the backup).

**Risks and mitigations.**

- *Stale builds.* The fingerprint recheck inside the lock catches a stale
  build, and the item is rebuilt under the lock; a guard refusal still
  leaves it to the one-at-a-time path.
- *Changes during the lead window.* A response, a pause, an eligibility
  change or a schedule edit after preparation is caught at the
  `submitting` commit by the unchanged dispatch guard and `disposition`;
  content edits are picked up by the send build's re-render. A refresh
  promotion after preparation only makes send builds rebuild; one during
  preparation pauses it until the population is rebuilt.
- *The guard change.* It is one condition, for one task type, in one mode,
  bounded by two hours, and the dispatch guard that decides sending is
  unchanged. The side-by-side tests pin exactly what changes.
- *Render differences between build and write.* A test pins identical
  inputs to identical renders, and the render guard still checks every
  render against current scope.
- *Credential drift.* The key inventory digests, token generation and
  credential epoch are in the fingerprint, and the dispatch guard rechecks
  the generation and epoch at commit.
- *A reminder to a Family that just submitted.* Accepted (decision 11): the
  window is one batch's sending time, as today.
- *Sign-in contention.* Family sign-ins wait behind batches about as they do
  today, since each hold stays within the 0.75-second budget, except that
  an item rebuilt under the lock costs about 0.8 seconds, as one item does
  today.
- *Plaintext in memory.* Carried send builds hold codes and links in memory
  only, never logged, and are dropped on a mismatch, a stop or the end of a
  drain.
- *Wasted builds.* An item another process takes first, or that changes,
  costs CPU only, never a second send.

**Rehearsal protocol** (the baseline in PR 1, the evidence in PR 4), on the
[local environment](../../guides/stewardship-local-environment.md):

1. **Production mode.** Measure in Production mode, as Production runs
   today; Testing timings do not carry over. A smaller Testing run checks
   that Testing preparation still works unchanged and that Testing sending
   works on the new build path.
2. **Mailpit with latency.** Send to Mailpit through PR 1's local-only SMTP
   latency setting, at about 0.6 seconds per message, so local runs model
   Gmail.
3. **Two sizes.** The default 100-Family seeded snapshot, and a 1,100-Family
   campaign from `up --families 1100` and the wizard, kept with `snapshot`,
   that sends only its initial invitation and one reminder. `snapshot`
   replaces the post-setup slot, so take the 100-Family seeded snapshot
   first. The initial invitation is triggered by activating the campaign to
   Production with its Initial schedule due a few minutes ahead, as the
   seeder does.
4. **Each build.** For each size, measure the one-at-a-time path, the bulk
   path of the current release, and the bulk path of the build under test,
   restoring the size's snapshot between runs. Trigger a reminder by adding
   one due a little more than the lead window ahead (a shorter lead for the
   local run is fine). Measure the 1,100-Family invitation end to
   end, and each reminder's preparation (in the lead window) and its send
   (from the due time) separately.
5. **What to measure.** Time from due to the last outcome and messages a
   minute; the [mail send report](../../guides/stewardship-mail-send-report.md)
   phases; a one-second `pg_locks` sampler of the work-order lock's holders
   and waiters; batch hold times; CPU load.
6. **Correctness.** Every candidate Family gets exactly one message, and none
   gets two; there is no `delivery_unknown`, no error and no second
   fulfillment; the seeder's invariant check passes afterwards.
7. **Fault drills** at 100 Families: kill a mail consumer mid-batch (at most
   one batch `delivery_unknown`, never resent); kill the worker mid-batch;
   pause and resume mid-send; edit the schedule and change the template
   mid-send (stale builds rebuilt under the lock); a response, a pause and
   a template edit during the lead window; turn the bulk
   send off and on mid-send; a key rotation between build and commit (local
   only, through a test hook, since no runtime caller rotates keys); a
   busy key-set lock during a build.
8. **Real-provider timing (optional).** On the validation host only, never
   locally: real Gmail timing batches go only to the Administrator's own
   address through the Testing redirect on a Testing campaign, never to
   Families. Before each batch confirm the redirect points at that address;
   never run one while that campaign is in Production, nor on a reminder
   day, since it shares the daily limit; keep every batch small and well
   inside Gmail's daily limit, leaving the 200-message reserve untouched.
   It measures provider latency only.
9. **Acceptance.** At 1,100 Families with the latency setting: correct as in
   step 6, ideally with zero `40P01`, preparation finished inside the lead
   window, and the send from its due time to the last outcome within about
   15 minutes. The local environment is only a proxy for the validation host,
   whose CPU is about four times slower (0.83 seconds per Family was
   measured there), so an optional Production-mode run on the validation
   host against Mailpit or a mail sink may follow, and the first Production
   send after PR 4's deploy is the backstop. A send that still takes longer
   is the trigger to revisit the deferred streamed design
   ([#554](https://github.com/epiphany40223/parishkit/issues/554)) after the
   campaign.
10. **Record.** Record the results, the build and the seeds in the
    [Family mail dispatch guide](../../guides/stewardship-family-mail-dispatch.md).

**Deploying it to Production (PR 4).** As soon as PR 1 through PR 3 have
merged and the rehearsal meets its acceptance (decision 1), the operator
deploys the release with the [scripted
upgrade](../../guides/stewardship-deployment-runbook.md#scripted-upgrade)
and `STEWARDSHIP_SCHEMA_CHANGE=1`, since the release carries PR 2's
migration. The upgrade carries the bulk switch over. It runs at an
Administrator-approved time outside a reminder send and its lead window.
The operator confirms that the switch is still on and that the configured
full refresh times precede every lead window, and the operator and the
implementer watch the next reminder's preparation and send live (progress
page, operational log, `pg_locks` sampler) and run the mail send report
afterwards. Turn the bulk send off at once (its quick stop, no approval
needed) on a second message to one Family, more than one batch per mail
consumer `delivery_unknown`, a rate below today's bulk baseline, or any
CRITICAL alert; the one-at-a-time path then prepares at the due time and
sends what is already prepared. A fault in the guard change itself is fixed
forward, or in a catastrophic case by restoring the pre-upgrade backup and
the previous release (see the rollback options above).

**Dependencies.** PR 1 needs only PR 0. PR 2 and PR 3 follow PR 0 and may
proceed alongside PR 1. PR 4 depends on PR 1, PR 2 and PR 3.

1. **PR 0, specification and plan** (#512). Tests: Markdown lint and the
   traceability test.
2. **PR 1, rehearsal harness and baseline.** A way for the local `deploy`
   to render the bulk switch on or off; the required local-only SMTP
   latency setting; a rehearsal script that adds the due Reminder, starts
   and stops mail-dispatch, samples `pg_locks` and prints the protocol's
   measurements, including build time per item, the commit-half and
   outcome holds separately, and the prebuilt and rebuilt counts once PR 2
   and PR 3 exist. Record
   baselines of the one-at-a-time and bulk paths at both sizes, including
   the bulk send-only rate. Tests: `shellcheck`, the
   script's pure parsing in fast tests, and a proof that Production
   rendering is unaffected by the latency setting.
3. **PR 2, preparation outside the lock and ahead of the due time.** The
   occurrence-guard migration described under schema impact, the build step
   in `preparation_bulk`, the lock-free rendering and source-loading
   variants, the key-locked sealing variant, the fingerprint, the prebuilt
   path in `prepare_occurrence` with the lookahead and carried builds,
   planning a Production reminder a lead window ahead, the preparation-time
   planning horizon, `enqueue()`'s new `not_before` parameter for the
   delivery Task, the refresh hold counting only due messages, the send
   progress panel and due-work health ignoring not-yet-due work, and the
   nightly-time WARNING. Tests: the migration's tests; a prebuilt item
   writing exactly the rows and render a single preparation writes,
   asserting that the prebuilt path was taken; each fingerprint field
   changing between build and write making the item rebuild under the lock;
   a carried build rechecked in a later batch; a key rotation between build
   and write; Testing items still preparing inside the lock and at the due
   time; each dropped-build case (busy key-set lock, inventory not current,
   `40001`, decryption error); a prepared reminder's delivery Task not
   claimed before its due time and the message not sent before it; a
   response and a pause during the lead window refusing the message at the
   `submitting` commit; a schedule edit during the lead window; deltas not
   held by messages not yet due, and preparation pausing while a promotion
   leaves the population dirty; the send progress panel and due-work health
   ignoring not-yet-due work; the nightly-time WARNING; preparation-time
   planning selecting a reminder due within the lead window while
   dispatch-time `plan_family` keeps its horizon; the bulk send turned off
   mid-window with preparation tasks queued, which still complete; the
   existing bulk and single preparation suites unchanged. The lock hold per
   item against today is measured by PR 4's rehearsal, from the batches'
   `item_ms`, `build_ms` and prebuilt/rebuilt counts (deferred from PR 2,
   whose local tests run against a database where rendering is too cheap
   for the comparison to mean anything).
4. **PR 3, sending built outside the lock.** The build step in
   `delivery_bulk`, the key-locked decryption variant, and
   `begin_submission` split into build and commit halves (the bulk path
   calls the commit half; the one-at-a-time path still calls it whole).
   Tests: the prebuilt path taken and asserted; each fingerprint field
   (message and occurrence versions, both configuration rows, hosted files
   and branding assets, source generation, the link token row's or the
   rehearsal credential's version and digest after an in-place change)
   making the message rebuild under the lock; a carried build dropped at the
   end of a drain and never logged; a pause, a close and a response
   between build and commit; a crash after the `submitting` commit leaving
   one batch per consumer `delivery_unknown`; Testing sends on the new
   build; the existing delivery suites unchanged.
5. **PR 4, rehearsal evidence and Production deploy.** Run the protocol on
   the merged build, fix what it finds, record the evidence, then deploy as
   above and record the first send's measurements. If the send still takes
   more than about 15 minutes, report it with the measurements so the
   Administrator can decide whether to revisit #554 after the campaign.

**Deferred alternative.** A streamed send (a per-send preparation run owned by
a new task type, a send loop claiming one message at a time, a three-valued
send-path setting, Testing credentials written inside each chunk, and a
change to lease renewal) was specified and reviewed on #512, then deferred by
decision 9, because it needs a guard migration that cannot be switched off.
Its full text, reasoning and review history are kept on
[#554](https://github.com/epiphany40223/parishkit/issues/554), to revisit only
after the campaign and only if a measured send still exceeds about 15
minutes. Generating Testing credentials once per rehearsal is
[#555](https://github.com/epiphany40223/parishkit/issues/555).

#### BG-12 decisions

All recorded on [#447](https://github.com/epiphany40223/parishkit/issues/447).

On 2026-10-04 the Administrator first decided the streamed design's open
questions (1–8), then chose the no-migration path (9–12), then refined it
to allow preparing ahead (13):

1. **Turning it on:** as soon as every PR has merged and passed its
   rehearsal, without a soak, as an approved Production change outside the
   reminder sends. This still applies, to the PR 4 deploy.
2. **Measure first** before any shared-lock stage. Superseded by decision 9;
   the measure-first rule survives as the 15-minute trigger.
3. **Keep per-message delivery Tasks and occurrence history.** Still
   applies.
4. **Senders:** the existing `mail_consumers` setting, default two. Still
   applies.
5. **Rehearsal data:** a 1,100-Family campaign with an invitation and one
   reminder, with no full 1,100-Family seeded snapshot. Still applies.
6. **Acceptance thresholds** for the streamed design. Superseded: the
   acceptance is now step 9 of the rehearsal protocol.
7. **One three-valued send-path setting.** Superseded by decision 9: no new
   setting; the existing bulk switch suffices.
8. **Stream Testing too.** Superseded: Testing preparation stays as it is,
   and generating its credentials per rehearsal is #555.
9. **No-migration path** (2026-10-04). Build the faster send inside today's
   bulk path, with reading, rendering, decryption and sealing outside the
   work-order lock and a short recheck-and-write under it. For the rest of
   this campaign every existing Production code and link token stays valid,
   and no schema or guard migration touches the send path. The streamed
   design is deferred to #554.
10. **Batched writes and crash loss.** Per-batch "about to send" records,
    then the batch's sends, then its outcomes; a crash loses at most one
    batch's outcomes per mail consumer, which become `delivery_unknown`
    for review and are never resent automatically (2026-10-04).
11. **Stale-state sends are accepted**, such as a reminder to a Family that
    submitted moments earlier, within one batch's sending window, if the
    scheme brings a send to about 10–15 minutes (the Administrator's
    statement on #447, 2026-10-04).
12. **Testing preparation stays on the existing path** for now (Testing
    sending uses the new build path, since it writes no credential);
    generating Testing credentials in bulk when a rehearsal begins is a
    later, Testing-only improvement, #555 (2026-10-04).
13. **Prepare reminders ahead of their due time** with one small forward
    guard migration (2026-10-04). It lets Family mail preparation move an
    occurrence to `running` up to the lead window before its due time;
    nothing may be sent before the due time, and no credential, code, link
    token or token lookup changes. This refines decision 9 to: no
    credential-affecting change and no migration other than this
    preparation-timing guard change. (The comment on #447 calls the refined
    decision 12.)

The Administrator also decided the review's proposals on the streamed design
that day: no buffer beyond the send window (still applies, above), and
retiring the old switch names (#538), keeping the send-path names, keeping the
key-set lock retake and an unrecognized-value fallback, all moot now that
there is no new setting and the streamed design is deferred.

## Review handoffs

- Review Gate 2 covers BG-01 and BG-05 source atomicity.
- Review Gate 3 covers BG-02 through BG-08 and the notification/shutdown subset
  of BG-10, with focused idempotency, email privacy, service-key, and restart
  review.
- Review Gate 4 covers BG-09, BG-11, and the purge-recovery additions to BG-10.
- BG-12 is outside the review gates: each of its pull requests gets an
  independent review, and PR 2 and PR 3 an adversarial concurrency review
  with a correction check.

## Completion criteria

- Duplicate hints, worker restarts, and scheduler downtime cannot duplicate a
  semantic external action or expose partial source truth.
- Every externally ambiguous state requires explicit reconciliation.
- Every long task is observable, retryable/cancellable only where safe, and
  protected by persistent authorization/admission checks.
