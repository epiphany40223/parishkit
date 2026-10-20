# Stewardship report emails

This specification defines **report emails**: Administrator-designed emails,
each made of an ordered list of self-contained report blocks, sent once or on
a repeating schedule to chosen recipients
([#1786](https://github.com/epiphany40223/parishkit/issues/1786)). Report
emails replace the two fixed Administrator digests, the Daily campaign digest
and the Weekly additional-information digest: the
[migration](#migration-of-the-daily-and-weekly-digests) converts each
campaign's Daily and Weekly digest into a report email in place, and from
then on there are no digest kinds, digest editors or digest commands.

Status: **specified, not built.** The [delivery plan](#delivery-plan) ships
it in small pull requests; the internal rework and the migration come first
and change nothing a reader sees except the converted Weekly's
[three documented changes](#conversion): the additional information block's
scope, a send every week, and the rewording of its default introduction.
Until the pull request that ships a part lands, the code that runs today is
the reference for that part, and this document describes where it is going.

General UX rules come from the [UX conventions](../ui-conventions/spec.md),
which take precedence over this specification for portal-wide rules (in-place
controls, no layout shift, browser-local times, conditional fields,
prerequisite gates, the review dialog, schedules and schedule previews,
tables and row actions). This document names the rule it follows and does
not restate it. Durable scheduling, occurrences, fulfillment, holds, mode
routing and delivery are in
[background processing](../background-processing/spec.md); this document
says only how report emails use them.

## Purpose and audience

The Administrator asked (2026-10-19) to stop having "types of emails (weekly
and daily)" and instead have one generic report email "where the admin
designates: the schedule (either one time or repeating); the email
recipients; the subject; the contents", the contents being "one or more
self-contained report blocks", taken from what the daily and weekly emails
already contain, "and maybe we'll add more options over time".

The readers are the parish's Administrators and Staff, and whoever else the
Administrator chooses to send a report to (a pastor, a finance council
member, a committee chair). The editors are Administrators, typically
mid-level IT volunteers, so every control has plain-language help.

## Administrator decisions

From the Administrator's interview of 2026-10-19 on #1786, superseding the
issue's proposed defaults where they differ. Decisions 15 to 17, made the
same day, settle the three readings this document first proposed for the
Administrator's sign-off. Each is referred to below by its number.

1. **Page.** A new **Mail › Report emails** page (New, Edit, Preview, Send a
   test to me, Send now, Pause and Resume, Delete, past sends). It replaces
   Reports › Emailed reports. Dates and mail schedules keeps Family mail
   only; the Campaign calendar still shows report sends.
2. **Recipients.** Portal users, the groups All Administrators and All
   Staff, and outside email addresses. Outside addresses are allowed and may
   receive any block, including the Family-level additional information
   block; the Administrator decides per report. Ministry leaders are not
   offered.
3. **Staff and the additional information block.** Staff may receive it,
   now, without waiting for the campaign to end
   ([#1597](https://github.com/epiphany40223/parishkit/issues/1597), which
   this decision settles).
4. **First send.** Superseded by decision 14.
5. **As of.** A per-report choice that applies to every figure in the
   report: the end of the previous day (campaign time zone), or the moment
   the email is made and sent. The converted Daily keeps the end of the
   previous day and the converted Weekly keeps "when the email is made", so
   nothing changes at deploy.
6. **Nothing to show.** A block with nothing to show says so in one line.
   The per-report option **Send only when there is something to report**
   skips the whole email (on for the converted Weekly). Amended by
   decisions 14 and 16.
7. **Send now, tests and previews** never move any "since last sent" point.
   Made moot by decision 14: no block keeps one.
8. **Pause.** A paused report does not catch up when resumed.
9. **When reports may send.** Any time until the campaign is archived.
   Saving a report whose one-time send is after the campaign ends, or whose
   repeating schedule extends past the campaign's end, asks for
   confirmation first.
10. **Limits.** Each block at most once per report; at most 20 report emails
    per campaign.
11. **Past sends** are listed on Report emails. Staff see copies only of
    reports whose blocks they may receive.
12. **New campaigns.** The campaign wizard offers Daily report and Weekly
    report as ready-made starting points.
13. **When to ship.** As soon as ready. The internal rework and migration go
   first, in a maintenance window with no report send pending; then the new
   page, as soon as it passes the Administrator's pk-local check, during this
   campaign.
14. **The additional information block lists every open request**
    (amending 4, 6, 7 and 8): every additional information request not yet
    accepted or handled (which decision 15 reads as not yet Handled or
    Ignored), on every send. It replaces "new since the last email"
    entirely, so the block keeps no "since last sent" state. The converted
    Weekly changes to this at deploy and may list requests it listed
    before. For this block, Send now, test sends, pause and the first
    send need no special rules. Its empty line is "No open requests", and
    "send only when there is something new" means "only when at least one
    block has content".
15. **Open means To review and To handle.** The additional information
    block lists every request not yet Handled or Ignored, including
    accepted requests still waiting on action.
16. **Any block counts for Send only when there is something to report.** A
    report is skipped only when no block has content. Progress blocks
    (Campaign totals, Response funnel, Participation charts, Day by day
    table) always have content, so a report containing one always sends.
    The converted Weekly (requests and Response funnel) therefore sends
    every week, its requests block showing "No open requests." when nothing
    is open. This is an accepted behavior change
    from today's Weekly, which skips an empty week.
17. **The migration rewords the Weekly's default introduction.** When the
    converted Weekly's introduction is still the built-in default ("Here
    are this week’s requests for follow-up."), the migration replaces it
    with "Here are the additional information requests that are still
    open."; an edited introduction is kept. The first Weekly after deploy
    therefore differs in that one sentence too.

Where this document had to decide something the decisions did not cover, it
says so in [Decisions made in this specification](#decisions-made-in-this-specification).

## Report email definition

A report email is one campaign schedule of the kind `report`. It keeps every
guarantee a mail schedule has
([schedule revisions and fulfillment](../data/spec.md#schedule-revisions-and-fulfillment)):
a stable `ScheduleDefinition` UUID, immutable `ScheduleRevision` values in
each applied configuration version, `ScheduleSelection` history, durable
`ScheduleOccurrence` work, semantic `ScheduleFulfillment`, explicit
`PostCloseMailResolution` skips and `RestoreDeliveryHold` coverage, and the
review, signed preview, work-lock recheck and audited configuration request
of every schedule change. The kinds `daily_digest` and `weekly_digest` no
longer exist after the [migration](#migration-of-the-daily-and-weekly-digests).

### Report values

A report revision holds these values. Every one is shown in plain words on
the page and the command line; none is a raw stored rule.

| Value | Meaning |
| --- | --- |
| `name` | Shown in lists, the calendar, Sent mail and the subject. 1–80 characters, unique within the campaign (compared case-insensitively, whitespace collapsed). |
| `subject` | The Administrator's subject, at most 254 characters, with the placeholders today's digest subjects accept (`ADMIN_DIGEST_PLACEHOLDERS`, public campaign values such as `{{ campaign_name }}` and `{{ parish_name }}`). The [subject rule](#subject) appends what it lacks. |
| `intro_html`, `intro_text` | An optional introduction above the report, sanitized and generated as every authored email part is ([content and email templates](../data/spec.md#content-and-email-templates)); file placeholders are refused, as in today's digest templates. Empty by default. |
| `recipients` | The [recipient list](#recipients): portal users, the groups All Administrators and All Staff, and outside addresses. At least one entry. |
| `schedule` | Either one time (a campaign-local date and time) or a [repeat rule](#repeating-sends). |
| `as_of` | `previous_day_end` (the end of the previous campaign-local day) or `send_time` (the moment the email is made). See [as of](#as-of). |
| `blocks` | An ordered list of `{block, version, options}`, one to six entries, each block at most once (decision 10). See the [block registry](#block-registry). |
| `content_required` | **Send only when there is something to report** (decisions 14 and 16): skip a send in which no block has content. Offered for every report ([sending only when there is content](#sending-only-when-there-is-content)). |
| `skip_dates` | Slot keys of single sends skipped ([skipped sends](#skipped-sends)). |
| `paused_ranges` | Closed and open pause intervals ([pause and resume](#pause-and-resume)). |
| `slot_basis` | `send_date` or `report_date`; fixed when the definition is created and never edited ([slot keys](#slot-keys)). |

#### Stored form

A report's values are stored with the schedule record's `campaign_id` and
`kind` (`report`), each value under its own key and every key present
(`campaigns/report_values.py` validates them):

- `recipients`: a list of entries, each `{"user": RECORD}` (the stable
  record id of a portal user), `{"group": "administrators"}`,
  `{"group": "staff"}` or `{"address": ADDRESS}` (one valid address, stored
  in lower case), with no entry twice.
- `schedule`: `{"type": "once", "date": DATE, "time": TIME}` or
  `{"type": "repeat", "frequency": "daily" or "weekly", "weekdays": [...],
  "from": FROM, "until": UNTIL, "time": TIME}`. `weekdays` (Monday 0 to
  Sunday 6, ascending, distinct) is present only for a weekly rule. `from`
  is `campaign_start`, `day_after_campaign_start` or a date; `until` is
  `campaign_end`, `day_after_campaign_end`, `first_send_after_campaign_end`,
  `archive` or a date. Dates are ISO dates and times are campaign-local
  wall-clock `HH:MM:SS` values. The bounds name send dates, whatever the
  slot basis.
- `blocks`: `[{"block": KEY, "version": 1, "options": {...}}, ...]`, the
  options holding only the values a block declares, each checked by the
  block.
- `skip_dates`: ascending slot keys, as a digest's are.
- `paused_ranges`: `[{"start": INSTANT, "end": INSTANT or null}, ...]` in
  UTC ISO form, ascending and not overlapping; only the last may be open
  (`end` null), meaning the report is paused now.
- `as_of`, `content_required` and `slot_basis` as the table above says, and
  `intro_html` and `intro_text` as strings, empty when there is no
  introduction.

The checks that span a campaign's reports (unique names, at most 20) are
made over all of its `report` records together.

Report values hold no Family data. Outside addresses are personal data of
the people they name, so they are shown only to Administrators, kept out of
logs and audit text (an audit event records the count of outside addresses
and a keyed digest of each, never the address), and leave the campaign with
it at purge.

### Slot keys

A report send's semantic slot, the key that `ScheduleFulfillment`,
`skip_dates` and `PostCloseMailResolution` name, is a campaign-local date:

- `send_date` (every report created after the migration, and the converted
  Weekly): the local date of the send.
- `report_date` (the converted Daily only): the local date the send reports
  on, the day before it sends, exactly as today's Daily digest keys its
  slots.

The basis is fixed per definition so that the converted Daily's existing
fulfillments, skips, post-close resolutions and restore holds keep naming
the same slots, while the Administrator remains free to change the report's
`as_of`. No two sends of one report fall on the same local date (a repeat
rule sends at most once a day), so the key is unique per report.

### Limits

- At most 20 report emails per campaign (decision 10), counting every
  definition not removed. The limit is refused at New report email's
  primary button in plain words ("This campaign already has 20 report
  emails, the most it can have. Delete one first."), as a
  [prerequisite gate](../ui-conventions/spec.md#prerequisite-gates), and by
  the server. Report emails do not count toward the Family schedule form
  set's limit of 100.
- Each block at most once per report (decision 10).
- At most 50 recipient entries per report, of which at most 25 outside
  addresses; a group counts as one entry. Refused at the field.
- At most 100 pause intervals per report, kept with every later revision.
- These bounds, with the six blocks, keep compile work bounded: several
  reports due at the same minute are compiled one after another by the
  existing report worker.

### Report send records

The values live in the campaign configuration document as `report`
schedule records in its `schedules` section, under a new validation schema
name (the next `campaign-series` version after `campaign-series-v11`),
admitted by the configuration's schema check the way #469 (frozen file
0075) admitted `campaign-series-v11`. A configuration saved under an earlier
schema name keeps meaning what it meant.

Each send's work is held in new tables that replace the daily and
weekly digest preparation, snapshot, ready and recipient tables for new
sends (the old tables stay, read-only, for the sends made before the
migration):

- `ReportEmailPreparation` (`stewardship_report_email_preparation`): one per
  occurrence (scheduled, recovery or manual) and its task: campaign,
  definition, revision and configuration ids, mode and rehearsal epoch, the
  covered slots of a recovery send, the recorded inputs (as-of kind and
  moment, capture instant, report day, source, submission watermark and
  fact pin), and its phase (`dates`, `cover`, `inputs`, `capture`, `fanout`,
  `complete`, `cancelled`; see [report pipeline](#report-pipeline)), with the
  same namespace and lineage guards the digest preparations have.
- `ReportEmailSnapshot` (`stewardship_report_email_snapshot`): the
  [report snapshot](#report-snapshot): the report's values, `blocks` (the
  versioned per-block JSON), whether any block has content, the fact
  generation it drew from, retained chart images, the frozen recipient
  cohort, and the compiled subject, HTML and text. Immutable once committed.
- `ReportEmailRecipient` (`stewardship_report_email_recipient`): one per distinct
  address of the cohort: the address, how it was chosen (`user`, `group` or
  `outside`, with the user or group), its outbox message, and its
  disposition (`queued`, with its message, or `revoked`, when the recipient
  failed the [recheck](#resolving-recipients) before a message was made),
  unique per snapshot and address.

A manual send's request is a `ReportEmailManualRequest`
(`stewardship_report_email_manual_request`) row keyed by its request id, so a replay returns the same send. All four tables are campaign
data: Testing cleanup, the Production-transition inventory, restore,
archive and the campaign purge include them as they include today's digest
tables, and their writes go through the same campaign read guards and
security-definer write functions.

### Report pipeline

One report send is prepared by one `report_email_prepare` task, which moves
its preparation through these phases, each committed with the work it
proves, so a retry resumes where the last attempt stopped:

1. **`dates`** (scheduled sends): the report's slots due by the
   preparation's cutoff are enumerated in pages of at most 100 candidate
   send dates through `SchedulePlan`, and each one not already covered (a
   fulfillment, a restore hold or a post-close resolution) gets its
   occurrence. The database's page proof
   (`stewardship_report_email_page_v1`) checks each page against the
   report's slot evaluation (`stewardship_report_slot_due_v1`), so no slot
   can be passed over.
2. **`cover`**: the latest pending occurrence is chosen; earlier overdue
   ones are coalesced into it (reason `missed_report_recovery`), or into one
   recovery occurrence (slot `recovery:<preparation>`) when only replaced
   revisions' sends are owed, as the digests do.
3. **`inputs`**: the send records its inputs on the preparation, once: the
   covered slots, the [as-of](#as-of) kind and moment, the capture instant,
   the report day (or none), the promoted source and the submission
   watermark, and the fact inputs its fact blocks will pin, with the source
   pinned to the preparation. A manual send's preparation starts here, with
   its manual occurrence (slot `manual:<request>`).
4. **`capture`**: the fact generation is built or reused (when the report
   has a fact block), each block captures its data, the cohort is resolved,
   the email is compiled, and the snapshot is committed with the fact pin.
   A send in which no block has content and **Send only when there is
   something to report** is on completes here with nothing to send, unless
   it is a Send now, which always sends.
5. **`fanout`**: each cohort address is rechecked and gets its own outbox
   message (purpose `admin_report`), or a `revoked` recipient row.
6. **`complete`**: the occurrence succeeds once every recipient is settled,
   with the fulfillment `delivered` (reason `report_email_complete`) or
   `empty` (`report_email_no_current_recipients`, or
   `report_email_nothing_to_report` for a send skipped as empty).
   `cancelled` ends a preparation whose report, revision, campaign scope or
   Testing epoch changed before capture.

## Schedules

The report editor follows the shared
[schedules](../ui-conventions/spec.md#schedules) rules: plain words, the
[Basic and Advanced modes](../ui-conventions/spec.md#basic-and-advanced-modes),
one [time entry](../ui-conventions/spec.md#time-entry) field, one
day-of-week picker, the shared zone note, the shared
[schedule preview](../ui-conventions/spec.md#schedule-previews) and the one
daylight-saving rule. Times are typed and shown in the browser's zone and
stored as campaign-local wall-clock values, with the shared conversion of
[New and Edit scheduled email](../admin-portal/spec.md#new-and-edit-scheduled-email).
A repeating report's time and days are converted at its next send; where the
browser's and the campaign's zones change their clocks on different dates,
the report keeps its campaign wall-clock time between them, as the digests
did. Edit posts the moment the page drew the schedule from, so a page drawn
before a daylight-saving change and posted after it still reads as
unchanged.

### One-time sends

A one-time report has a local date and time. A send time at or before now
is refused at the field; Apply rechecks it against the database clock as
every schedule review does
([#1464](https://github.com/epiphany40223/parishkit/issues/1464)). A
one-time send may be before the campaign starts, during it or after it ends,
until the campaign is archived (decision 9). Its slot key is its date.

### Repeating sends

A repeat rule is the shared repeat rule
([#469](https://github.com/epiphany40223/parishkit/issues/469),
`campaigns/recurrence.py`, `components/repeat-rule.html`) with a time, and
with bounds that may follow the campaign's dates:

- **How often:** every day, or every week on the chosen days. (The monthly
  choices of the shared rule are not offered for report emails in this
  release; a campaign lasts weeks.)
- **From:** the campaign's first day (the default), the day after the
  campaign's first day, or a chosen date.
- **Until:** the campaign's last day, the day after the campaign's last day
  (the default), the first send after the campaign's last day (for a weekly
  rule, the next chosen weekday after it, one to seven days later), a chosen
  date, or until the campaign is archived.

A bound that follows the campaign's dates moves with them, so changing the
campaign's dates re-plans the report's sends as it re-plans today's digests
([schedule replacement and removal](../background-processing/spec.md#schedule-replacement-and-removal)),
and the date-change review lists each report whose sends change. A chosen
date stays where it is.

Unlike the reminder repeat, which adds one reminder schedule per date, a
report keeps its rule: the scheduler evaluates it, as it evaluates today's
digests, through the one schedule evaluator (`SchedulePlan`), so planning,
catch-up, recovery, the preview and the calendar agree. The database's slot
check evaluates the same rule (see [database functions](#database-functions)).
An every-day rule leaves out a wholly skipped civil day (a day a time zone
change removes), as the Daily digest's evaluator does today, so no send is
invented for a day that has no moments.

Basic mode offers these presets, each named in plain words with the time
the Administrator types:

- **Every morning during the campaign** ("Every day at 7:00 AM, from the
  day after the campaign starts until the day after it ends"): every day,
  From the day after the campaign's first day, Until the day after its last
  day. With `as_of` at the end of the previous day, each send reports one
  campaign day, as the Daily digest does.
- **Every week during the campaign** ("Every Monday at 8:00 AM, from the
  campaign's first day until the first Monday after it ends"): every week on
  one chosen day, From the campaign's first day, Until the first send after
  its last day.
- **Once** (a date and time).

Advanced mode shows the full rule. A rule Basic cannot show opens in
Advanced, as the [modes](../ui-conventions/spec.md#basic-and-advanced-modes)
convention requires.

### As of

`as_of` applies to every figure in one report (decision 5):

- **End of the previous day** (`previous_day_end`). Every fact block reports
  the report day, the campaign-local day before the send, from the report
  day's end-of-day row of one exact ready `CampaignDailyFactSet`, pinned when
  the occurrence first records its inputs
  ([daily email report parity](../reports/spec.md#daily-email-report-parity),
  [#721](https://github.com/epiphany40223/parishkit/issues/721)), so the
  text, the table and the chart's last point always agree. The response
  funnel is counted at the end of the report day. A late, retried or
  recovery send never moves the report day. A send whose report day is
  before the campaign's first day has no report day: each fact block shows
  its empty statement ("No campaign day has ended yet."). A report day after
  the campaign's last day is held at the last day, as today's Compared with
  past campaigns section holds it; a report as of when it is made holds its
  report day there too.
- **When the email is made** (`send_time`). Every figure is counted at one
  capture instant, recorded when the occurrence first records its inputs,
  and never moved by a retry: the fact blocks pin the exact
  `CampaignDailyFactSet` whose submission watermark is the last submission
  committed by that instant and whose `through_date` is the current campaign
  day (its last row is that day so far), and the funnel is counted at that instant from durable
  timestamps, as today's Weekly digest counts it. Rows still committing at
  the capture instant can be missed, as the funnel's
  [as-of rule](../reports/spec.md#response-funnel) says.

Report generation waits and retries while the pinned generation is building;
a failed materialization makes the send visibly failed and retryable rather
than substituting stale or mixed facts. Later source or status changes never
rewrite a sent report.

### Skipped sends

One send of a repeating report can be skipped
([#1658](https://github.com/epiphany40223/parishkit/issues/1658)), from the
editor's [Skip this send](#skip-one-send) or the calendar. A skip adds the
send's slot key to `skip_dates` in a new revision, reviewed, applied and
audited like any other change, with today's rules unchanged: only a send
whose time is still ahead, that is a send of this report, is not skipped
already and has no Production work (a Testing rehearsal's work does not
count); every later revision keeps past skip dates; a change that moves the
report's sends drops each skip of a send still ahead that is no longer a
send, and its review lists the dropped skips; a skip cannot be undone in
this release. Every evaluator and the database's slot check, occurrence
guard and page proof honour skip dates as they do today
([schedule replacement and removal](../background-processing/spec.md#schedule-replacement-and-removal)).

### Pause and resume

**Pause** stops one report's sends without deleting it. It is a reviewed
change (a new revision) that opens a pause interval at Apply: every send due
from then on is not a send, as a skipped date is not. **Resume** closes the
interval at its Apply. The report does not catch up (decision 8): sends
whose due time fell inside a pause interval are never sent, never become
missed work and are never coalesced into a recovery send. Every later
revision keeps the closed intervals, so a later change cannot re-arm a
paused send as missed. A paused report's next send after Resume reports
exactly what any send reports: its as-of moment and every open request then
(decision 14), so nothing is lost by the pause.

Pausing is refused while a send of the report is in progress or uncertain,
as every schedule change is. A paused one-time report whose time passes
while paused never sends; its row says so ("Did not send: paused").

Pause and resume mail
([live delivery pause](../admin-portal/spec.md#live-delivery-pause)) is
separate and unchanged: it holds every report send of the campaign, and
resume's recovery plan coalesces held report sends as it coalesces held
digests today.

### Sends after the campaign ends

Reports may send until the campaign is archived (decision 9). Saving a
report asks for confirmation first, in the review dialog, when its one-time
send is after the campaign's last day, or when its repeat rule has any send
after the campaign's last day. The dialog names the campaign's last day and
the sends after it (the [schedule preview](../ui-conventions/spec.md#schedule-previews)
marks them), and Apply stays unavailable until the Administrator ticks
"Send after the campaign ends" ([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)).
The tick is bound to the signed review, so the command line asks the same
question ([command line](#command-line)). The two presets end the day after
the campaign's last day, which is a send after its end, so they ask too; the
converted Daily and Weekly were confirmed by the migration, which changes
nothing about when they send.

When the campaign closes, a report keeps sending as its schedule says. An
archive ends every report: archive inventories and resolves report sends as
[archive and post-close sends](#archive-and-post-close-sends) says, and no
send is made for an archived campaign. Return to Testing and reopen follow
the campaign lifecycle unchanged.

### Catch-up, recovery and holds

Report sends use the shared machinery unchanged:

- **Activation catch-up.** Direct Production activation's catch-up
  enumerates each report's slots through its cutoff in bounded batches and
  holds report dispatch until it completes, as it does for digests today
  ([activation catch-up](../background-processing/spec.md#activation-catch-up)).
- **Missed sends.** When services recover, the overdue sends of one report
  are coalesced into one recovery send, never a burst. Each original
  occurrence is retained as `coalesced`, references the recovery occurrence
  and writes a `coalesced` fulfillment. A recovery send of a report whose
  `as_of` is the end of the previous day covers the whole missed range of
  report days: its per-day rows (the day by day table lists every covered
  day, however many) and its end-of-range cumulative figures and chart, as
  today's recovery daily digest does, with its opening line naming the range.
  A recovery send of a report whose `as_of` is when the email is made
  reports as of its own capture instant. Either is labeled "recovery" in its
  subject.
- **Holds.** Configuration activation holds, restore holds and their
  release, the live delivery pause and the campaign work gates hold report
  sends as they hold digests today. A send covered by an unreviewed or
  assumed-delivered `RestoreDeliveryHold` is excluded from automatic missed
  work.

### Archive and post-close sends

Archive preparation and the scheduler share one inventory of report send
obligations, replacing today's daily and weekly digest obligations in
[post-close reporting obligations](../background-processing/spec.md#post-close-reporting-obligations):

- every send of every report whose due time is at or before the archive
  check and whose occurrence is not settled (missed, pending, running,
  delivery-unknown or failed without settlement) is an obligation. It is
  settled by delivery, by a recorded empty outcome, by coalescing into a
  completed recovery send, or by an explicit skip
  (`PostCloseMailResolution`, reason `admin_post_close_skip`), exactly as
  today;
- a send whose due time is after the archive check is not an obligation:
  archive's review lists each report's sends still to come and says they
  will not be sent, and archive cancels them. There is no "final weekly"
  candidate: the additional information block keeps no coverage that a
  later send must complete.

The archive and Return checks keep their locking protocol, so a report send
cannot race a lifecycle transition.

## Recipients

### Recipient kinds

A report's recipient list may hold, in any combination (decision 2):

- **Portal users** who are Administrators or Staff. A user whose only role
  is Ministry leader is not offered, and neither is a deactivated user.
- **All Administrators** and **All Staff**, resolved when each send is made.
  All Staff is the users whose role is Staff; Administrators, who also hold
  Staff's access, are All Administrators.
- **Outside email addresses**, each a single valid address. An outside
  address may receive any block.

Ministry leaders are not offered (decision 2).

### Resolving recipients

When a send captures its snapshot, right after it records its inputs (see
[report pipeline](#report-pipeline)), its recipient cohort is resolved and
frozen: the groups are expanded to the users holding the role
then, each chosen user is resolved to their current address, and the list is
reduced to distinct addresses (compared after normalization), so a person in
two entries gets one message. Each recipient remembers how it was chosen (a
user, a group, or an outside address). Each recipient gets an individual
message, so addresses are never exposed to other recipients.

Before each submission, the recipient's authority is rechecked, as today's
Administrator recheck does
([Administrator report emails](../background-processing/spec.md#administrator-report-emails)):

- a recipient chosen as a user, or through a group, must still be an active
  portal user holding Administrator or Staff (for a group, the group's
  role);
- an outside address has no portal authority to recheck. It is checked
  against the report's current revision instead: an address the
  Administrator has since removed from the report is no longer a recipient.

A recipient who fails the recheck has their unsent message safely cancelled
with `recipient_revoked`, retained as an audited withdrawal of that
obligation, never as delivery, with today's completion, settlement, retry
and reopening rules unchanged. When no recipient was accepted the occurrence
records `report_email_no_current_recipients` and an `empty` fulfillment.

### Testing redirect

In Testing every report message goes only to the configured Testing
recipient, as all campaign communication does
([mode routing](../background-processing/spec.md#mode-routing)), with
routing class `testing_override`, a "[TEST] " subject prefix, and a first
line naming who it was meant for. The line names how the recipient was
chosen: "TEST — sent to {Testing recipient} instead of Administrator
{address}." (as today), "… instead of Staff member {address}." or "…
instead of outside address {address}.". A Testing send's saved copy and
Sent mail row name the intended recipients as today. Production applies no
override.

## Compiling a report

One compiler builds every report from its blocks. The output of the two
converted reports is byte for byte today's Daily and Weekly digest output,
except the converted Weekly's documented changes
([conversion](#conversion), [gating tests](#gating-tests)).

### Shared email layout

Every report email uses today's Administrator report layout
([#720](https://github.com/epiphany40223/parishkit/issues/720)): a
desktop-first 960px column (fixed at 960px in Outlook for Windows; a
narrower window shrinks it), not Family mail's 600px column; the closed set
of inline styles, bar colours and table attributes the report validator
admits; links in the portal's link colour; buttons drawn as the portal's
primary button in a table cell; no title line and no small print. The email
opens straight into its content, in this order:

1. The **TEST** heading and line, in Testing only.
2. The Administrator's **intro**, when it is not empty.
3. The **opening line**, when the report has a fact block or its `as_of` is
   the end of the previous day: what the report is and its as-of moment,
   the only place the email names the time zone, with an "Open the Response
   dashboard" link right-aligned on the same line in a two-cell presentation
   table. Wording: "Here is yesterday’s report; all data is as of the end of
   October 6, 2026 (EDT)." (end of the previous day), "Here is the report
   for October 4, 2026 through October 6, 2026; all data is as of …" (a
   recovery range), or "Here is the report; all data is as of 7:00 AM,
   October 7, 2026 (EDT)." (when the email is made). A report day
   [held](#as-of) at the campaign's last day reads "Here is the report;
   all data is as of the end of …", since it is not yesterday's. The zone
   abbreviation is the one in force at that moment. The text part lists
   the dashboard link on the next line.
4. The **blocks**, in the report's order, except that a block placed
   [after the button](#block-contract) follows the button. A block with
   nothing to show draws its label as a heading and its empty statement
   under it (in the text part, the label and the statement on two lines).
5. The **closing button**: "Open the Response dashboard" when the report
   has a fact block (the text part repeats the link line), otherwise "Open
   protected report", linking the send's [saved copy](#saved-copies) (the
   text part: "Open protected report (staff login required): URL").
6. Blocks placed after the button.

Fact blocks are Campaign totals, Participation charts, Day by day table and
Compared with past campaigns. The Response dashboard links go to its Testing
view in a Testing send. The email never links its own saved copy except
through the closing "Open protected report" button and the additional
information block's request links. A test send and a Preview have no saved
copy, so in them the request links go to each live request on the
[Additional information](../reports/spec.md#additional-information) page
and the "Open protected report" button goes to the Report emails page;
everything else is drawn as a send would draw it.

Background output uses the date format of the configuration the send pinned,
not whichever is active when it runs
([dates and times](../ui-conventions/spec.md#dates-and-times)).

### Subject

The subject names the report, its date, the campaign and the parish, each
once. The Administrator's subject (placeholders filled) is kept and only
what it lacks is appended, as today:

- the report's name, starting with a lower-case letter ("daily report"),
  unless the subject already names a report, digest or summary as a whole
  word; when the subject already contains the name's first word, only the
  rest of the name is appended ("Annual campaign daily" gets "report");
- the report's date, unless already present: the report day (end of the
  previous day; for a recovery send, its range, "October 4, 2026 through
  October 6, 2026", as today's recovery daily) or the capture date in the
  campaign's time zone (when the email is made, as today's weekly);
- in parentheses, "manual" (Send now), "recovery" (a recovery send) and the
  campaign or parish name, each only when missing.

A subject too long for the limit (247 characters, leaving room for the
"[TEST] " prefix) loses the end of the Administrator's text, never what is
appended. An empty subject (after placeholders) becomes the report's name
as typed ("Daily report"), as today. The default subject is "{{ campaign_name }} — {name}" in lower
case after the dash, for example "Annual campaign — daily report, October 6,
2026 (Example Parish)". A test send's subject is the report's subject with
"test" in the parentheses.

### Report snapshot

Right after a send records its inputs (its as-of moment and its source,
held on its preparation), the compiler captures a report snapshot: the
report's values as of that revision, the fact-generation pin, the frozen
recipient cohort, and, per block, `{block, version, data}`, the block's captured data as versioned JSON.
Every email, retry and saved copy of the send is drawn from that snapshot,
so a later change to a block's code, a report's configuration or the
campaign's data never changes what a send said, and an old saved copy keeps
rendering after a block changes. A retry sends the same bytes, including
retained chart images. The snapshot is the report's only copy of Family
data; it is retained and purged with the campaign as today's digest
snapshots are.

### Sending only when there is content

With **Send only when there is something to report** on (decisions 14 and
16), a send in which no block has content sends no email: the occurrence
succeeds with an `empty` outcome and an `empty` fulfillment, so the slot is
not reconsidered, as an empty weekly interval is today. Every block counts.
A list block has content when it lists at least one item. A [status
block](#block-contract) (decision 16's progress blocks) always has content,
even when it draws its empty statement ("No campaign day has ended yet.")
or, in Testing, the Response funnel's Testing note, so a report with one is
never skipped. The one exception is Compared with past campaigns, which has
content only when it is drawn (it is left out with no imported years). A
report with the additional information block and the Response funnel, as the
converted Weekly has, sends every time, its requests block drawing its empty
statement. Today's Weekly digest skips an empty week; the converted Weekly
no longer does (decision 16).

The option is offered for every report, whatever its blocks, with help
that says when it can take effect: "Skips a send only when every block is
empty. Blocks that show where the campaign stands, such as the Response
funnel, always count, so a report with one always sends." It is not a
[conditional field](../ui-conventions/spec.md#conditional-fields): adding
or removing a block never shows, hides or clears it. Send now and test
sends always send.

## Block registry

Blocks live in a Python registry (`reports/report_blocks/`, one module per
block). The compiler, the editor, the saved copy page and the command line
read only the registry, so adding a block needs its module, its section in
this document, its traceability entry and its tests, and no schema change.

### Block contract

Each block declares:

- **key, label and help:** a stable key (the stored value), the label the
  editor lists, and one or two plain-language sentences of help.
- **kind:** a *list block* reports records that can run out (it can be
  empty); a *status block* reports where the campaign stands and always
  draws something, so it always has content, even its empty statement
  (Compared with past campaigns, left out with no imported years, is the one
  exception). A report is
  [skipped as empty](#sending-only-when-there-is-content) only when no
  block has content.
- **fact block:** whether it reads the pinned daily fact set (it then
  brings the [opening line and dashboard button](#shared-email-layout)).
- **placement:** in the body (every block but one), or *after the button*
  (Compared with past campaigns, which carries its own link). The editor
  keeps an after-the-button block last in the list.
- **reader role:** the lowest portal role that may open a saved copy that
  contains it. Every block below is Staff. A report's saved copies are
  readable by a role only when every block in it is (decision 11). The
  reader role does not limit recipients (decision 2).
- **options:** a small form with defaults, validated on save.
- **capture:** freezes the block's data into the snapshot as versioned JSON,
  from the send's pinned inputs only.
- **email HTML and text renderers:** emit only the validator's closed
  markup and escape every value.
- **saved copy renderer:** the block on the saved copy page.
- **empty statement:** the one line drawn when it has nothing to show.
- **Testing behaviour:** what it draws in a Testing send.
- **preview:** whether Preview and Send a test may draw it from current data
  (every block below may).

Blocks are drawn from the six existing sections, extracted from today's code
with no change to their output.

### Open additional information requests block

**List block**; reader role Staff; no options. It lists every
[Additional information](../reports/spec.md#additional-information) request
of the campaign that was submitted at or before the send's as-of moment
and is still open when the send captures its data (follow-up flags keep no
history, so whether an item was open at a past moment cannot be known).
Open means not yet Handled or Ignored (decision 15): an item that is
`current_actionable` at the
[review pipeline](../review-pipeline/spec.md#additional-information-stream)'s
**To review** or **To handle** stage, so accepted requests still waiting on
action stay listed. Handled, ignored, superseded and withdrawn items are
not listed. The stage is the one the
[stream mapping](../review-pipeline/spec.md#additional-information-stream)
derives, with each Staff revision's review decision
([#1685](https://github.com/epiphany40223/parishkit/issues/1685), schema
migration 0126): `current_actionable` and not followed up, with follow-up
needed (To handle) or not needed and not Ignored (To review). An item a
Staff member ignored, including one whose latest revision from before 0126
left follow-up not needed
([existing state](../review-pipeline/spec.md#existing-state-to-stage)), is
Ignored and is not listed.

It keeps no state between sends (decision 14): every send, scheduled,
manual, recovery or test, lists every open request so read, and
an item may be listed in many sends until someone marks it handled or
ignores it.
There is no watermark, no per-recipient coverage and no corrections
section: a request that is superseded or withdrawn simply leaves the list.

Drawn as today's weekly requests section: a heading with the count ("5 open
requests"; "1 open request"), then one table row per request, oldest
submission first, numbered 1, 2, 3 in number cells: the Family, Family DUID,
link and submitted time (no zone) on the left and the text on the right. Text
longer than 240 characters is shortened at a word boundary and ends with "…"
and a "Read the full request" link; complete text has neither. Each link
goes to the request in the send's saved copy (in a test send or Preview, to
the live request). Empty statement: "No open
requests." Testing: the same, from the rehearsal's items.

This block's scope is one of the converted Weekly's three changes at
deploy ([conversion](#conversion)): the Weekly's heading said "N new
actionable requests" (or, for a manual report, "N current actionable
requests") and listed only requests new since its last scheduled send,
followed by a corrections section. It now says "N open requests", lists
every open request and has no corrections section
([gating tests](#gating-tests)).

### Campaign totals block

**Status block**, fact block; reader role Staff; no options. Today's
**Campaign totals** section: one line per figure on a desktop, its label
(which never wraps), a bar drawn with table cells (so it shows with images
off) and the exact value, for example "Families that have responded — 10 out
of 93 (10.8%)"
([#1844](https://github.com/epiphany40223/parishkit/issues/1844)). On a
phone the label moves above its bar and value, and the value breaks only
before its parenthesis, so four-digit counts still fit a 320px phone
([#1564](https://github.com/epiphany40223/parishkit/issues/1564)). The
figures are the as-of day's first submissions, cumulative participation and
percentage, and, when financial stewardship is enabled, cumulative annual
pledges (USD) with the table column's label. Families that have responded
are drawn against the Families they are out of, and "First submissions that
day" against the busiest campaign day so far, stated beside the number; a
bar is never drawn full unless the share is complete. Pledges have no bar.
Live population figures (Members, eligible or deliverable email, comparison
pledges) are not reported, because they cannot be stated as of the end of a
past day. Empty statement: "No campaign day has ended yet." The text part
gives one "label: value" line per figure.

### Response funnel block

**Status block** (not a fact block: it is counted from durable timestamps,
not the fact set); reader role Staff; no options. Today's funnel section
([response funnel](../reports/spec.md#response-funnel),
[#477](https://github.com/epiphany40223/parishkit/issues/477)): each stage's
Families compared with the first stage, which the email names "Email
invites sent" (the dashboard's "Invited"), every stage a row with a share
bar, with no caption and no chart
([#1370](https://github.com/epiphany40223/parishkit/issues/1370)). It leaves
out the "Link followed" stage
([#1016](https://github.com/epiphany40223/parishkit/issues/1016)) and the
three figures beside the funnel
([#1532](https://github.com/epiphany40223/parishkit/issues/1532)), which the
Response dashboard and `pk-admin report responses` still show. It is counted
at the report's as-of moment: the end of the report day, or the capture
instant. The saved copy shows the stages as a table, counted again at the
same moment from the same durable timestamps, so the two agree unless the
engagement record is backfilled later. A Testing send omits the funnel,
because a rehearsal's records are cleaned up and could not be counted again,
and says instead that the Response dashboard's Testing view has it (today's
Testing note).

### Participation charts block

**Status block**, fact block; reader role Staff. Option: **which charts**,
any of First submissions of the day, Cumulative participating Families and
Effective annual pledges (the last only when financial stewardship is
enabled), at least one, default all. Today's **Daily participation** section:
the [participation graph](../reports/spec.md#participation-graph)'s chosen
panels on one date axis, drawn by its renderer as one inline PNG
(`cid:` image, 2x for sharpness) with alt text carrying the same totals; the
date axis names no zone. The block adds nothing to the text part (the alt
text carries its figures in the HTML part only), as today. The image is
retained with the snapshot, so a retry
sends the same bytes. A chart that cannot be rendered within its timeout is
left out with the alt text's figures in its place on the last attempt, as
today; a report is never left unsent for a chart. Empty statement: "No
campaign day has ended yet."

### Day by day table block

**Status block**, fact block; reader role Staff. Option: **days shown**, 1
to 14, default 5. Today's **Day by day** table: the last N campaign days up
to the as-of day (every covered day of a recovery send, however many). On a
desktop each day is one row, its date first (never wrapped), then its first
submissions, cumulative participation and, with financial stewardship,
cumulative annual pledges (#1844). On a phone each day takes two rows, the
date across the table and then its values under their headings, so it fits
a 320px phone
([#1559](https://github.com/epiphany40223/parishkit/issues/1559)). The phone
layouts come from the email's style block, as the
[UX conventions](../ui-conventions/spec.md#devices-and-browsers) require; a
mail program that drops it shows the desktop layout. The text part keeps one
line per day. Empty statement: "No campaign day has ended
yet."

### Compared with past campaigns block

**Status block**, fact block, placed after the button; reader role Staff; no
options. Today's [Compared with past
campaigns](../campaign-history/spec.md#digest-section) section
([#1698](https://github.com/epiphany40223/parishkit/issues/1698)): this
campaign on its comparison day beside last year and the imported years'
average, from the same pinned facts (the comparison day is the report day,
or, for a report as of when it is made, the last complete campaign day, as
that section's rules say), with its own charts and link to the Campaign
comparison report, its computed aggregates retained with the snapshot so its
saved copy shows it unchanged. With no imported years it is left out
entirely, as today, rather than drawing an empty statement, since the
Administrator chose it knowing it compares with imports. Left out, it has no
content for [Send only when there is something to
report](#sending-only-when-there-is-content).

## Report emails page

**Report emails** (`/admin/mail/report-emails/`) is in the **Mail and
Family portal** menu group, after Sent mail. Administrators and Staff may
open it; Staff see a read-only page. It is unavailable (greyed in the menu)
while there is no current campaign
([unavailable menu pages](../ui-conventions/spec.md#unavailable-menu-pages)).
For an archived campaign it is read-only for everyone: its past sends stay
listed and no action is offered. It replaces Reports › Emailed reports and
the Send a weekly report now page, which are removed with no redirect
([#864](https://github.com/epiphany40223/parishkit/issues/864)); the digest
editors on Dates and mail schedules are removed when its editor ships ([delivery plan](#delivery-plan)).

The page starts with its [page help](../ui-conventions/spec.md#page-help)
About panel: what a report email is, that each is made of blocks, who can
receive one, that times are this computer's, that sends continue after the
campaign ends until it is archived, and that the campaign's other
automatic mail (Family mail, receipts, alerts) is elsewhere.

### Report list

**New report email**, above the table, opens
[New report email](#new-and-edit-report-email). It is unavailable at the
[limit](#limits) with its reason after it.

The table is an [Admin table](../ui-conventions/spec.md#admin-tables) and
an [action table](../ui-conventions/spec.md#row-actions-and-confirmation),
one row per report email, in next-send order (reports with no send ahead
last, then by name). Columns: the report's name (a link to Edit), its
schedule in plain words ("Every day at 7:00 AM, from the day after the
campaign starts until the day after it ends"; "Once, Tuesday, October 20,
2026 at 9:00 AM"), its recipients in short ("All Administrators and 2
others"), the next send (browser-local), the last send with its result, a
status, and the row's actions. Status is one of Upcoming, Sending, Paused,
Done (a one-time report that sent, or a repeating report with no send
ahead) and "Needs attention" (a failed or uncertain send, linking Sent
mail). Row actions: **Edit**, **Preview**, **Send now**, **Pause** or
**Resume**, and **Delete**. Every action acts in place
([in-place controls](../ui-conventions/spec.md#in-place-controls)): a
dialog opens over the page, and Apply redraws only the changed row and the
status line, never reloading the page or moving the reader, with focus kept
on the row's control or moved as the in-place focus rule says when a row
leaves. An action the row cannot take now (Send now while a send of the
report is in progress, Pause while a send is uncertain, every action but
Preview on a report whose send is being prepared) is drawn unavailable with its reason
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)).

Which actions each status offers (an unavailable action stays in place,
greyed, with its reason):

| Status | Edit | Preview | Send now | Pause / Resume | Delete |
| --- | --- | --- | --- | --- | --- |
| Upcoming | yes | yes | yes | Pause | yes |
| Sending | unavailable (a send is in progress) | yes | unavailable | unavailable | unavailable |
| Paused | yes | yes | yes (Send now is not a scheduled send) | Resume | yes |
| Needs attention | yes, once the uncertain send is resolved | yes | unavailable until resolved | unavailable until resolved | unavailable until resolved |
| Done | yes (a later time or new rule makes it Upcoming again) | yes | yes | unavailable (nothing ahead to pause) | yes |
| Any status, archived campaign | no actions; the page is read-only | | | | |

A report can switch between one-time and repeating only while it has made
no scheduled send; afterwards the schedule kind is fixed, as a schedule's
mail type is, and the editor shows it read-only with that reason. A
one-time revision carries its resolved due instant, as a Family one-time
schedule's does; a repeating revision carries none (the schedule revision
check is widened for `report` accordingly). Both kinds key their slots by
local date ([slot keys](#slot-keys)), so a one-time report's single send
and any edit of its date keep one slot per date.

Staff see the table without the recipients column and with no actions, no
New report email and no links to Edit; each report's past sends are their
way in.

### New and Edit report email

**New report email** (`/admin/mail/report-emails/new/`) and **Edit report
email** (`/admin/mail/report-emails/<report>/`) are one Administrator page.
New first offers **Start from**: Blank, Daily report or Weekly report (the
[starting points](#campaign-wizard-starting-points)); choosing one fills the
form in place and changes nothing saved. The page has these sections, in
this order:

1. **Name** and **Subject**, with the placeholders listed in the help and
   the [subject rule](#subject) explained in one sentence ("The report's
   name, date, campaign and parish are added when the subject does not
   already say them.").
2. **Recipients** ([recipients field](#recipients-field)).
3. **Schedule** ([schedule fields](#schedule-fields)) and **As of**, a
   [binary choice](../ui-conventions/spec.md#binary-choices): "End of the
   previous day (campaign time zone)" or "When the email is made", with help
   saying it applies to every figure.
4. **Blocks** ([blocks field](#blocks-field)) and **Send only when there is
   something to report**, a checkbox always shown, with help saying it
   skips a send only when every block is empty
   ([sending only when there is content](#sending-only-when-there-is-content)).
5. **Introduction** (optional), the HTML and plain-text editor every
   authored email part uses, with "Generate plain text from HTML".
6. On Edit, the report's **past sends** and **Delete**.

The primary button reads **Add the report email** on New and **Review
changes** on Edit; it is unavailable until the form differs from what it
was drawn with, with a short hint after it, as New and Edit scheduled email
are. **Preview** and **Send a test to me** sit beside it and draw the form
as it is now, unsaved. **Cancel** returns to the list without saving.

Field errors follow the
[field error conventions](../ui-conventions/spec.md#field-errors-and-live-checks):
a value the server refuses is shown at its field, with the error summary,
and the review dialog stays shut; a stale page, another Administrator's
change or work that blocks the change is shown in the dialog.

### Recipients field

One combined picker. Typing filters the campaign's eligible portal users
(Administrators and Staff, by name and address) and the two groups; Enter or
a click adds the highlighted choice. Text that is a valid email address and
matches no user adds an outside address, after one plain confirmation line
under the field: "This address has no portal account. It will receive the
whole report, including any Family names and request text." Each chosen
entry is a removable chip naming what it is ("All Staff (group)", "Pat
Doe — Staff", "council@example.org — outside address"). The field rejects a
duplicate entry and an invalid address at the field. The groups' current
members are counted beside each group chip ("All Staff (group, 4 people
now)"); the count is read when the page is drawn. Ministry leaders and
deactivated users are never offered.

### Schedule fields

Basic mode offers the three [presets](#repeating-sends) as a radio group,
each with its time field (and, for the weekly preset, its day). Advanced
shows the full rule: how often, the days, From and Until with their campaign
choices and dates, and the time. A one-time report has a date and time. The
zone note names the browser's zone. Under the fields, the shared
[schedule preview](../ui-conventions/spec.md#schedule-previews) lists the
next five sends in its compact layout and count-from-now scope, redrawn in
place as the fields change, in a region that keeps its place
([no layout shift](../ui-conventions/spec.md#no-layout-shift)). Each line
names the send and, for a report as of the end of the previous day, the day
it reports on ("Daily report for Monday, October 19"); sends after the
campaign's last day are marked "after the campaign ends"; skipped and paused
sends stay listed, struck through, with their reason in words. While live
delivery is paused, the preview shows its paused notice.

### Blocks field

The blocks are an ordered list. **Add a block** is a select of the blocks
not yet in the report, with each block's help shown under the select when it
is chosen; a block already in the report is not offered (decision 10), and
the control is unavailable with its reason when all six are in. Each block
in the list is a row with its label, its help, its options (if any) and
**Move up**, **Move down** and **Remove** buttons; there is no drag-only
control ([accessibility](../ui-conventions/spec.md#accessibility-and-client-behavior)).
Moving or removing acts in place and keeps focus on the moved row's button
(or, after Remove, on the next row's Remove or on Add a block), announcing
the new position. Move up on the first row and Move down on the last are
unavailable with their reason, and an after-the-button block is always last
and has neither, with help saying why ("This block always follows the
dashboard button."). A report needs at least one block; removing the last
one is refused at the field on Review.

### Review and Apply

**Review changes** (or Add the report email) opens the
[review dialog](../ui-conventions/spec.md#review-dialog) with the server's
review of the complete change, built, validated, previewed and signed
exactly as the command line's [report-email preview](#command-line) builds
it: the report's values before and after in plain words (recipients by
name and kind, blocks in order with their options, the schedule and as-of),
the sends each side makes with the shared schedule preview, the dropped
skips, the affected work (sends already made are not recalled; planned sends
not started are cancelled; sends in progress or uncertain block the change,
and then no Apply is offered and Sent mail is named), and the
[after-the-campaign confirmation](#sends-after-the-campaign-ends) when it
applies. **Apply** records the change with the signed preview, the
blocking-work recheck under the work lock, the stale and two-tab checks,
the recheck of a reviewed send time that has passed, and an audited
configuration request, as every schedule review does
([new and edit scheduled email](../admin-portal/spec.md#new-and-edit-scheduled-email)).
Once applied, the reader returns to the list, whose status line says what
was applied and when ("Report email added.", "Report email changed."). An
edit applies to later sends only; a send already prepared keeps its
snapshot.

### Preview

**Preview** (a row action, and a button on the editor for the unsaved form)
draws the report from current data in the review dialog, marked "Preview —
not sent", without saving or sending anything. It uses the report's as-of
choice against now (the end of yesterday, or now), compiles every block as
a send would, and shows the email as recipients would see it (HTML, with a
tab for the plain text), the subject, and the recipients it would go to now.
It needs no fact pin: it reads the latest ready fact generation, and says
"Updating" if that generation is not current, as the Response dashboard
does. The dialog body scrolls. Preview records an audit event (it reads
Family data) and creates no occurrence, outbox row or saved copy.

### Send a test to me

**Send a test to me** sends one copy of the report as it would be drawn now
(the saved report from a row, the unsaved form from the editor) to the
signed-in Administrator's own address, or to the Testing recipient in
Testing, labeled test in its subject and first line. It acts in place: a
reserved status line after the button says "Test sent to you at 9:42 AM."
(browser-local), or why it is unavailable or refused. It creates no
occurrence, fulfillment or saved copy, never counts as a send of the
report, is limited to one pending test per Administrator and report, and is
audited. Its Sent mail row has the purpose `admin_report_test`.

### Send now

**Send now** sends the saved report to its recipients now, outside its
schedule, labeled "manual" in its subject. It opens a
[confirmation](../ui-conventions/spec.md#row-actions-and-confirmation)
dialog ("Send Weekly report now?") that names the recipient count and,
when the report has the additional information block, says it lists every
open request, including requests earlier sends listed. **Send now** in the
dialog creates one manual occurrence (its occurrence key comes from the
request id; it has no semantic slot), with the same campaign, pause, restore,
unresolved-delivery, recipient and Testing rules as scheduled sends; it
never creates or satisfies a scheduled slot's fulfillment. Replaying the
same request returns its original send, not a second one. The confirmation
is bound to the report's current revision; a changed report refuses it as
stale. Send now is unavailable while another send of the report is being
prepared or delivered. The row's last send then shows the manual send, and
the status line links its saved copy once it is made.

### Skip one send

On Edit of a repeating report, each send still ahead in the schedule
preview that may be skipped (the [rules](#skipped-sends)) has **Skip this
send**. It opens the review dialog with the report's change and names the
skipped send by its browser-local time (and, as of the end of the previous
day, the day it reports on). Apply records it as any change; the status
line says "Send skipped." The calendar's link to a send opens Edit with that
send named (`?send=<slot key>`), its Skip this send focused. As today for a
digest send: skipping a send whose time has passed, or that has Production
work (prepared, sending, sent or failed), is refused (403) with nothing
recorded, and so is one skipped again (409, a stale page); a send that is
not one of the report's is refused as an unknown request (400). The
reviewed send's time is signed into the review, so Apply refuses under the
work lock once it has passed, and the review's work fingerprint refuses
once any work for the report has changed since it was drawn.

### Pause and Resume actions

**Pause** and **Resume** are row actions and Edit buttons. Each opens a
confirmation in the review dialog: Pause says that no send is made while
paused and that sends missed while paused are not made later; Resume names
the next send. Apply records the change in place; the row's status becomes
Paused, or returns to its schedule status. A paused report's row keeps its
schedule shown, with "Paused since 9:00 AM, October 20" under it.

### Delete a report email

**Delete** (a row action, and a section of its own at the bottom of Edit)
opens the review dialog: the report's sends still to come stop, sends
already made are not recalled, planned sends not started are cancelled, and
sends in progress or uncertain block it. Apply removes the definition (its
removal marker), in place on the list ("Report email deleted."), or from
Edit back to the list. Past sends, their saved copies and their Sent mail
rows stay, listed under **Deleted report emails**, a collapsed
[disclosure](../ui-conventions/spec.md#disclosures) under the table, so
history is never lost.

### Past sends

Each report's past sends are listed on its Edit page (for Staff, under the
report's row, in a disclosure) newest first, 20 at a time with the table
navigator: when it was made (browser-local), what it was (scheduled,
manual, recovery), the as-of moment it reported, its recipient count, its
result (Sent, Not sent: nothing to report, Failed, Sending) and a link to
its saved copy. A list block's count is shown beside it ("12 open
requests"). The sends of the converted Daily and Weekly made before the
migration are listed with their report, linking their existing saved
copies.

### Saved copies

Every send that made an email has a **saved copy**, its report page at
`/admin/mail/report-emails/sends/<send>/`, drawn from its snapshot: the
same blocks in the same order, as web components (the funnel as a table,
the charts through the chart engine, the totals as
[stat tiles](../ui-conventions/spec.md#stat-tiles), the requests with links
to each request's own page at `…/sends/<send>/items/<item>/`, which links
the live Additional information request). It also states "All data is as of
…" (without "yesterday"), the ParishSoft data age and connection line as
recorded when the email was made, how it was sent (scheduled, manual or
recovery; a test send has no saved copy) and its recipients by kind (names for users
and groups; outside addresses only to Administrators). Its access is
audited (`report_email_viewed`).

Who may open it: every Administrator; Staff only when every block in it has
reader role Staff (decision 11), which holds for every block today, so Staff
may open every saved copy, including those with the additional information
block (decision 3). Ministry leaders may not. An outside recipient cannot
open it (no portal account); the email itself is their copy.

Saved copies made before the migration keep their pages and addresses
unchanged: the daily report page `/admin/reports/emailed/daily/<snapshot>/`
and the weekly report pages `/admin/reports/emailed/weekly/<snapshot>/` and
`…/items/<item>/`, which links in emails already sent point to. Their Staff
access becomes that of the new pages (Staff may now open the weekly pages).
Their list, `/admin/reports/emailed/`, and Send a weekly report now are
removed with no redirect.

### Campaign calendar and other pages

- **Campaign calendar.** The
  [Campaign calendar](../admin-portal/spec.md#campaign-calendar) still shows
  every report send, expanded by the same evaluator, named by the report's
  name, with skipped and paused sends marked in words. A report send still
  to come links to its report's Edit page with the send named; report sends
  are not movable. Add on a day adds only Family mail.
- **Dates and mail schedules** lists Family mail only and links to Report
  emails in its About panel and under its table ("Report emails to
  Administrators, Staff and others are on Report emails.").
- **Sent mail** names a report message by its report's name and kind
  (scheduled, manual, recovery, test) and links its saved copy.
- **Background work** names report tasks by their report.
- **Pause and resume mail** lists held report messages under "Report
  emails", and its Resolve held messages workflow names the message type
  "Report emails".

## Campaign wizard starting points

The campaign wizard's schedule step and New report email offer two starting
points (decision 12), each the converted report's values with no time chosen
yet:

- **Daily report:** Every morning during the campaign, as of the end of the
  previous day, to All Administrators, with Campaign totals, Response
  funnel, Participation charts (all three) and Day by day table (5 days);
  subject "{{ campaign_name }} — daily report"; no introduction.
- **Weekly report:** Every week during the campaign on Monday (until the
  first Monday after it ends), as of when
  the email is made, to All Administrators, with Open additional information
  requests and Response funnel, Send only when there is something to report
  on; subject "{{ campaign_name }} — weekly report"; no introduction.

In the wizard (#1404), the step lists them under "Suggested report emails",
each with a checkbox and a time field, marked "Not saved yet"; **Add these
schedules and continue** adds the ticked ones with the Family schedules in
the step's one save. Elsewhere, Start from fills New report email's form.
Neither is required for go-live: Go-live readiness no longer checks digest
schedules.

## Command line

`pk-admin report-email` reaches every action of the page under the same
authorization, service functions, checks, signed previews, confirmation and
audit ([admin automation](../admin-automation/spec.md#reports-and-exports)).
It replaces `pk-admin digest list`, `digest daily`, `digest weekly` and
`digest weekly-request`, which are removed with no aliases, and the digest
entries of the schedule commands (a daily digest's `comparison`, a digest's
`skip`, and digests in `schedule show` and `schedule preview`).

| Command | Does |
| --- | --- |
| `report-email list [--campaign C]` | The Report emails table: each report's id, name, schedule in plain words, recipient count, next send, last send and status. Any session; Staff get the read-only fields. |
| `report-email show REPORT` | One report's values (recipients by kind; outside addresses to Administrators only), blocks with options, skip dates, pause intervals, the configuration `version`, and its next five sends. |
| `report-email preview --expected-version V --changes FILE` | The review of a change document: `create`, `edit` (by `id`, keeping what it leaves out), `delete`, `pause`, `resume` and `skip` (one slot key) entries, validated, previewed and signed as the page does; prints the review and its token, and whether the after-campaign confirmation is required. |
| `report-email confirm TOKEN` | Applies a previewed change; the after-campaign confirmation is asked at the [prompt](../admin-automation/spec.md#command-line-confirmation) or given by `--yes`, else exit 4 (`confirmation_required`). |
| `report-email render REPORT` | Preview: the report drawn from current data, its subject and text part (never the HTML), and who it would go to now. Records the page's audit event. |
| `report-email send-test REPORT` | Send a test to me. |
| `report-email send-now REPORT --command-id ID` | Send now, keyed by its command id, with the confirmation at the prompt or `--yes`. |
| `report-email sends REPORT` | Past sends, as the page lists them, ids and dates only. |
| `report-email copy SEND` | A saved copy, read from its snapshot through its page's functions: each block's figures, and for the additional information block each request's identifier and states, never a Family's text. Reads pre-migration daily and weekly copies too. Records `report_email_viewed`. |

Other commands change with it: `delivery list --purpose` accepts
`admin_report` and `admin_report_test` (and still the old `daily_digest`
and `weekly_digest` for history), and the Resolve held messages commands'
`--type` takes `report` in place of `daily_digest` and `weekly_digest`
([family email sends and delivery controls](../admin-automation/spec.md#family-email-sends-and-delivery-controls)).

A full-scope session is needed for `preview`, `confirm`, `send-test` and
`send-now`; the reads take any scope. Exit codes and output follow the
[command shape](../admin-automation/spec.md#command-shape).

## Migration of the Daily and Weekly digests

Forward migrations with frozen SQL files, under the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy)
(migration numbers are assigned when each pull request is opened): a
pipeline file that only adds, then one conversion file that converts the
rows and rewrites the database functions together
([database functions](#database-functions)), with a new campaign
configuration validation schema name, following the pattern #469 used. It
changes no output at deploy except the converted Weekly's three documented
changes ([conversion](#conversion)).

### Conversion

For every campaign that has a Daily or a Weekly digest schedule (every
lifecycle state, both modes), the migration converts each **in place**,
keeping its `ScheduleDefinition` UUID, so its occurrences, fulfillments,
skip dates, post-close resolutions, restore holds and recovery lineage all
still apply. It writes one new revision and selection per definition, in a
new applied configuration version recorded and audited as a configuration
change (actor `system`, reason "#1786 conversion"). Requirements the
implementing pull request (delivery PR 4) must meet, and record in its
design notes before code:

- **The YAML document and the database stay in step.** The converted
  document is built by Python from each campaign's applied document (the
  frozen SQL file never parses YAML) and installed through the
  configuration installer's normal journaled path in the upgrade's
  configuration step, so the YAML and database digests match afterwards; the
  frozen file converts and checks the database rows against it. The PR
  states the exact ordering against the migration run.
- **No re-plan and no hold.** The new revision covers exactly the slots the
  old one did, so the replacement cancels nothing (the migration refuses to
  run while any digest work is open; see below), creates no occurrence, and
  sets no activation hold; the gating test asserts the next planned sends
  are unchanged.
- **Every lifecycle state.** Draft, active, closed and archived campaigns
  are converted, in both modes. Archived campaigns' write guards refuse
  ordinary changes, so the conversion uses a migration-only guarded path
  that changes only the schedule kind and values and nothing else.
- **Fail forward.** A release from before the conversion cannot read
  `report` rows, so there is no rollback to it short of restoring the
  pre-upgrade backup with the previous release, as the
  [production upgrade](../operations/spec.md#production-upgrades) rules
  allow. Older configuration versions that still name digest kinds remain
  readable as history, and their saved copies keep rendering; they are never
  re-applied.

| Value | Converted Daily | Converted Weekly |
| --- | --- | --- |
| `name` | Daily report | Weekly report |
| `subject`, intro | its saved subject, and the intro of the email template it sends; an intro that still holds a built-in default, current or retired, becomes empty (the email already leaves such an intro out, so the output is unchanged) | its saved subject, and the intro of the email template it sends; an intro that still holds a built-in default, current or retired, becomes "Here are the additional information requests that are still open." in both parts (decision 17); any other intro is kept as written |
| `recipients` | All Administrators | All Administrators |
| `schedule` | every day at its saved time, From the day after the campaign's first day, Until the day after its last day | every week on its saved weekday at its saved time, From the campaign's first day, Until the first send after its last day (the one post-close weekly slot today's evaluator can give) |
| `as_of` | `previous_day_end` | `send_time` |
| `blocks` | Campaign totals, Response funnel, Participation charts (all three), Day by day table (5 days), then Compared with past campaigns if it was on | Open additional information requests, Response funnel |
| `content_required` | off | on |
| `skip_dates` | kept (report days) | kept (send days) |
| `slot_basis` | `report_date` | `send_date` |

The Daily's schedule reproduces today's rule exactly: a send the morning
after each active campaign day, including the day after close. The Weekly's
sends are today's slots, including the post-close slot on the first chosen
weekday after the campaign's last day.

The converted Daily's output is byte-identical. The converted Weekly
changes in exactly three ways at deploy, each an accepted behavior change;
everything else in it stays byte-identical:

- **Scope** (decisions 14 and 15): its requests block lists every open
  request (To review and To handle) on every send, under "N open requests",
  with no corrections section
  ([the block](#open-additional-information-requests-block)).
- **It sends every week** (decision 16): it keeps Send only when there is
  something to report on, but its Response funnel always has content, so
  it is never skipped as empty; a week with nothing open sends with the
  requests block's "No open requests." line. Its post-close slot, today
  considered only while items or corrections are still owed, is now an
  ordinary send and is made too.
- **Its default introduction is reworded** (decision 17): an introduction
  still holding a built-in default, current ("Here are this week’s requests
  for follow-up.") or retired, becomes "Here are the additional information
  requests that are still open."; an edited introduction is kept.

The digest email templates the two schedules sent are kept, unchanged, for
the saved copies that name them; the daily and weekly digest email types
leave Pages and emails, which no longer offers them, and new campaigns have
none. Old daily and weekly snapshots, preparations and recipients are kept
as they are and keep rendering their saved copies.

### Database functions

The digest kinds are named throughout the installed SQL. The baseline files
`daily_digests.sql`, `daily_dispatch.sql`, `weekly_digests.sql`,
`weekly_capture.sql`, `weekly_completion.sql`, `weekly_dispatch.sql`,
`weekly_fanout.sql` and `weekly_manual.sql` hold the two digest pipelines
(page proofs, preparation, snapshot, ready and recipient guards, mail writes,
completion and settlement, manual allocation). Shared functions also branch
on the kinds or the purposes: in `functions.sql` the occurrence guard
(`stewardship_occurrence_guard_v1`), the canonical slot check
(`stewardship_catchup_due_v1`), the catch-up receipt and occurrence shape
checks, the post-close, fulfillment and checkpoint guards, recovery
replacement, schedule activation, the campaign and content completeness
checks, the setup completion write and the task-type login check; the work
row views and cancellation functions in `schedule_reconciliation.sql`; the
closed-campaign, recovery, control and resolution functions in the
`delivery_*.sql` files; the outbox purpose checks in `guards.sql`; the
Testing cleanup and Production-transition inventories; and a few single
functions (activation impact, exact fact pins, fact runtime binding,
Family dispatch).

The work is split across two frozen files, each with its own forward
migration:

- **The report pipeline file** (delivery PR 3) adds the
  [report send records](#report-send-records) tables, their guards and
  write functions, the report page proof and the report slot evaluation,
  all with plain `CREATE`, and the task type they need
  (`report_email_prepare`, added to the two task-type maps, the only
  existing objects it replaces; the metadata finalizer's
  `report_email_finalize` comes with the scheduler's producers in the
  conversion file). Nothing uses them
  yet: the shared guards that would admit the pipeline's occurrence,
  outbox, pin and cleanup writes still admit only the digests until the
  conversion file rewrites them.
- **The conversion file** (delivery PR 4) converts the rows and, in the
  same file, replaces every shared function above that names a digest kind
  or purpose with a report-aware body copied whole from the baseline, which
  carries the final definitions (the implementing pull request lists the
  complete inventory in the file's header, found by searching the installed
  catalog, not only the baseline files). It widens the schedule kind and
  revision checks to `report` and drops `daily_digest` and `weekly_digest`
  from them after converting the rows; adds the outbox purposes
  `admin_report` and `admin_report_test`, keeping the old purposes valid for
  history; adds the new validation schema name; and makes the digest
  pipelines' write functions refuse (no digest kind remains to write for)
  while their read functions keep serving old saved copies. The new bodies
  evaluate the stored repeat rule, slot basis, skip dates and pause
  intervals exactly as `SchedulePlan` does, and one shared case table pins
  the two together, as `dst_cases.json` pins the daylight-saving rule.

Each file repeats every `ALTER FUNCTION … SECURITY DEFINER` the baseline sets
for a function it replaces, and ends with a `DO` block that raises unless
everything it installs is present (each new or replaced body, `prosecdef`
for each definer function, the checks and purposes) and, for the conversion
file, every former digest definition now has kind `report` and no revision
names a digest kind.

The conversion file refuses to run while any digest occurrence is
`pending`, `running` or `delivery_unknown`, or any digest outbox message is
not terminal, naming the counts, so a send is never converted mid-flight;
the operator waits for the send to finish and runs the upgrade again. The
Python that reads `report` rows ships in the same release as the conversion
file, so no release reads a `report` row with digest code.

### Gating tests

The migration's pull request carries a test that compiles each converted
report from fixture data and requires the same subject, HTML and text as the
old digest compiler, byte for byte, in Production and in Testing, for: a
single-day Daily; a recovery Daily covering several days; a Daily with
Compared with past campaigns; a Daily with a default and an edited intro; a
manual and a scheduled Weekly, each with the current default, a retired
default and an edited intro; and a Testing Daily and Weekly. The only
permitted differences are the converted Weekly's documented changes: the
test builds the expected Weekly output by applying exactly those changes
(the heading wording, every To review and To handle request listed, no
corrections section, and the reworded current or retired default intro, an
edited intro staying as it was) to the old compiler's rows, and fails on any
other difference. It also asserts that a Weekly with no open request now
sends, with the "No open requests." line, where the old compiler recorded an
empty interval. A second test runs the migration on a database seeded with
both digests, their occurrences, fulfillments, skip dates and a post-close
resolution, and asserts that the next sends planned are the same instants
and slot keys as before. The upgrade-parity, frozen-file digest,
function-equality and `SECURITY DEFINER` tests of the schema policy apply
unchanged.

### Deploy conditions

The migration ships in a maintenance window with no report send pending
(decision 13); the deploy precheck lists any pending, running or uncertain
digest occurrence and outbox message, and the upgrade is not started while
any exists. Nightly release windows do not move report send times. The
release notes tell operators to run the scripted upgrade with
`STEWARDSHIP_SCHEMA_CHANGE=1` and that Reports › Emailed reports moves when
the page ships.

## Delivery plan

Small pull requests, in order. Each updates its specs and tests first,
carries traceability entries for new headings, refers to #1786 without
closing it until the last, and ships each page action with its
`pk-admin report-email` command in the same pull request, as the
[rules for new Admin actions](../admin-automation/spec.md#rules-for-new-admin-actions)
require.

1. **Spec** (this document and the cross-links), no code.
2. **Block registry and one compiler**: extract the six blocks from today's
   daily and weekly compilers into `reports/report_blocks/`, with one
   compiler and the shared layout, still driven by today's digest schedules
   and with byte-identical output (the gating tests' byte-identical cases,
   without the converted Weekly's three changes). No schema change; no
   visible change.
3. **Report pipeline**: the pipeline frozen file (new tables, guards,
   write functions, page proof and slot evaluation), the report values'
   validation, the repeat rule with campaign bounds, slot basis and pause
   intervals in `SchedulePlan`, and the report preparation, capture, fanout,
   recheck and completion code, all unused until PR 4. No visible change.
   Its tests exercise the new objects directly (the slot evaluation against
   `SchedulePlan` through one shared case table, the guards' refusals, the
   cohort and request reads) and the Python pieces that need no shared
   guard; the end-to-end send tests come with PR 4, whose rewritten shared
   guards are the first to admit a report's writes.
4. **Conversion**: the conversion frozen file with its function rewrites and
   the conversion; the scheduler, catch-up, recovery, live pause recovery,
   archive inventory, Testing cleanup, Go-live readiness and activation
   counts (`campaigns/readiness_digests.py`, `reports/readiness_weekly.py`,
   which counted weekly messages from the watermark) on `report`; the outbox
   purposes; the additional information block's scope change, the converted
   Weekly sending every week and the rewording of its default intro. New
   scheduled email and the date-change review stop offering the digest mail
   types (no digest kind exists to add). Ships in a maintenance window with
   no send pending. Until PRs 5 and 6 the existing pages and commands work
   on the converted reports: Dates and mail schedules' digest editors edit
   their time, weekday, comparison block and skips; Emailed reports lists
   their sends, old and new; Send a weekly report now is Send now of the
   report converted from the Weekly; and the digest commands do the same.
5. **Report emails list and sends**: the Report emails page's list, status
   and action matrix, Send now, Pause and Resume, Delete, past sends and
   saved copies, with `report-email list`, `show`, `sends`, `copy`,
   `send-now` and the `preview`/`confirm` entries for `pause`, `resume`,
   `delete` and `skip`. Until PR 6, a row's Edit and the calendar's link to
   a send open the existing digest editor on Dates and mail schedules for
   the converted reports (its Delete with This email only is the page's
   skip), and until PR 7 the row has no Preview; the calendar, Sent mail and Pause and
   resume mail names; the delivery commands' purposes and types. Removes
   Reports › Emailed reports, Send a weekly report now and `digest list`,
   `digest daily`, `digest weekly` and `digest weekly-request`. Needs the
   Administrator's pk-local check.
6. **New and Edit report email**: the editor with the recipients, schedule,
   as-of and blocks fields, review and Apply, the after-campaign
   confirmation and Skip this send (the calendar's send links move to it), with the `create` and `edit` entries of `report-email
   preview`/`confirm`, and the Participation charts block's "which charts"
   option, the first place a report can choose its charts (until then
   every report draws all three). Removes the digest editors on Dates and mail
   schedules and the schedule commands' digest entries. Needs the
   Administrator's pk-local check.
7. **Preview and Send a test to me**, with `report-email render` and
   `send-test`.
8. **Campaign wizard starting points**, and Start from on New.

Issue #1597 is settled by decision 3; it is closed by PR 6, the first that lets an
Administrator add Staff as recipients.

## Decisions made in this specification

Decisions the Administrator's answers did not cover, made here with the
reason; the Administrator may change any of them.

- **Where it lives.** A specification of its own, since report emails span
  background processing, data, the Admin portal, reports and automation;
  those specs link here instead of repeating it.
- **No corrections section.** Corrections are dropped with the "since
  last sent" state, because a request that is no longer open simply leaves
  the list.
- **"Something to report", not "something new".** The option is labeled
  "something to report" rather than the issue's "something new", since no
  block reports what is new any more.
- **The option is offered for every report** (following decision 16). It can
  take effect only when every block is a list block or Compared with past
  campaigns, so it could have been shown only then; but a field that appears
  and clears as blocks are added would hide the converted Weekly's saved
  setting and surprise an editor. One always-shown checkbox, whose help says
  it skips a send only when every block is empty, is clearer.
- **Retired Weekly default intros are reworded too.** Decision 17 names
  today's default; an intro still holding an earlier built-in Weekly
  default (from before #720) was never edited either and is just as out of
  date, so the migration treats it as the default, as the Daily's
  conversion does.
- **Opening line and closing button.** Kept exactly as each digest draws
  them: a report with a fact block (or as of the end of the previous day)
  gets the opening line and the Response dashboard button; otherwise the
  "Open protected report" button to the saved copy. Compared with past
  campaigns stays after the button and therefore last in the list, as it is
  today.
- **Slot basis fixed per report.** The converted Daily keeps keying its
  sends by the day reported so its history still matches; every other
  report keys by the send date, which leaves the as-of choice freely
  editable.
- **Subject and introduction belong to the report.** They are report
  values, not a separate email template on Pages and emails, so a report is
  edited in one place; the two digest email types leave Pages and emails.
- **Repeat rule bounds follow the campaign.** From and Until can follow the
  campaign's first and last days (or the day after), so the converted Daily
  and Weekly keep moving with the campaign's dates as today; monthly repeats
  are not offered.
- **Archive ends sends.** Sends due before archive must be settled as
  today; sends after it are listed in archive's review and cancelled. There
  is no longer a post-close weekly obligation, since nothing is owed.
- **Recipients.** Portal users are limited to Administrators and Staff;
  outside addresses are rechecked only against the report's current
  recipient list; addresses are deduplicated; at most 50 entries and 25
  outside addresses per report.
- **Staff on the page.** Staff may open Report emails read-only, to reach
  saved copies, as they reached Emailed reports.
- **Old saved copies keep their addresses** so links in emails already sent
  still open; only the list page and Send a weekly report now are removed.
- **Test sends in Production** go to the signed-in Administrator's own
  address; they are never saved copies.

## Rejected alternatives

- **Keep the two kinds and add checkboxes to each.** No one-time reports,
  chosen recipients or chosen blocks; the request is to drop the types.
- **A template language with block placeholders in the HTML body.** Hard
  for a mid-level IT Administrator, and it would loosen the closed markup
  validator. An ordered list of blocks is easier and safer.
- **A standalone report table outside the campaign configuration.** It
  would lose the review, audit, configuration versions, catch-up, pause and
  restore holds and post-close handling that schedule definitions have.
- **Create new definitions at the migration and remove the old ones.** It
  breaks the UUID lineage: fulfillments, skips and holds would no longer
  apply and the converted reports could send a slot twice.
- **Keep `daily_digest` and `weekly_digest` as permanent kinds read as
  reports.** Kind-specific branches would stay in Python and SQL forever,
  and a converted report could never gain blocks.
- **Expand a repeat rule into one schedule per send, as reminders do.** A
  report would become dozens of definitions, each with its own history, and
  a change to the time would touch every one.
- **Count only list blocks for Send only when there is something to
  report.** It would have kept the converted Weekly skipping empty weeks,
  but decision 16 counts every block.
- **Open means To review only.** Accepted requests still waiting on action
  would have left the list; decision 15 keeps them listed.
- **One shared "last sent" cursor, or one per report, for the additional
  information block.** Superseded by decision 14: listing every open
  request needs no cursor and never hides a request from a recipient.
