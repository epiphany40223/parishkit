# Stewardship login-rule autosave queue

This guide records the ninth slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
the autosave queue the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#portal-user-management)
requires for role checkbox changes on the Portal users page. It continues the
[manual assignment editor increment](stewardship-assignment-editor.md) and
follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

With scripting, each role checkbox change on an exact-address or
hosted-domain rule row is autosaved as one logical intent, one target, one
role, one desired value, through a `ConfigurationChangeRequest` with a
transient Applying/Applied/error indicator beside that role; without
scripting the page's native review forms remain the way roles change, and a
new rule is always made by the reviewed add forms. One in-memory queue per
page holds ordered intents with at most one request in flight; further
changes stay interactive and read **Queued — not saved**. The queue adopts a
request's applied digest only from an activation receipt and forms the next
minimal patch from the remaining intent, pauses on any refusal, failure or
uncertainty, shows the current rules beside the remaining intents for a
conflict so the Administrator discards or retries them as new requests
against the refreshed digest, clears the page and offers sign-in when access
is lost, warns before leaving with unsent intents, and never replays a
former queue after a reload.

The queue serves the rule tables only; the assignment and Chairperson
actions keep their previewed forms. The tests of ADM-07.05 remain a later
slice. No ADM-07 task is checked.

## Design

### An intent is the rule editor's patch, applied over the page's rules

An intent names the target, the role and the desired value, never a toggle
and never a copy of the configuration: the server takes the target's
configured roles from the rules the page was drawn from, grants or withdraws
the one role, and builds the rule editor's own minimal patch, so provenance,
the domain-rule fences and the last-Administrator guard are the same
whichever path made the change. Only a rule the page shows autosaves: a
target another Administrator has since removed is refused as stale whichever
way the tick went, so a retry never recreates a deleted rule implicitly, and
an intent that changes nothing is refused rather than recorded. Because the
patch updates an existing record, identical intents build identical patches.

### The request is the confirmation, keyed by the client

The apply route validates the whole resulting policy before intake, as a
preview does, and records the request exactly as a confirmed preview would,
with the same admission under the work lock, the same actor recheck and the
same digest check, but the request key is chosen by the page; anything else
intake raises is answered as unavailability, never as a refusal of the
change. A key the
Administrator already used names its request, and the route answers with
that request's committed state before looking at the digest or the rules:
a lost answer is recovered after the installer has moved on, the intent is
never rebuilt over a base it was not made against, and no second grant can
result. The page reads the request until an activation receipt says it
applied and only then shows Applied beside the role, records that value as
confirmed, adopts the applied digest into every form on the page and
dispatches the next intent. HTTP acceptance, `prepared` and
`yaml_activated` advance nothing.

### Failures pause; uncertainty keeps the key; conflicts are resolved in the open

A refusal, an installer failure or a cancellation pauses the queue with a
closed message beside the role, restores the tick to its confirmed value
and lists the remaining intents, which the Administrator continues or
discards. No answer at all, or an outcome that cannot be read, is
uncertainty, not failure: the request and its key are kept, nothing further
is sent, and the Administrator looks again with the very same key. For a
stale digest, whether refused at intake or found by the installer, the page
reads the applied digest and configured roles through a read-only route
under the same capability and shows every remaining intent, in the one
order the queue keeps, beside the rule's current roles or that the rule no
longer exists; intents the current rules already satisfy are not
preselected, a deleted target cannot be retried, the selection belongs to
the intent so a redraw never resets it, and the Administrator retries the
selected intents as new requests against the refreshed digest or discards
them; either way every control on the page is reconciled with the current
rules, a kept intent keeping its tick and a deleted target's controls
disabled until the page is redrawn. A change of mind back to a confirmed
value, or to the value a request in flight will confirm, needs no request,
and the queue is pruned of such intents before each dispatch and after each
refusal. Every request has a deadline, so a stalled connection reaches the
uncertain state rather than waiting forever, and a failed read of the
current rules is offered again with the queue kept, a reload abandoning the
queue first so no warning is raised for changes just given up. A lost session ends the queue,
clears the restricted tables and forms, and offers sign-in; nothing is
retried across a login.

### Privacy, authority and cost

Every route requires the current session, the users capability and CSRF,
rechecks the actor after the work, and answers with the closed error codes
every enhanced client understands, never a submitted value. Every answer
names the session's current CSRF token, which the page adopts for its later
requests and forms, since an Administrator who changes their own roles keeps
a rotated session whose token the page cannot read from its cookie. The
status read is passive and actor-scoped, so an open page cannot keep an idle
login alive or observe another Administrator's request, and polling waits
while the tab is hidden. A used key is looked up again before a stale digest
is refused, so an original request that activated between the two reads
still answers. The page adds no query to its own render; the script runs
only when a rule row exists, and its retry wait, poll interval and deadline
are read from the page as test seams the template never sets. When a stale
digest opens the conflict view, an intent that was in flight returns to the
queue only when no newer intent for its control already waits, so the
Administrator's latest click stands.

## Schema

No schema change.

## Focused validation

- Two database-free cases: a role granted and withdrawn over the configured
  roles, preserving every other grant's origin, building the same patch for
  the same intent, down to an explicit deny; and a missing target refused
  either way, an unchanged intent, the domain-rule fences, an unknown role
  and an invalid target refused.
- Two PostgreSQL cases under the real web and restricted installer roles:
  an intent recorded once for its key, the same key answered with the same
  request whether resubmitted unchanged, with another intent or after the
  request has applied, the request read as staged and then as applied with
  the applied digest, the role in force at sign-in with its grant bound to
  the request, a stale digest refused, the base read returning the current
  digest and roles, the answered CSRF token accepted, and another
  Administrator unable to read the request by its real id; and intake
  refusing for a reason other than the policy answered as unavailable, the
  last Administrator's withdrawal, a rule the page does not show either
  way, a new domain, the domain-rule fences, malformed and unexpected
  fields, a wrong method, a missing CSRF token and a Staff reader
  all refused with nothing recorded.
- Eleven browser cases in every engine against the component page with the
  routes mocked, the slow paths driven through the timing seams: an
  unanswered request kept uncertain with its key and Try again resending
  that key, unreadable outcomes pausing and Try again reading the same
  request, the pause panel discarding the rest or continuing, a reload from
  a failed read leaving without a warning, a newer click superseding a
  conflicted in-flight intent, and the following: ticks applied in order with Applied only from the receipt,
  the digest and a rotated CSRF token adopted, a change of mind dropped
  whether against the confirmed value or the one in flight; a refusal
  restoring its tick, pausing, and surviving a queued change to another
  role, with leaving warning; a conflict marking the row, listing the
  current rules, taking a newer change into the same list, keeping the
  Administrator's selection across a redraw, retrying the selected intents
  with new keys on the refreshed digest, discarding the rest and reconciling
  an untouched row; a failed read of the current rules offered again with
  the queue kept, then discarding the conflict restoring the current values
  and later changes using the refreshed digest; lost access clearing the tables
  and offering sign-in; a lost answer resent with the same key; and a failed
  request pausing while a stale base found at activation opens the conflict
  view.
- The Portal users page, rule editor and assignment editor suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete and
[rounds 1 to 6](stewardship-rule-autosave-reviews.md) are answered; the
correction check, full exact-head CI, DCO and protected delivery remain
open. M5 and Gate 3 remain open. No deployment, release, live-provider write
or database deletion is authorized by this increment.
