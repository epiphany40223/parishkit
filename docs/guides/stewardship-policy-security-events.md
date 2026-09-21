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
recipient's and a newer Administrator's alike. Administrators are judged by
address, the way the event records its recipients, since several Google
identities may share one address and an address may change. An
acknowledgement by an Administrator who existed at activation, one of the
event's recipients other than the granting actor, settles the event for
everyone. The actor's own settles it only when no other Administrator
existed at activation: the actor of a portal-driven activation is always
among the recipients, since Administrator is an exact-address role and the
request is bound to the predecessor, so that is a recipient list of one,
judged by its length rather than by an address that may since have changed.
An event with nobody else to await, a deployment's root activation or one
whose predecessor named no Administrator, is settled by any acknowledgement.
Any other acknowledgement, the actor's, a newer Administrator's or the
granted account's own, clears the event for that address alone, so a grant
one Administrator gave themselves, or gave a second account they control,
still awaits an Administrator who was already one. Whether an
acknowledgement is the actor's own is decided when it is recorded, from the
actor's identity or current address, so a later change of address cannot
turn it into another Administrator's. A recovery event has no portal actor
and names the account it grants among its recipients, since that account
must be told; that account was not an Administrator at activation, so its
acknowledgement clears the event for itself alone and any prior recipient's
settles it. The rule is one pure function
over the event and its acknowledgements; the dashboard reads every event
with its acknowledgements in one query, since an expansion is recorded
rarely and only an acknowledged one needs judging.

### What the dashboard shows

Each open event is named by its kind in the specification's words, its
target, its time, the granting Administrator's current address when the
event has one, and the roles before and after in the fixed role order the
Portal users page uses. A target is shown as text. An event the viewer has
acknowledged has left their dashboard, whatever it still awaits from others.
Each row's button is a native form posting to the event's own route with a
CSRF token, so the panel needs no script.

### How an acknowledgement is recorded

The route takes only POST under a current Administrator session with the
`manage_users` capability, refuses a query string, and reads the coherent
configuration inside one transaction so the audit names the Parish as every
other Administrator action does. The acknowledgement is one append-only row
naming the event, the acknowledging Administrator's normalized address at
that time and whether it is the granting actor's own, with the audit actor
and correlation the immutable-record base records. A unique constraint on
event and address makes a repeat, including one racing another request or
made through a second Google identity at the same address, record nothing
more and audit nothing more; the first records one
`security_event_acknowledged` audit action naming the event. An unknown
event is not found, and the final session recheck every Admin route makes
runs here too before the dashboard is shown again. The web role gains
`SELECT` and `INSERT` on the acknowledgement table and nothing else; the
event table stays read-only to it, and the installer roles do not touch the
acknowledgements.

## Fresh-install schema audit

Independent fresh predecessor and candidate databases were compared on the
disposable PostgreSQL cluster, once for the increment and again after the
first review round added the acknowledgement's own-flag column; the
predecessor, verified main `4f0465fa`, exactly matches its committed
fingerprint. One relation is added, `stewardship_policy_security_ack`, with
its seven columns, primary key, unique event-and-address constraint,
deferred foreign key to the event, two indexes, its append-only trigger and
that trigger's function; nothing is changed or removed. The candidate has
211 relations, 2,367 columns, 3,282 constraints, 976 indexes, 569 functions,
536 triggers and 28 policies. The strict fixture was updated only after
each inspected comparison. This is a
pre-production fresh-install baseline; no upgrade path is added and no
retained database was deleted.

## Focused validation

- Eleven database-free cases for who still sees an event: everyone while it
  is unacknowledged, the granting actor alone after their own acknowledgement
  while another Administrator existed, everyone after another recipient's, a
  newer Administrator or the granted account alone after their own even
  beside the actor's, everyone after the actor's alone when no other
  Administrator existed even under an address changed since, anyone settling
  an event with no recipients, a recovery event settled by a prior recipient
  and never by the account it grants unless nobody else existed, and a label
  for each kind the trigger records.
- Three PostgreSQL cases under the real web role, with rules applied through
  the real installer: an Administrator grant recorded as an event naming both
  existing Administrators, shown on the granting actor's dashboard,
  acknowledged through the actor's second Google identity at the same
  address as the actor's own, once with its audit, gone for both identities
  and not settled, the granted account clearing it for itself alone, the
  other Administrator still seeing it and settling it for everyone; a domain
  rule created by the only Administrator settled by their own word, the root
  activation's own event with no recipients settled by any acknowledgement,
  and a later Administrator inheriting nothing; and an unknown event not
  found, GET not served, a
  missing CSRF token, a query string, a configuration under restore review
  and Staff refused with nothing recorded.
- The schema baseline, immutable-record inventory, policy activation,
  recovery and login rule edit suites pass with the added table.
- Six browser cases across Chromium, Firefox and WebKit at phone and desktop
  widths: the panel a labelled region with no accessibility violations, each
  kind worded, roles before and after, a target shown as text, each button a
  native form posting to its event's route with a CSRF token, and the panel
  absent when nothing is open.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-policy-security-event-reviews.md) are
complete, all single-source under the second September 20, 2026 Codex
exemption, with every accepted finding fixed; full exact-head CI, DCO and
protected delivery remain open. M5 and Gate 3
remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
