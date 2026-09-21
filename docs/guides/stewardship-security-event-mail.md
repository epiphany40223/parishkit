# Stewardship security event email

This guide records the fourth ADM-07 increment: the operational email the
[admin portal specification](../specs/stewardship/admin-portal/spec.md)
requires for each high-impact login-policy expansion, sent to every
Administrator who existed immediately before the activation, through the
durable outbox with the same retry, escalation and exactly-once discipline
the operational incident alerts use. It completes the notification half of
ADM-07.02 begun by the
[security event acknowledgement increment](stewardship-policy-security-events.md).

## Scope

The activation trigger records each expansion as a `PolicySecurityEvent`
carrying the addresses of the exact-address Administrators of the
configuration being replaced, and the operator-recovery trigger adds the
granted address to a recovery event's recipients. This increment turns each
such event with recipients into one outbox message per recorded address, a
new delivery purpose `security_event`, prepared by the worker, sent by the
isolated mail process through its own private helper, and settled with the
outbox's usual certainty classes. An event with no recipients, a
deployment's root activation, is nothing to send.

Not in this increment: the autosave queue of ADM-07.01 and the suggestion
and assignment editors of ADM-07.03 and .04. ADM-07.02 is now implemented
end to end, but the task stays unchecked until its test task ADM-07.05 and
the rest of the package close.

## Design

### One engine, two compiled owners

The operational incident alerts already had the shape this email needs: a
SQL-created immutable source row, a frozen per-source recipient cohort
captured by a fenced preparation Task, one outbox message per address with
a deterministic semantic key, a MAIL owner that rebuilds the content at send
time and records the provider's answer, and SQL admission functions that let
no other writer touch those rows. Rather than copy that engine, the
preparation, dispatch and MAIL modules now take a compiled `AlertOwner`, a
closed record of what differs: the purpose, the Task type and namespace, the
cohort and recipient tables, how the cohort is captured, how a recipient
stays current, how content is compiled and which private helper submits it.
The operational owner is the default everywhere, so its behaviour and its
suites are unchanged; the security owner is registered beside it in the
worker, the scheduler and the outbox dispatcher, which maps a message's
stored purpose to its owner and never accepts a purpose from a caller.

Two things differ by design. The operational cohort is the current
Administrators, rechecked at send so a revoked Administrator is not told;
the security cohort is the event's recorded recipients, the Administrators
who existed before the expansion, and a recorded recipient is never revoked:
an Administrator whose grant was removed since is still told what changed
while they held it. And the security owner needs no current Administrator
to route, only a Parish and an email channel, since its recipients are
already known.

### Content and its SQL twin

The email names the expansion by the same words the dashboard uses, the
target, the roles before and after in the fixed role order, the granting
Administrator's current address or operator recovery, the time in UTC, the
deployment mode and the event reference, with a fixed instruction to
acknowledge it on the Admin dashboard. The Python compiler and
`stewardship_security_content_v1` produce the same subject, HTML and text
byte for byte, and the SQL render matcher admits an outbox render only when
it equals that recompilation for the message's recipient, sender and
reply-to, so no process can substitute its own prose. The private helper
recompiles the content from the event's facts after the pipe; the envelope
carries no rendered text, and the operational and security envelopes refuse
each other's schema.

### Schema and grants

Two relations are added, the security cohort and recipient tables, with the
same immutability guards and binding triggers the operational tables have:
a cohort may be written only by the running fenced preparation Task that
owns its event and must name exactly the event's recorded recipients, each
once, compared as sets since the owner passes the recorded order through
and the database's collation need not agree with Python's; a recipient row
must bind an outbox message that is exactly one pending security-purpose
envelope to that address. A schema-owned view exposes each event's
recipient count, so the metadata scheduler decides whether an event has
anyone to tell without holding any grant on the addresses. The three outbox purpose
constraints and the outbox scope trigger admit the new purpose as they do
the operational one, the shared dispatch trigger routes it to its own
admission function, and a sibling error trigger leaves the same fixed safe
ERROR a failed operational delivery leaves. The worker gains `SELECT` on the
event table and the count view and `SELECT`/`INSERT` on the two new tables,
the scheduler the count view and the new tables' opaque metadata columns
only, and the mail process `SELECT` on the event and the two tables. The
private helper allowlist admits `security_mail_worker`.

## Fresh-install schema audit

Independent fresh predecessor and candidate databases were compared on the
disposable PostgreSQL cluster; the predecessor, verified main `f9ce5278`,
exactly matches its committed fingerprint. Two relations and the recipient
count view are added, the relations with their columns, constraints,
indexes, immutability and binding triggers; the content, render-matcher,
prepare and dispatch admission, receipt and error functions are added; and
exactly seven existing objects change: the three outbox purpose
constraints, the outbox scope trigger function, the SMTP result function's
purpose list, the worker-side outbox insertion trigger function and the
shared dispatch trigger function, each admitting the new purpose; nothing
is removed. The candidate has 214 relations, 2,390 columns, 3,316
constraints, 987 indexes, 583 functions, 543 triggers and 28 policies. The
strict fixture was updated only after this inspected comparison. This is a
pre-production fresh-install baseline; no upgrade path is added and no
retained database was deleted.

## Focused validation

- Eleven database-free cases for the content compiler: every fact in fixed
  order, markup shown as text, recovery and empty roles worded, the mode
  visible, only canonical UTC accepted, unknown or untyped facts refused,
  the role words equal to the page's labels in their order, and the kinds
  matching the dashboard's wording.
- Sixteen database-free cases for the envelope and helper: facts round-trip
  and generate the MIME, invalid facts and arbitrary content refused without
  echo, the operational and security envelopes and submitters not
  interchangeable, the real pipe owner launching the security helper with a
  fake process, the MIME compiled in the isolated helper with no
  environment, and the installed helper rejecting an invalid credential
  without network.
- Two database-free cases for the owner record: the two owners distinct in
  every closed part, and one assembled from an open part refused.
- The background handler registry case expects `security_prepare` bound to
  the heartbeat pulse, and the scheduler process case expects both fanout
  producers, the second naming the security owner.
- PostgreSQL cases under the real scheduler, worker and mail roles: an
  expansion allocating one durable outbox per recorded recipient with the
  root event never scheduled, SQL refusing a cohort that differs from the
  recorded recipients, the metadata scheduler refused the event's
  recipients, the cohort's addresses and the recipient's address while
  allowed the count and unable to prepare, the SQL content twin equal to the
  Python compiler for every kind and mode with and without a portal actor
  and the escape helper equal to Python's, the security owner binding,
  preparing, submitting and settling a recipient with the operational owner
  unable to bind it, a page with private text or a missing recipient receipt
  refused under the worker's own grants, a failed partial preparation
  cancelling its committed child as `preparation_failed` with two fixed
  ERROR logs, transient and permanent provider answers settled under the
  security admission with their SMTP reasons, an abandoned submission
  becoming `delivery_unknown` with its task failed, and a recipient revoked
  since activation still told.
- The operational fanout, dispatch, routing and hold suites pass unchanged
  under the shared engine, with the background and mail grant suites, the
  immutable-record inventory and the schema baseline.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-security-event-mail-reviews.md) are
complete, single-source under the second September 20, 2026 Codex
exemption, the third validating nothing; full exact-head CI, DCO and
protected delivery remain open. M5 and Gate 3
remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.

## Protected delivery

PR #83 delivered candidate `24d575e4`, three logical commits plus the PR #81
receipt, the fast-selection rotation and one standalone build correction,
whose tree differs from the retained commit-by-commit review history on
`pr/stewardship-security-event-mail-reviewed` (`b74a9f9f`) only by the README
commit `origin/main` gained from PR #82 while the rounds ran; the candidate
was built on that tip, `ba7edc92`, and the landed tree is the candidate's.
The three [review/fix rounds](stewardship-security-event-mail-reviews.md)
were single-source under the second exemption, the third finding nothing to
fix. The first ready candidate, `2670ac4c`, failed the fast build contract:
the default-deny container context re-includes each schema file by name, and
the three security schema files were not listed, so the image would have
started without them. The correction lists them beside the operational alert
schema in both ignore files; a Claude-only correction check, recorded in the
[ledger](stewardship-security-event-mail-reviews.md#round-4-correction-check-single-source-under-the-second-exemption),
found nothing to fix. Exact-head ready-candidate CI `35623288625` and DCO then
passed all 25 checks, from 16:05:06 to 16:22:46 UTC on September 21, 2026
(17 minutes 40 seconds). `origin/main` had no intervening commits since the
candidate's base `ba7edc92`. Protected auto-merge landed as
`b1f806617691d46e7d8efe866c07c999252d64e7` at 16:22:55 UTC and was verified on
freshly fetched `origin/main`, whose second parent's tree `54eaea38` is the
candidate's, before the next increment started. This used the standing
delivery authority, without deployment or release, and supersedes the
checkpoint above. The failed and cancelled runs, including those GitHub
created for the draft and retained-history branches, are not counted as
acceptance.

The security event email increment is delivered: every high-impact
login-policy expansion is sent to the Administrators who existed before it,
through the durable outbox as its own delivery purpose, prepared, sent and
settled by the owner-parametrized alert engine. ADM-07 stays open for its
autosave queue, suggestions and assignments. M5 and Gate 3 remain open.
