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
transient Applying/Applied/error indicator; without scripting the page's
native review forms remain the way roles change. One in-memory queue per
page holds ordered intents with at most one request in flight; further
changes stay interactive and read **Queued — not saved**. The queue adopts a
request's applied digest only from an activation receipt and forms the next
minimal patch from the remaining intent, pauses on any failure or conflict,
shows the current rules beside the remaining intents for a conflict so the
Administrator discards or retries them as new requests against the refreshed
digest, warns before leaving with unsent intents, and never replays a former
queue after a reload.

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
whichever path made the change. An intent that changes nothing, or that
withdraws a role from a rule that no longer exists, is refused rather than
recorded; a retry never recreates a deleted rule implicitly.

### The request is the confirmation, keyed by the client

The apply route records the request exactly as a confirmed preview would,
with the same admission under the work lock, the same actor recheck and the
same digest check, but the request key is chosen by the page: a lost answer
is recovered by resubmitting the same key, which returns the original
request, and a key resubmitted with a different intent is refused as stale.
The answer is the request's committed state; the page reads the request
until an activation receipt says it applied and only then shows Applied,
adopts the applied digest into every form on the page and dispatches the
next intent. HTTP acceptance, `prepared` and `yaml_activated` advance
nothing.

### Failures pause; conflicts are resolved in the open

A refusal, an installer failure, a cancellation or a stale digest pauses the
queue with a closed message and leaves the remaining intents visible. For a
stale digest the page reads the applied digest and configured roles through
a read-only route under the same capability and shows each remaining intent
beside the rule's current roles, or that the rule no longer exists; the
Administrator retries the selected intents as new requests against the
refreshed digest or discards them. A lost session ends the queue and offers
sign-in; nothing is retried across a login.

### Privacy, authority and cost

Every route requires the current session, the users capability and CSRF,
rechecks the actor after the work, and answers with the closed error codes
every enhanced client understands, never a submitted value. The status read
is passive and actor-scoped, so an open page cannot keep an idle login alive
or observe another Administrator's request. The page adds no query to its
own render; the script runs only when a rule row exists.

## Schema

No schema change.

## Focused validation

- Two database-free cases: a role granted and withdrawn over the configured
  roles, preserving every other grant's origin, down to an explicit deny;
  and a new rule created by a grant, a withdrawal from no rule, an unchanged
  intent, the domain-rule fences, an unknown role and an invalid target
  refused.
- Two PostgreSQL cases under the real web and restricted installer roles:
  an intent recorded once for its key, the same key returning the same
  request and a different intent for it refused, the request read as staged
  and, after activation, as applied with the applied digest, the role in
  force at sign-in with its grant bound to the request, a stale digest
  refused and the base read returning the current digest and roles; and the
  last Administrator's withdrawal, a withdrawal from a missing rule, the
  domain-rule fences, malformed and unexpected fields, a wrong method, a
  missing CSRF token and a Staff reader all refused with nothing recorded.
- Browser cases: a checkbox change shows Applying then Applied and adopts
  the digest; two rapid changes queue and settle in order; a refusal pauses
  the queue; leaving with unsent intents warns.
- The Portal users page, rule editor and assignment editor suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete; review/fix rounds, full
exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
