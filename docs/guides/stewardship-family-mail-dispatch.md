# Stewardship isolated Family mail dispatch

## Scope and delivery boundary

Branch `pr/stewardship-family-mail-dispatch` starts at verified PR #38 merge
`72787c28d9108fee0c48e570e09b167d13e9f860`. It continues
[BG-06](../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders)
after [personalized preparation](stewardship-family-mail-preparation.md).

This increment connects a prepared invitation/reminder to the isolated MAIL
consumer, including current-source rendering, private-token resolution, final
admission, provider submission, recipient-specific outcomes, bounded retries,
pause/resume and crash reconciliation. It does not permit live-provider smoke
tests or Production activation before the controlling gate.

The following coherent increment owns the Admin delivery-resolution and
verified-refusal-clear workflows: external-evidence resolution, explicit
duplicate-risk resend, failed-message retry and durable portal notification.
Failed-task retry also owns an unsent pending message whose preparation Task
exhausted its budget. That row retains its occurrence and failure journal for
an explicit linked retry; it is not silently skipped or automatically resent.
Those actions remain unavailable, never inferred from an uncertain SMTP
acknowledgement. The full BG-06 checklist stays open until that integration and
its acceptance tests are complete. BG-10 retains operational escalation;
ADM-06 retains the broader campaign-control UI. Gate 3 remains closed.

## Implementation checkpoints

- SMTP uses one Family envelope, shared MIME construction and the configured
  Workspace identity. MAIL/RCPT and DATA are separated so a definitive refusal
  is distinguishable from lost acceptance. A stable Message-ID is correlation
  only; this provider has no assumed idempotent-send or status-query contract.
- A finite private helper receives credentials and resolved content through
  anonymous pipes. It emits only a closed result and recipient positions,
  never addresses, provider prose, tokens or keys. Missing/malformed/late
  acknowledgements remain uncertain. A result the helper has finished is
  kept, even when the worker's lease check waited past the deadline or
  failed afterwards; settling it still rechecks ownership. The lease check
  never blocks for long: the worker's own lock and each of its few database
  statements (lock waits included) get at most a second, so a tick takes a
  few seconds at worst, and a tick stopped by one of those limits is skipped
  and reported. A busy deployment cannot turn a known outcome into an
  uncertain one.
- Family mail uses one batched helper per mail worker (#284), not one per
  message. The helper reuses its OAuth token and SMTP connection across
  consecutive messages, while each message keeps its own Task, committed
  submitting state, deadline and settled outcome. The helper writes a
  `started` line before each submission. A helper that ends before that line
  never began the message, which is then handed once to a fresh helper. After
  that line, a lost helper or a mismatched result is delivery unknown and is
  never resent. A connection Gmail closed while idle is replaced before DATA,
  and the message is sent once. Any fault retires the connection, and a
  provider fault retires the helper. Helpers are rotated after 100 messages,
  10 minutes or 60 seconds idle. The
  [design comment](https://github.com/epiphany40223/parishkit/issues/284#issuecomment-5896798635)
  has the full reasoning. Digests keep one-message helpers. Replacing a
  helper never waits: it gets EOF and is reaped later, and one that has not
  exited two seconds later is killed and logged as `helper_timed_out`. A
  deadline kill before the `started` line leaves the message definitely
  unsent (an ordinary retry); a result that fully arrived before the kill
  is kept.
- The transport has an operator fallback to one helper per message; see
  [Falling back to one helper per message](#falling-back-to-one-helper-per-message).
- Gmail's own sending limits are recognized by the enhanced status code that
  starts a reply line, never by prose elsewhere in it: `5.4.5` on a 5xx
  reply (the daily user sending limit, at any stage), `421 4.7.x` (Gmail
  closing the connection for a sending rate) and `454 4.7.x` in reply to
  AUTH (login rate). The one exception reads Gmail's wording: `454 4.7.0
  Cannot authenticate due to a temporary system problem` in reply to AUTH
  is Gmail's own login service failing, so it is a shared outage like any
  other temporary handshake reply, not a limit, and it counts toward the
  outage pause below (#382). Other `4.7.x` refusals, for example to one RCPT, stay
  ordinary per-address or per-message temporary refusals. A limit refusal
  blames no address and trips no outage circuit. The helper reports the
  limit on its output line only; the stored attempt is an ordinary definitive
  non-acceptance by a healthy provider.
- The refused message waits an hour for the daily limit and 15 minutes for a
  rate limit (`LIMIT_RETRY_SECONDS`). The daily limit and a rate limit at
  the greeting, EHLO, AUTH, MAIL or RCPT are mailbox-wide, so new sends pause
  for the same time (plus up to five minutes of jitter), logged once per
  pause. A `421 4.7.x` in reply to DATA (`"message"`) holds only that
  message: it still closes the session, so it is a limit rather than a
  content refusal (which would be 5.7.x or 552), but pausing all Family mail
  at every retry of one message Gmail keeps refusing would stall the queue.
  Any other `421`, and any `4.3.x` or `4.4.x` reply (such as `451 4.3.0
  Temporary System Problem`), in reply to RCPT or DATA is the provider's
  temporary trouble and is a `"message"` limit too, so a partial Google
  incident cannot spend every queued message's attempt budget. Per-address
  `45x 4.7.x` RCPT refusals are unchanged. One message carries all of a
  Family's head addresses, so a `4.3.x` or `4.4.x` refusal that really
  concerns one of them holds the whole message (sending to the others on a
  session the provider is closing could send a duplicate later). A
  persistently flaky address can therefore keep the invitation from reaching
  the Family's other, working address until the message is accepted or
  reaches the 7-day cap below.
- Limit refusals are left out of the attempt budget. A message refused at a
  limit continuously for over 48 hours (`LIMIT_GIVE_UP`), measured from the
  first refusal of an unbroken run (another outcome or a staff retry starts
  a new run), fails visibly like any exhausted retry, but only while no mail
  from the sending mailbox was accepted in the last 24 hours
  (`ACCEPTANCE_WINDOW`): recent acceptances mean the queue is still draining
  past a real but partial limit, so the message keeps its place. After 7
  days (`LIMIT_GIVE_UP_ABSOLUTE`) it fails regardless, counted from the start
  of the run and also from the message's first limit or outage outcome
  since its last staff retry, so an outage that starts a new run cannot
  stretch the wait to about two weeks (#382). An earlier ordinary failure
  does not count, so it never fails a message at its first limit hold. The
  failure log names
  the message id and Family DUID, never an address. Since stored evidence
  never names a limit, a limit refusal is recognized by its Task's
  RECONCILING-phase deferral for the same attempt, with healthy evidence.
  That phase is written in the transaction that records the outcome, so a
  crash before the Task's retry transition keeps it (#382); recovery then
  retries the Task as a hold.
- Stewardship also stops before Google does. Google limits a mailbox per
  rolling 24 hours to about 2,000 messages and 2,000 unique external
  recipients (see Google's Gmail sending limits), so Stewardship counts the
  RECIPIENTS of accepted and uncertain submissions in the last 24 hours and
  sends nothing more at 1,800 (`DAILY_SEND_LIMIT`). Invitations, reminders and
  Family tests stop 200 earlier (`RESERVED_SENDS`), keeping room for receipts,
  digests and alerts. A capped message is still claimed, then deferred 15
  minutes as a hold (not a failed attempt), so the queue goes quiet instead of
  refusing the same claim on every scan. A campaign with more recipients than
  that simply finishes over the following days; split the initial invitation
  with mail schedules to control which Families go first.
- Per-address outcomes are recorded in the existing immutable outbox event's
  bounded evidence, bound to its numbered attempt and exact rendering. A
  permanently refused address can be suppressed independently of accepted,
  retryable or uncertain DATA outcomes. Transient refusals do not suppress an
  address, and partial acceptance does not resend to the accepted recipients.
  A message whose every address was refused temporarily (such as `450
  4.2.1`, the receiving mailbox throttling) is retried after 15 minutes, 1
  hour, 4 hours and then 12 hours (`RECIPIENT_RETRY_SECONDS`), so its five
  attempts span about 17 hours instead of about 8 minutes (#382). The step
  counts only attempts that count against the budget, so limit and outage
  holds before it do not skip it ahead. Such a message shows on Outgoing
  mail as waiting to retry, with no reason, for up to about 17 hours; that
  is expected, and there is no control to hurry it. Administrator alerts, a
  refusal of only some addresses, and a refusal that mixes permanent and
  temporary codes keep the ordinary schedule (30 seconds, doubling, at most
  10 minutes).
- Current source, recipient selection, content and credentials are rechecked at
  the locked submission boundary. The mail process has no general-code key;
  it opens the prepared code/reference and decrypts only the corresponding
  retained token. Generation changes remain with restore/reopen ownership.
- Observed outcomes settle independently of subsequent lifecycle/configuration
  changes. Terminal outbox transitions scrub message substitutions; uncertainty
  retains them and blocks automatic resend. Task abandonment plus elapsed
  provider deadline is uncertainty, not proof of non-acceptance.
- Shared temporary token/connection/handshake failures retain a definite-unsent
  retry outcome and impose a 60-second process-wide new-send cooldown. Such
  an outage result is not the message's fault, so it is left out of the
  attempt budget; a message kept unsent only by outages fails 7 days
  (`LIMIT_GIVE_UP_ABSOLUTE`) after its first provider outcome (or its last
  staff retry). Three
  consecutive shared outages pause that sending run for 10 minutes
  (`OUTAGE_RECOVERY_SECONDS`). After a pause exactly one message probes the
  provider, and one more outage result pauses again at once, so a long
  outage spends one probe per pause. The first pause since a healthy result
  is logged CRITICAL and later ones WARNING. An observed healthy provider
  result resets the consecutive-failure count; local unobserved outcomes do
  not. No result can shorten an existing pause. Deterministic shared
  TLS/configuration/protocol faults (SYSTEMIC) stop the run immediately and
  stay stopped until the process restarts, since waiting cannot fix them.
  The pause and the stop are process-local; the durable record is BG-10's
  `mail_provider_failed` incident, which the same stored outcomes open and
  the first healthy outcome after the pause resolves. Already-submitted
  outcomes can always drain.
- Delivery certainty and provider health are separate closed values in the
  private IPC and SQL evidence. A DATA connection fault can be both uncertain
  delivery and an unhealthy provider; it never becomes a safe resend. Earlier
  exact RCPT refusals survive a subsequent shared fault. Temporary TLS EOF,
  closed-connection and syscall failures use cooldown; certificate/protocol
  faults stop the run. Local helper-launch failures also use shared cooldown.
- Without SMTPUTF8, an unsupported Family address fails only that Family's
  message, with no fabricated RCPT refusal. The adapter does not silently remove
  a configured recipient to deliver an ASCII subset. The next Admin resolution
  increment owns retry after correcting the address or provider capability.
  Unsupported shared sender/Reply-To headers instead stop the sending run.

Implementation and local correction validation of this dispatch boundary are
complete; protected PR delivery is pending. All checks use synthetic
providers and owned disposable databases; existing development databases are
untouched. The follow-on Admin workflows and Gate 3 remain open.

## Falling back to one helper per message

`batched` is the default Family mail transport. `per_message` is the
one-helper-per-message transport from before #284, with the same per-message
outcomes, retries and limits, only slower. The mail worker logs which one is
in effect when it starts: an INFO line for batched, a WARNING for the
fallback.

To switch the running deployment, recreate only the mail worker with the
variable set for that one command. The rendered Compose file passes it to
`mail-dispatch` only, and leaves it empty otherwise:

```text
PARISHKIT_STEWARDSHIP_FAMILY_MAIL_TRANSPORT=per_message docker compose ... up --detach --force-recreate mail-dispatch
```

To return to batched sending, recreate it again without the variable:

```text
docker compose ... up --detach --force-recreate mail-dispatch
```

Recreating stops the worker gracefully: its in-flight message is settled,
or, if it cannot be, recovered like any other interrupted submission (never
resent blindly).

The choice lasts only as long as that container. `restart mail-dispatch`
keeps it, but **any recreation of `mail-dispatch` without the variable
returns to batched sending**: an upgrade's `up --detach`, or any other `up`
that recreates it. To stay on the fallback, put the same prefix on that
command too, and check the startup log line afterwards. Replacing the
mailbox key from the web recreates nothing: every message rereads the key,
and a new key simply starts a fresh helper.

Use the one-command prefix form shown above, never `export`. An exported
variable stays in your shell, where `retarget-image` refuses to run while
it is set, and an install run from that shell writes it into the
deployment's rendered documents for good.

The deployment YAML has the same setting, `family_mail_transport` (see the
[deployment settings](../development/stewardship-deployment.md#schema-version-1)).
It is rendered into each service's document, so changing it there takes
effect only with a reinstall, as the
[runbook](stewardship-deployment-runbook.md) says for `operational_alerts`.

## Two mail consumers

One mail consumer prepares and sends one message at a time, so a large send
takes the sum of every message's preparation and SMTP time. The
`mail-dispatch` container therefore runs two mail consumer processes by
default: the main process, and a second one it starts itself
(`runtime ... --queue mail`). Both take hints from the same mail queues,
each has its own batched helper, OAuth token and SMTP connection, and both
use the one Workspace mailbox. `docker compose ... top mail-dispatch` lists
both processes and, while Family mail is going out, up to two helpers.
Everything else mail-dispatch sends also runs on both processes: Admin
report digests, Administrator alert and security mail, campaign mail and
setup mail tests. Each process has its own outage circuit for alert and
security mail too.

Two processes never send one message twice. Every message is one Task, and
a consumer must claim the Task before it does anything: the claim locks the
TaskRun row, requires it to be still queued and advances its fence. A hint
taken by both processes therefore runs once, and the other process claims
nothing (`tests/stewardship/database/test_mail_consumers_postgresql.py`
races two real handlers to prove it).

The processes share what must be shared and keep the rest per process:

- **Daily limit.** The count of recipients sent in the last 24 hours is read
  from PostgreSQL, so both processes see each other's sends. Far from the
  bulk limit each process re-reads it at most every 15 seconds; once its
  count is within 100 recipients of the limit, before every message. From
  then on a process cannot see only the other's one message still in
  flight. A message has at most 100 recipients, so the bulk limit (1,600)
  can be passed by at most that, 1,700 of the 1,800, still inside the 200
  held back for receipts and digests. Reaching the last 100 unseen would
  need more than 100 recipients settled within one 15-second interval,
  about three times the launch's rate; that is an expectation from the
  sending rate, not a hard bound.
- **Gmail sending limits.** A refusal at Gmail's limit holds only the
  process that received it. The other process is held by its own next
  refusal, so at most one more message is refused. That message is
  definitely unsent and is retried after the limit, like the first.
- **Outages.** Each process pauses after its own three outage results, so
  an outage costs at most three attempts per process (six in all instead
  of three), and each 10-minute cooldown ends with one probe per process.
  A connection that never reached `DATA` is definitely unsent and retried.
  One that failed after `DATA` is `delivery_unknown` and is never retried
  automatically, as with one process; resolve it as described in the
  launch runbooks. The CRITICAL pause and limit log lines may appear once
  per process.
- **A configuration or credential fault (SYSTEMIC).** This stops sending
  until the mail worker restarts, in both processes: the process that sees
  it tells the other through a marker file in the container, and the
  container's next start clears it. The other process may already have one
  message in flight, so such a fault can affect up to two messages (one per
  process) instead of one. What happened to each depends on when the fault
  struck. A fault before the `DATA` command was sent (for example while
  connecting, signing in, or naming the sender and recipients) means the
  message was definitely not sent: it becomes a failed delivery, and an
  Admin retry after the fault is fixed is safe. Once `DATA` has been sent,
  a fault (for example an unexpected reply to `DATA`, or a protocol or TLS
  failure while waiting for one) means Gmail may have accepted the message:
  it becomes `delivery_unknown`, is never retried automatically, and is
  settled with evidence as the launch runbooks'
  [messages in `delivery_unknown`](stewardship-launch-runbooks.md#messages-in-delivery_unknown)
  describe. A message one process has just claimed when the other stops is
  held, not failed, and does not spend one of its preparation attempts.

The main process supervises the second as the worker supervises its source
process ([worker queues and processes](../specs/stewardship/background-processing/spec.md#worker-queues-and-processes)).
A stop request reaches both, and they drain together. If the second process
exits, the main process stops and the container exits (production restarts
it). A late heartbeat from the second process is logged as a
`helper_timed_out` entry for `mail_helper`, with the limit and how late it
was. Silence for more than twice the probe's limit stops the container and
is logged at `ERROR`. Under `docker stop`, a second process still draining
is killed 15 seconds before the container's stop grace period ends, after
its `ERROR` entry is written, so Docker's own kill never comes first. That
entry's limit is the deadline it was killed at (the grace less 15 seconds,
345 by default) and its elapsed time is counted from the stop request.

The same `mail_helper` kind also records the SMTP helper's deadline and
retirement kills. Those entries name the helper (`helper` is
`family_delivery_worker`, or another `*_worker` for digests and alerts);
the second process's entries have no `helper`.

Two processes need six connections for the mail login (three each: the
task, lease renewal and timeout log). Each process writes one timeout entry
at a time, so the three per process are a real bound. Every timeout entry's
facts (what, limit, elapsed) go to the process log first; one that waits
more than 5 seconds for its turn is not written durably, and the process
log records that as `task_timed_out` for `timeout_log_slot`, with its own
limit and wait. A deployment provisioned before this
change needs the one-time step in the runbook's
[mail dispatch connection limit](stewardship-deployment-runbook.md#mail-dispatch-connection-limit).
A runtime budget whose mail limit is below six runs one process.

Each mail consumer keeps its task connection open between messages (#365),
so a message does not pay for new connections or plan the dispatch guards
again. This adds no connection: the kept connection is the process's task
connection, and the in-flight check during SMTP uses it. A connection is
closed instead when anything could carry into the next message: an open
transaction, a database error, or a failed message. A connection is not
reused once it is 5 minutes old; it may stay open, idle, until the next
message, which then reconnects and passes the login checks again. Before
each message, and after SMTP before the outcome is recorded, a consumer
drops a kept connection that no longer answers, such as after a PostgreSQL
restart, and reconnects. Each outcome's send statistics say whether its
message started on a kept connection (`db_kept`). Every other service still
closes its connection after each task. The operations specification's
[kept mail connections](../specs/stewardship/operations/spec.md#kept-mail-connections)
gives what operators see and how to cut off the mail login. The code is
`src/parishkit/stewardship/jobs/connection_reuse.py`.

### Falling back to one mail consumer

The setting is `mail_consumers` (1 or 2, default 2). To run one process,
recreate only the mail worker with the variable set for that one command,
exactly like the transport fallback below:

```text
PARISHKIT_STEWARDSHIP_MAIL_CONSUMERS=1 docker compose ... up --detach --force-recreate mail-dispatch
```

To return to two, recreate it again without the variable. The same rules
apply as for the transport switch: use the prefix form, never `export`; any
later recreation without the prefix returns to the deployment setting; and
both prefixes can be used together. One process needs no change to the SQL
connection limit.

## Turning on the bulk Family send

The [bulk Family send](../specs/stewardship/background-processing/spec.md#bulk-family-send)
plans, prepares and sends invitations and reminders in batches. It is off by
default. It needs the batched transport (above): with the per-message
fallback the mail worker keeps sending one message at a time.

The switch is rendered into the deployment's generated documents, so it is
turned on by re-rendering them. With the online services stopped, run the
runbook's `retarget-image` step in the release image, with the same digest
the deployment already runs (or the new one, when upgrading), and add
`-e PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1` to that `docker run`. Add
`-e PARISHKIT_STEWARDSHIP_BULK_SEND_BATCH=10` (1–100, default 20) to choose
B, the most messages one send batch marks `submitting` before sending; a
smaller B means fewer `delivery_unknown` messages to settle if a mail
consumer dies mid-batch. Then start the services as usual. At startup the
worker and mail worker log a WARNING saying the bulk send is on and with
which B; with debug logging on, each batch transaction logs one
`bulk timing: {...}` line: what it did (`prepare`, `commit` or `outcome`),
how many items it finished and tried, its lock hold, each item's time
under the lock, and how many items were written from a build made outside
the lock (`prebuilt`) or rebuilt under it because their inputs changed
(`rebuilt`); BG-12's rehearsal report reads these lines.

To turn it off, either:

- for a quick stop without re-rendering, recreate the three services with
  the variable set to 0 on that one command (only a render with the switch on
  passes it through):

  ```text
  PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=0 docker compose ... up --detach --force-recreate scheduler worker mail-dispatch
  ```

  A later recreation without the prefix turns it back on; or
- re-render without it: the same `retarget-image` step without the variable.
  The rendered documents are then exactly those of a release without the bulk
  send, so returning to such a release is the ordinary `retarget-image` with
  its digest.

Work already started either way is finished by whichever path runs next; no
cleanup is needed. The switch is never part of the provisioning record, so
`retarget-image` does not count it as a changed deployment input.

### Reminders prepared ahead of their due time

With the bulk send on, a Production reminder is prepared during the two
hours before its due time, so only sending is left at the due time
([preparing ahead](../specs/stewardship/background-processing/spec.md#preparing-ahead-of-the-due-time),
BG-12). What an operator sees during that lead window:

- Outgoing mail lists each prepared reminder as pending. Its delivery task
  waits for the due time; nothing is sent before it, and the dispatch guard
  refuses a message whose occurrence is not yet due in any case.
- The send progress panel shows the reminder as upcoming (within an hour
  of its due time), not as a send in progress, and due-work health ignores
  it until it is due.
- Scheduled delta refreshes wait while the reminders are being prepared,
  and run again once preparation ends, until the send begins. A Family that
  responds, a pause, or a schedule or content change after preparation is
  caught when the message is sent, as always.
- If a scheduled ParishSoft full refresh (the nightly time, any other
  configured full refresh time, or an hourly or quarter-hour full refresh)
  falls inside a reminder's lead window, the scheduler logs one WARNING
  (`refresh_lead_window_conflict` with the category
  `full_refresh_in_lead_window`, #584) per process for each campaign and configuration, so a settings or
  schedule change is checked again: that refresh's promotion would pause
  preparation until the Family population is rebuilt. The default 02:00
  precedes an 08:00 reminder's window.
- Planning ahead applies only once a Family's invitation is fulfilled. A
  Family whose invitation is still owed plans exactly as before, so an
  unsent invitation never absorbs a reminder early. Two reminders less than
  two hours apart are merged: the earlier one, not yet sent, is coalesced
  into the later, and the Family gets one reminder, at the later time.

Turning the bulk send off during a lead window is safe: preparation already
queued completes on the one-at-a-time path, and the prepared reminders are
sent at their due time by whichever path is running. The initial invitation
and Testing are always prepared at their due time.

The release that adds this carries a forward migration
(`stewardship_campaigns.0003_occurrence_prepare_ahead`), so it is deployed
with `STEWARDSHIP_SCHEMA_CHANGE=1` and cannot be rolled back by retargeting
the previous image; see the
[runbook](stewardship-deployment-runbook.md#preparing-reminders-ahead-bg-12).

## Send statistics and tuning the batch caps

Every Family outcome (and every digest outcome this worker settles) records
send statistics beside its evidence (#284). These are per-phase timings,
how the helper and its connection were used, why a connection or helper
ended, the transport and the caps in effect. The database admits only
whole numbers of at most 12 digits, yes/no values, a random helper id and a
closed list of fixed words for the keys that take words, at most 48 values
per outcome, so nothing personal can be stored there. Numeric keys stay
open, so a new timing needs no schema change; a new word needs one. They
never affect an outcome: invalid ones are dropped with a warning. A database whose result validator predates
them simply gets none, with one WARNING from the mail worker, until the
release's in-place SQL is applied.

After a large send, run the
[mail send report](stewardship-mail-send-report.md). It shows the wall
clock, throughput, phase percentiles, connection reuse, retire reasons,
limit holds and what batching saved. Its
[tuning section](stewardship-mail-send-report.md#tuning-the-caps) explains
which numbers argue for larger or smaller batch caps.

## Bulk send rehearsal baselines

The [faster bulk Family send](../plans/stewardship/background-processing.md#bg-12-faster-bulk-family-send)
(BG-12) is accepted on local rehearsals. A rehearsal is the local
environment's `rehearse` command: see
[rehearsing a bulk send](stewardship-local-environment.md#rehearsing-a-bulk-send)
for how to run it and what its report shows. This section records the
results, the build and the seeds.

**Baselines (BG-12 PR 1).** Each size needs the one-at-a-time path, the bulk
path and the bulk send-only rate, with the modeled latency at 600 ms:

| Size | Path | Build | Due to last outcome | Accepted/min | Preparation | Send-only/min | Lock held | 40P01 | Correct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 100 | one at a time | `78babeab` | 0.70 min | 104.4 | 0.39 min (187/min) | n/a | 17.6% | 0 | yes |
| 100 | bulk | `78babeab` | 0.53 min | 139.0 | 0.19 min (392/min) | n/a | 13.4% | 0 | yes |
| 100 | bulk, send-only | `78babeab` | 1.57 min | 46.4 | 0.18 min (397/min) | 161.8 (0.45 min) | 12.5% | 0 | yes |
| 1,100 | one at a time | (not yet run) | | | | | | | |
| 1,100 | bulk | (not yet run) | | | | | | | |
| 1,100 | bulk, send-only | (not yet run) | | | | | | | |

The 100-Family runs were made on 2026-10-05 on the local VM (4 CPUs, 5 GiB),
each from `reset --seeded` and then `deploy --schema-change --bulk on|off
--smtp-latency-ms 600` of `main` at `78babeab` (release 1.3.0; the seeded
snapshot predates a migration, hence `--schema-change`), then `rehearse`
with the default five-minute `--due-in`. The seed is the default 100-Family
seeded snapshot (default response scale) taken 2026-10-04, in which 73
Families are a Reminder's candidates, so each run sent 73 messages. Each
run had two mail consumers.

- **Correctness.** Every run passed: one accepted message per candidate,
  none accepted twice, no `delivery_unknown`, no failure, no deadlock, and
  no ordering invariant violation.
- **Send.** `submit_ms` was about 605 ms at the median (the modeled 600 ms
  plus Mailpit), with a p99 of about 1.05–1.09 s (the first message on a
  new connection).
- **Bulk holds.** Preparation batches held the lock for a median of 650–715
  ms over about six Families (about 73 ms per item, 42–43 ms of it build
  work); commit-half batches held it for 683–693 ms over five to seven
  messages (about 77 ms per item, 49–50 ms of it build work); outcome chunks
  held it for about 125 ms over eight messages (14 ms per item). Prebuilt
  and rebuilt counts were 0, as expected before PR 2 and PR 3.
- **Lock samples.** The work-order lock was held in 12–18% of the
  one-second samples and waited on in 1–4%; CPU busy share was about 10%.
- **Send-only.** Its due-to-last-outcome time includes the minute or so
  during which mail-dispatch stayed stopped until preparation had finished,
  so its send-only rate (about 162 a minute) is the figure to compare.

At 100 Families the local VM, faster than the validation host, is not
lock-bound: the send is limited by the modeled latency over two consumers
(about 100 a minute each at 0.6 s), and both paths finish within a minute
of the due time. The 1,100-Family runs, where the lock is expected to
matter, still need the invitation support that a later BG-12 pull request
adds (see the local guide); #447 tracks them.

## Fresh-install schema evidence

Independent empty PostgreSQL 18.6 databases installed the exact PR #38 baseline
and the current schema. The former matched its committed catalog fingerprint.
The latter adds four dispatch/result functions and six triggers, changes the
occurrence guard, refusal guard, refusal-effect function and scheduling work
view, and removes nothing. Columns, constraints, indexes and policies are
unchanged. The updated baseline records 401 functions and 409 triggers; it is
not an upgrade path or a promise of development-database compatibility.
The committed [catalog fingerprint](../../tests/stewardship/database/schema-baseline.json)
and [fresh-schema test](../../tests/stewardship/database/test_schema_baseline_postgresql.py)
enforce this locally captured evidence independently of model declarations.

The refusal-effect trigger uses a fixed search path and owner privileges only
to derive deliverability from validated immutable refusal evidence. It has no
callable runtime/public entry point. MAIL has no general Family UPDATE grant;
the real-role permission tests check both boundaries.

## Review evidence

Round 1, Pika `20260916-095640-8749e8`, reviewed the full branch from
`72787c28d9108fee0c48e570e09b167d13e9f860` through
`92db22265b09d8e9ef4a20b00e757ea31fed70a8` (tree
`8b2453ab7dcb286ba6ce01aea992d8dabe5d53b5`). Exact-path permission preflight
passed; both Claude shards and Codex completed without degradation. There were
20 raw findings: one High, seven Medium and 12 Low. All are dispositioned here,
including the 12 below Pika's displayed cutoff. This completes round 1; the
High finding was fixed and subsequent independent rounds remain required.
Exact-head validation receipts are listed separately below.

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1/1 | High | Fixed: metadata-only admission can never commit SUBMIT after resume; real-worker race regression. |
| Claude 1/2 | Medium | Fixed for in-flight holds: typed holds retain journaled reconciliation retries outside the failure budget. Pre-claim holds were already excluded by admission. |
| Claude 1/3 | Medium | Fixed known pre-launch validation and elapsed-budget outcomes. Lost ownership remains fatal, not a fabricated receipt: that exception is a BaseException and never entered the claimed generic handler. |
| Claude 1/4 | Medium | Fixed per-Family SMTPUTF8/malformed-recipient classification; only shared provider/configuration faults halt the current worker run. Restart resets that process-owned halt. |
| Claude 1/5 | Low | Fixed: recovery requires exactly one optimistic occurrence update and rolls back on mismatch. |
| Claude 1/6 | Low | Fixed defensively: absent population is a typed hold in either mode. |
| Claude 1/7 | Low | Retained safety/performance trade-off: each private-helper poll rechecks ownership and releases its database connection. The shared finite transport uses this pattern; pooling/throttling must preserve those fences and can be measured with BG-10 operational work. |
| Claude 1/8 | Medium | Fixed: added real-worker regression coverage for resume, credentials/configuration, launch deadline, admission holds, crash budget and actual systemic halt. |
| Claude 1/9 | Low | Fixed: configuration identity is checked before SUBMIT, so an edited configuration no longer generates a fictitious SMTP transient attempt. |
| Claude 2/1 | Medium | Fixed with Codex 1: transient token/EHLO/AUTH/MAIL errors retry; definitive shared credential/configuration refusals remain systemic. |
| Claude 2/2 | Low | Fixed with Claude 1/4: unsupported Family addresses do not halt others or invent RCPT refusal evidence. |
| Claude 2/3 | Low | Duplicate of Claude 1/5; same checked-update fix and regression. |
| Claude 2/4 | Low | Retained conservative boundary: after helper launch a hard deadline with no receipt is unknown, even if the child might not have reached DATA. Inferring its protocol position would be unsafe. Per-operation budget optimization is not required for correctness. |
| Claude 2/5 | Low | Fixed: temporary handshake, token outage/refusal, Unicode body and address negotiation tests. |
| Claude 2/6 | Low | Fixed: removed duplicate refusal SELECT entry. |
| Claude 2/7 | Low | Clarified evidence links to the committed catalog fixture and real schema test; the independent local comparison was already performed and passed. |
| Codex 1 | Medium | Fixed with Claude 2/1: temporary MAIL refusal is definitely unsent and retryable. |
| Codex 2 | Medium | Fixed: abandoned unsent work honors the preparation failure budget, excluding journaled admission holds. |
| Codex 3 | Low | Fixed: Reply-To participates in SMTPUTF8 negotiation; seven-bit body encoding avoids unadvertised raw eight-bit content. |
| Codex 4 | Low | Rejected: RFC 5321 section 4.3.2 lists RCPT success as 250/251; 252 belongs to VRFY/EXPN. No DATA is sent for an unexpected RCPT response. Round 2 further classifies it as a bounded per-message retry, not a shared halt. |

The SMTP classifications and Unicode serialization were checked against
[RFC 5321 command/reply sequences](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.3.2)
and [Python email policy](https://docs.python.org/3/library/email.policy.html#email.policy.Policy.cte_type).

Round 2, Pika `20260916-102413-01fd55`, reviewed corrections from `92db222`
through `be5bbc1c3faf5f44d8da13b329fe28518e5b5175` (tree
`98843fa5e744aac3b9174d3488aefe747a3c9c53`). Exact-path permission preflight
passed; Claude and Codex completed without degradation. All 13 raw findings
(six Medium and seven Low, no High) are dispositioned below. The read-only
Codex reviewer could not run pytest without writable temporary storage; the
independent validation runs below supply executable evidence.

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1 | Medium | Fixed with Codex 2: distinct durable-submission and helper-launch flags retain definite non-acceptance for a pre-launch clock/settings failure; real-worker regression. |
| Claude 2 | Medium | Fixed: shared settings/credential/invocation validation failures are systemic; only a message's transport-size overflow is per-message permanent failure. |
| Claude 3 | Medium | Fixed: explicit shared-unavailable result, 60-second circuit cooldown and three-consecutive-outage run halt; deterministic TLS/protocol failures halt immediately. Durable evidence and actual-worker admission tests cover the new outcome. |
| Claude 4 | Low | Clarified ownership: the following Admin-resolution increment owns explicit linked retry of failed preparation Tasks and their retained pending messages. An unsent crash is not fabricated provider failure or silent cancellation. |
| Claude 5 | Low | Fixed: unexpected RCPT replies are definitely unsent bounded retries without invented address-refusal evidence. |
| Claude 6 | Low | Retained and documented the one-Family-envelope choice: never silently omit a configured recipient. Added the missing unsupported shared-header regression. |
| Claude 7 | Low | Fixed with Codex 1: real crash-after-hold and hold-followed-by-failure tests verify immutable phase accounting and claim reset. |
| Claude 8 | Low | Fixed: negative mutation probes assert SQLSTATE 23514 only for the compiled MAIL outbox write, otherwise 42501. |
| Claude 9 | Low | Fixed: validation receipts below name their exact heads and distinguish failed diagnostics from passing full coverage. |
| Claude 10 | Low | Fixed: rewrapped the paragraph consistently. |
| Codex 1 | Medium | Fixed: reconciliation-phase recovery_retry events count as held attempts, alongside ordinary retryable_failure holds. |
| Codex 2 | Medium | Duplicate of Claude 1; same launch-certainty fix and real-worker clock-failure regression. |
| Codex 3 | Medium | Fixed: explicit TLS, response-bearing exception and malformed-reply classification; observed protocol outcomes survive a failing QUIT. |

Round 3, Pika `20260916-104656-947776`, reviewed `be5bbc1` through
`b1b222babe7e7fa2d05be8c17d32361e5f6becd4` (tree
`ecb6f96b896fd72e3ea003931c60bde0d15081b2`), broadening to unchanged dispatch,
process ownership and SQL contracts. Exact-path permission preflight and both
reviewers completed without degradation or verdict mismatch. All eight raw
findings (four Medium and four Low; no High/Critical) are dispositioned here.
Corrections and their validation belong to this third round under the
[delivery-cycle contract](../plans/stewardship/overall.md#automated-phase-delivery-cycle).

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1 | Medium | Fixed: explicit unobserved health never resets the circuit; helper-launch failures and pre-launch shared failures participate in cooldown. Delivery certainty remains independently preserved. |
| Claude 2 | Medium | Fixed: typed TLS EOF/closed/syscall interruptions are temporary; certificate and unclassified protocol faults are systemic. Bounded inspection handles requests/urllib3 wrappers without parsing provider prose. |
| Claude 3 | Low | Duplicate of Codex 1; shared RCPT exceptions retain prior indices and affect the circuit. |
| Claude 4 | Low | Rejected: a shared RCPT-stage failure may follow a genuine earlier RCPT 550. Neither Python nor SQL should discard that independent refusal. Added real database cases for unavailable/systemic results with an earlier permanent refusal. Unobserved outcomes, unlike shared faults, now explicitly forbid recipient evidence in both decoders. |
| Claude 5 | Low | Fixed: DeliveryCircuit.blocks_new_send names the cooldown/halt behavior explicitly at admission call sites. |
| Claude 6 | Low | Fixed: parametrized MAIL/RCPT/DATA fault tests, wrapped TLS tests, local-only circuit behavior and real unknown-delivery/systemic-halt worker coverage. |
| Codex 1 | Medium | Fixed: MAIL and RCPT connection exceptions use the shared classifier without losing earlier refusal indices; unexpected numeric RCPT replies remain per-message bounded retries. |
| Codex 2 | Medium | Fixed: UNKNOWN retains uncertain delivery while a separate closed health value triggers cooldown/halt. SQL rejects forged health/status combinations; observed acceptance survives QUIT. |

The typed TLS distinctions follow the
[Python SSL exception contract](https://docs.python.org/3.12/library/ssl.html#exceptions)
and [urllib3's wrapped-error contract](https://urllib3.readthedocs.io/en/stable/reference/urllib3.exceptions.html#urllib3.exceptions.MaxRetryError).

## Validation receipts

- Initial head `92db222`: image build, 12 container-isolation checks, 17
  runtime/provisioning/ingress/broker checks and one fake-backed configured
  Compose startup passed. The initial full database run failed five outdated
  grant assertions among 3,125 cases; its 5,888-pass baseline is diagnostic
  evidence only, not a passing full-run receipt.
- Round-1 correction head `be5bbc1`: 112 provider/private-transport tests, 73
  affected database cases and a separate 59-case dispatch batch passed. The
  full credential-free baseline passed 5,908 tests. All 3,134 database cases
  passed across eight shards, and verified combined stewardship coverage was
  93.99% lines / 85.20% branches. Ruff check/format and tracked Markdown passed.
- Round-2 corrections: 97 provider/private-transport/circuit tests, 90 affected
  real-database cases and 5,924 credential-free baseline tests passed. Ruff
  check/format and guide Markdown passed. Full final-head validation remains
  pending. The independent empty-database schema comparison was repeated after
  adding the shared-unavailable result;
  the reference still matches PR #38 and the same 401-function/409-trigger
  catalog surface remains, with only the result decoder's body changed.
- Reviewed head `b1b222b`: image build, 12 isolation checks, 17 runtime/
  provisioning/ingress/broker checks and the synthetic configured Compose
  startup passed. Its full local database rerun passed 3,140 cases but failed
  one performance assertion under eight-way local contention (lookup p95
  2.78 seconds, limit 2 seconds). The unchanged performance test passed alone:
  5,000-Family lookup p95 0.021 seconds and 100-session page p95 0.038 seconds.
  This failed full rerun is diagnostic evidence, not passing combined coverage.
- Round-3 corrections: 125 provider/private/circuit cases pass. A new independent
  fresh-schema audit again matched the PR #38 reference and updated only the
  current result-decoder fingerprint. All 90 affected database cases and 5,952
  credential-free baseline tests pass, as do Ruff check/format, all tracked
  Markdown and the model-drift check. All three review/fix rounds are complete,
  with no accepted Medium-or-higher issue unresolved and no High/Critical in
  the final round. Final exact-head CI must pass before protected merge.

## Protected delivery

[PR #39](https://github.com/epiphany40223/parishkit/pull/39) merged through the
normal protected queue on September 16, 2026, as
`9e5b6e99d1fbfd1c33e386a46525630e9dd73f0a`, verified on freshly fetched
`origin/main`. All 24 exact-head jobs passed at `7707af0`, including 3,148
PostgreSQL cases and combined coverage of 94.01% lines / 85.24% branches; DCO
also passed. All 24 merge-group jobs passed in run
[35116575720](https://github.com/epiphany40223/parishkit/actions/runs/35116575720).
The exact-head receipt is retained on the PR. These results supersede the
pending-delivery notes above, not the explicitly deferred scope.

The next [Admin-resolution increment](stewardship-family-mail-resolution.md)
starts from that verified merge. BG-06 and Gate 3 remain open.
