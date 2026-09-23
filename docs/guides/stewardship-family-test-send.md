# Stewardship chosen-Family test sends

The [Admin portal
specification](../specs/stewardship/admin-portal/spec.md#production-transition)
describes a readiness test send that could render a selected Family, but only
the fictional sample existed, and the only way for staff to hold a Testing
code or link was the scheduled Testing invitation run, which mails every
Family with a deliverable address to the one Testing mailbox. This increment
adds the page **Send this email to chosen Families**, linked from the
fictional sample's test page as **Send this email to chosen real Families
(Testing recipient only)**, so staff can validate the Family form as up to ten
real Families at a time. The [deployment
runbook](stewardship-deployment-runbook.md#staff-validation-checklist) says
how staff use it.

## Design

- **A distinct outbox purpose.** Each send is an outbox message of purpose
  `family_test`, routed `testing_override` in the rehearsal credential
  namespace. Every scheduled-Family path filters on the `initial` and
  `reminder` purposes, and the occurrence guard ties a sender to its
  occurrence by semantic key, so a test message can never create, move or
  satisfy an occurrence or a fulfillment; the SQL additionally stops a
  `family_test` sender from writing occurrence or fulfillment rows.
- **A ticket per Family.** The web request records one ticket per chosen
  Family (`stewardship_family_mail_test`) and queues a `family_mail_test`
  task. SQL admits the ticket only for an Administrator whose sign-in is at
  most five minutes old, in Testing mode on the current draft campaign with a
  live Workspace integration and an active Testing credential set, for a
  Family that is active, eligible, deliverable and current, with at most ten
  tickets or messages in progress per campaign. Once a ticket leaves `queued`
  its Family is erased from it; only the outbox message, which Testing cleanup
  deletes, keeps the link.
- **The general worker prepares it.** Under its claim, the worker issues or
  reuses the Family's Testing credential (not date-gated, so it can run before
  the invitation is due; scheduled Testing mail later reuses the same
  credential), renders the chosen template for that Family, seals the
  credential substitutions and creates the outbox message, in one transaction.
  Preparation rechecks that the requesting Administrator is still enabled, the
  configuration still active and the template still in use. The mail-dispatch
  worker then sends it like any Family message.
- **Temporary gates hold; lasting loss ends it.** A restore review, a campaign
  work gate or a dirty or stale source population holds the ticket or message
  without spending its retry budget. A lasting loss of scope (mode, campaign,
  configuration, credential set, Administrator or template) cancels it, a
  Family that stays ineligible after a clean reconciliation cancels its
  message, and a message whose preparation keeps failing is cancelled rather
  than left pending. The scheduler settles tickets whose task ended.
- **Resolution.** An unknown outcome can be confirmed delivered or recorded as
  not sent. With the cancellations above, no test message can be left
  unfinished to block Production readiness. Resend and retry are refused.

It does not open the portal outside the campaign dates, does not satisfy the
readiness check (the fictional sample still does), and is deleted by Testing
cleanup. It adds a table, a purpose and guards to the fresh-install schema;
the baseline is regenerated.

## Reviews

This increment follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, since it changes mail dispatch and the schema, with a
correction check after any round that validates a finding, each recorded
here by which sources answered.

### Round 1

Claude and Codex both answered (two Claude shards). Twenty-two raw
findings; eight validated and corrected (three Highs and five Mediums,
covering the five distinct defects below: some were reported by both
sources, or by one source at two severities), with every Low below the
cutoff taken:

- High (both sources): a test message whose dispatch preparation failed for
  good (for example after its template was removed) stayed pending with no
  settling action, blocking Production readiness permanently. Exhausted
  preparation now cancels it, and a removed template cancels it too.
- High: a Family that became lastingly ineligible held its message forever in
  a free retry loop. After a clean, current reconciliation it now cancels.
- Medium (both sources): SQL checked only that the sign-in time matched a live
  session, not that it was within five minutes; it now enforces the window,
  and the cleanup test uses a genuinely fresh session.
- Medium (Codex): preparation did not recheck the requesting Administrator or
  the active configuration; it now does, in Python and in SQL.
- Medium: temporary gates cancelled queued tickets and spent the worker's
  retry budget; they now hold without charge, and only lasting scope loss
  cancels.
- Lows: portal eligibility is required at every admission point, tests pin the
  Production refusal and the closed-portal sign-in, the page labels a
  cleaned-up ticket, the Family link is computed inside the sample's own
  transaction, the preview/confirm form reuses the shared field checks, and
  the documents name the link and the page consistently.

A correction check follows.

### Round 2

Claude answered with two shards; Codex did not answer this round. Twelve raw
findings; one validated and corrected, with the Lows below the cutoff
taken:

- Medium (both shards): round 1's settlement covered preparation failing
  inside the worker but not a worker that crashed or lost its lease, whose
  recovery failed the task and left the message pending. Recovery now cancels
  such a message once its preparation budget is spent, and a test abandons a
  real dispatch five times to prove it. An audit of every other exit found no
  remaining path that leaves a test message unfinished without a task to
  settle it.
- Lows: a ticket for a lastingly ineligible Family now ends cancelled rather
  than failed; a hold that appears at the final transition defers instead of
  escaping; the work-gate hold is campaign-scoped everywhere and the page
  shows a hold before confirmation; the scheduler sweep asserts its login and
  evaluates the scope once per row; bad Family ID lists re-render as a field
  error; a recipient projection that disagrees with the database is cancelled
  rather than retried; the Family link cannot abort the sample send's
  transaction; and the specifications describe the new worker behavior and
  wrap consistently.

A correction check follows.

### Round 3

Claude answered with two shards; Codex did not answer this round. Seven raw
findings; one validated and corrected, with the Lows below the cutoff taken:

- Medium: the round 2 recovery cancel was admitted only for a message never
  submitted, so a test waiting to retry after a temporary provider error would
  still have been stranded. Recovery now cancels a pending message or one
  waiting after a definite non-acceptance, never an uncertain one, and the
  Python and SQL sides agree exactly; a test covers the temporary-error case.
- Lows: the SQL retry budget and the Python one are cross-referenced and a
  test pins them equal; negative tests show the mail role refused the recovery
  cancel outside its exact conditions; the recovery decision is side-effect
  free again, so the scheduler's hint admission writes nothing, with the
  cancel moved into the recovery step; a test already cancelled when its
  budget runs out settles cleanly; and this guide is rewrapped.

A correction check follows.

### Round 4

Claude answered with two shards; Codex did not answer this round.
Correction check: five raw findings, none validated; the review rounds are
closed. The Lows were taken: the in-process settlement uses the same
"definitely unsent" test as recovery, the negative recovery tests match the
guard's own refusal, an unused test parameter is gone, a test covers
recovering a test message that was already cancelled, and this guide's
Design list is split back into its bullets.

### Candidate CI correction

Exact-head CI on the first candidate failed two tests the local suites had
not run: the storage contract test requires every mutable guard to name
each immutable column in the quoted form the generated guards use, and the
container check requires the host and the image to collect identical test
ids, which a test parametrized with freshly signed tokens broke. The guard
now quotes its columns (the schema baseline is regenerated) and the test has
fixed ids. A focused review of the correction (two Claude shards; Codex
did not answer) found nothing.

## Protected delivery

PR #113 delivered candidate `b9ac3900`, two logical commits plus the receipt
of PR #112, whose content is the retained review history on
`pr/stewardship-family-test-send-reviewed` (`42a1a6fa`), applied to `main`
after PR #112 with identical content, plus exactly that receipt; on that
combined tree the full pure suite, ruff, `makemigrations --check` and 184
PostgreSQL tests across the affected suites passed, and the candidate tree
`5d3eecf2` is the landed tree. The four rounds above had two Claude shards
each and Codex in round 1; rounds 1 to 3 validated three Highs and seven
Mediums, all corrected, and round 4 validated none. The first candidate's
exact-head CI (`35824483608`) failed in two jobs (four checks with their
aggregate jobs), corrected as recorded above and not counted as acceptance.
Exact-head ready-candidate CI `35827926031` and DCO passed all 25 checks,
from 06:41:21 to 07:01:25 UTC on September 23, 2026 (20 minutes 4 seconds).
`origin/main` had no intervening commits since the candidate's base
`fae00f93`. Protected auto-merge landed as `f9b68cab` at 07:01:27 UTC and
was verified on freshly fetched `origin/main`, whose second parent's tree is
the candidate's, before the next increment was committed. This used the
standing delivery authority, without deployment or release; no real provider
was contacted.
