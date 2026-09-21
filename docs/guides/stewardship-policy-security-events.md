# Stewardship security event acknowledgement

This guide records the third ADM-07 increment: the durable security event a
high-impact login-policy expansion leaves behind now stays on every
Administrator's dashboard until an Administrator acknowledges it, and the
acknowledgement is recorded once and audited. It continues the
[login rule edit increment](stewardship-user-rule-edits.md) and the
[portal users review](stewardship-portal-users.md), and it belongs to the
[admin portal specification](../specs/stewardship/admin-portal/spec.md)'s
low-friction role editing policy, whose three expansions it surfaces.

## Scope

The specification names three expansions that take effect the moment a
configuration activates and must alert every other Administrator: adding
Administrator to an exact-address rule, creating any hosted-domain rule, and
adding Staff to an existing hosted-domain rule. The activation trigger,
`stewardship_policy_activation_v1` in the fresh-install schema baseline,
already records each as a durable security event in the activation
transaction, with the addresses of the exact-address Administrators who
existed before it, and audits the record. This increment adds what was
missing between that record and an Administrator: the dashboard's
prominence and the acknowledgement that clears it.

Not in this increment: the operational email to each recipient, which needs
a new outbox purpose and its dispatch, and remains the rest of ADM-07.02; the
autosave queue of ADM-07.01; and the suggestion and assignment editors of
ADM-07.03 and .04. No ADM-07 task is checked.

## Design

### Who still sees an event

An unacknowledged event shows on every Administrator's dashboard, a
recipient's and a newer Administrator's alike. Any Administrator other than
the granting actor clears it for everyone with one acknowledgement. The
granting actor's own acknowledgement clears it for that actor alone, so a
grant an Administrator gave themselves still awaits another's eyes; it
clears it for everyone only when no other Administrator existed at
activation, judged by the event's recorded recipients less the actor's own
address. A recovery event has no portal actor, so any Administrator's
acknowledgement is another's. The rule is one pure function over the event
and its acknowledgements, and the dashboard applies it after one query that
excludes every event someone other than its actor has acknowledged.

### What the dashboard shows

Each open event is named by its kind in the specification's words, its
target, its time, the granting Administrator's current address when the
event has one, and the roles before and after in the fixed role order the
Portal users page uses. A target is shown as text. When the viewer has
already acknowledged an event that still awaits another Administrator, the
row says so instead of offering the button again. Each row's button is a
native form posting to the event's own route with a CSRF token, so the panel
needs no script.

### How an acknowledgement is recorded

The route takes only POST under a current Administrator session with the
`manage_users` capability, refuses a query string, and reads the coherent
configuration inside one transaction so the audit names the Parish as every
other Administrator action does. The acknowledgement is one append-only row
naming the event and the acknowledging Administrator's normalized address at
that time, with the audit actor and correlation the immutable-record base
records; the address is kept because a Google identity's address can change
while an event's recipients are addresses. A unique constraint on event and
address makes a repeat, including one racing another request from the same
Administrator, record nothing more and audit nothing more; the first records
one `security_event_acknowledged` audit action naming the event. An unknown
event is not found, and the final session recheck every Admin route makes
runs here too before the dashboard is shown again. The web role gains
`SELECT` and `INSERT` on the acknowledgement table and nothing else; the
event table stays read-only to it, and the installer roles do not touch the
acknowledgements.

## Fresh-install schema audit

Independent fresh predecessor and candidate databases were compared on the
disposable PostgreSQL cluster; the predecessor, verified main `4f0465fa`,
exactly matches its committed fingerprint. One relation is added,
`stewardship_policy_security_ack`, with its six columns, primary key, unique
event-and-address constraint, deferred foreign key to the event, two
indexes, its append-only trigger and that trigger's function; nothing is
changed or removed. The candidate has 211 relations, 2,366 columns, 3,281
constraints, 976 indexes, 569 functions, 536 triggers and 28 policies. The
strict fixture was updated only after this inspected comparison. This is a
pre-production fresh-install baseline; no upgrade path is added and no
retained database was deleted.

## Focused validation

- Eight database-free cases for who still sees an event: everyone while it
  is unacknowledged, the granting actor alone after their own acknowledgement
  while another Administrator existed, everyone after any other
  Administrator's, everyone after the actor's alone when no other
  Administrator existed or the event came from recovery, and a label for
  each kind the trigger records.
- Three PostgreSQL cases under the real web role, with rules applied through
  the real installer: an Administrator grant recorded as an event naming both
  existing Administrators, shown on the granting actor's dashboard,
  acknowledged once with its audit and gone for the actor while the other
  Administrator still sees it without the actor's note, then settled for
  everyone including the newly granted Administrator by the other's
  acknowledgement; a domain rule created by the only Administrator settled by
  their own word, with a later Administrator inheriting nothing; and an
  unknown event not found, GET not served, a missing CSRF token and Staff
  refused with nothing recorded.
- The schema baseline, immutable-record inventory, policy activation,
  recovery and login rule edit suites pass with the added table.
- Six browser cases across Chromium, Firefox and WebKit at phone and desktop
  widths: the panel a labelled region with no accessibility violations, each
  kind worded, roles before and after, a target shown as text, the viewer's
  own earlier acknowledgement worded without a button, each button a native
  form posting to its event's route with a CSRF token, and the panel absent
  when nothing is open.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation and focused validation are complete; the review/fix rounds,
full exact-head CI, DCO and protected delivery remain open. M5 and Gate 3
remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
