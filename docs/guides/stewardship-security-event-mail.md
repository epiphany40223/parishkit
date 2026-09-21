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
constraints, 987 indexes, 583 functions, 543 triggers and 28 policies. The strict fixture was updated only after this inspected
comparison. This is a pre-production fresh-install baseline; no upgrade path
is added and no retained database was deleted.

## Focused validation

- Ten database-free cases for the content compiler: every fact in fixed
  order, markup shown as text, recovery and empty roles worded, the mode
  visible, only canonical UTC accepted, unknown or untyped facts refused, and
  the kinds matching the dashboard's wording.
- Fourteen database-free cases for the envelope and helper: facts round-trip
  and generate the MIME, invalid facts and arbitrary content refused without
  echo, the operational and security envelopes and submitters not
  interchangeable, the real pipe owner launching the security helper with a
  fake process, and the installed helper rejecting an invalid credential
  without network.
- Two database-free cases for the owner record: the two owners distinct in
  every closed part, and one assembled from an open part refused.
- PostgreSQL cases under the real scheduler, worker and mail roles: an
  expansion allocating one durable outbox per recorded recipient with the
  root event never scheduled, SQL refusing a cohort that differs from the
  recorded recipients, the metadata scheduler refused the event's
  recipients, the cohort's addresses and the recipient's address while
  allowed the count and unable to prepare, the SQL content twin equal to the
  Python compiler for every kind and mode with and without a portal actor
  and the escape helper equal to Python's, the security owner binding,
  preparing, submitting and settling a recipient with the operational owner
  unable to bind it, a failed partial preparation cancelling its committed
  child as `preparation_failed` with two fixed ERROR logs, and a recipient
  revoked since activation still told.
- The operational fanout, dispatch, routing and hold suites pass unchanged
  under the shared engine, with the background and mail grant suites, the
  immutable-record inventory and the schema baseline.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and the first
[review/fix round](stewardship-security-event-mail-reviews.md) are
complete, single-source under the second September 20, 2026 Codex
exemption; further rounds, full exact-head CI, DCO and protected delivery
remain open. M5 and Gate 3
remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
