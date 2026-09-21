# Stewardship manual assignment editor

This guide records the eighth slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
the manual Ministry assignments editor the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#chairperson-suggestions-and-assignments)
requires, under the
[authorization data model](../specs/stewardship/data/spec.md#administration-user-and-policy).
It continues the [Chairperson seed review increment](stewardship-chair-review.md)
and follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

An Administrator assigns an address to an active Ministry of the promoted
catalog, or removes an Administrator entry assignment, from the Portal users
page: an **Assign to Ministry** form on each exact-address rule's row, a
**Review assignment removal** button beside each Administrator entry
assignment in the exact-address and domain-assignment tables, worded apart
from the rule's own removal, and an **Add a Ministry assignment** form for
an address that has no rule yet. Each is previewed and
confirmed exactly as a rule change is and applied as an ordinary policy
request. The page now names each assignment's Ministry beside its DUID from
the promoted catalog, as the
[read-only review](stewardship-portal-users.md) deferred to this editor.

The autosave queue of ADM-07.01 and the tests of .05 remain later slices. No
ADM-07 task is checked.

## Design

### Every edit is an ordinary policy request

The ordinary request schema admits a new manual assignment bound to the
request that carries it and a removed record, so the editor needs no schema,
no writer and no new guard: pure builders in `assignment_edits` produce the
patch for an addition or a removal and refuse, by closed reason, a second
manual assignment of the same address to the same Ministry and a removal of
something that is not an Administrator entry, naming a Chairperson seed as
such so the Administrator is sent to the review that decides seeds. A seed of
the same pair may exist beside a manual assignment, as policy admits, and the
parish source never rewrites a manual one.

### The catalog and the effect are stated, not inferred

The editor offers only the promoted catalog's Ministries the applied activity
keeps active, read as the Ministry activity editor reads them, and refuses any
other Ministry for an addition by closed reason; the page offers an addition
only when that editor would accept the catalog, so without a promoted source
of the configured organization the page offers no addition form and an
addition is unavailable. A removal needs no catalog and is judged by the
applied policy alone, since an assignment to a Ministry since deactivated,
dropped from the catalog or left behind by a source no longer promoted is
exactly what an Administrator cleans up; the promoted snapshot, if any,
serves a removal only the Ministry's name, and the preview says when there is
none. An assignment takes effect only through a rule
granting Ministry leader, so the preview states through which rule it would:
the address's exact rule, its hosted-domain rule and that rule's Google
claim, or none until such a rule exists. The roles come from the one policy
evaluator over the resulting policy, not from a rule's configured list, so
an Administrator is told the assignment adds no scope, and a seeded leader
role the assignment itself brings back into force is stated as a sign-in
would decide it. The address needs no rule yet, and the page then lists the
assignment among those relying on a domain rule with its warning.

### Privacy, authority and cost

The route is the rule editor's route in every guard: session and capability,
CSRF, the applied digest signed into the preview, the actor rechecked at
intake and activation, and the complete resulting policy validated before
anything is signed. The preview also signs the promoted snapshot it judged
the catalog against, as the Ministry activity editor does, since a
promotion changes no digest yet may drop the Ministry an addition was
previewed for; a removal signs whatever was promoted, nothing included. The Ministry names the page shows come from the snapshot's
own canonical payloads, which the web role reads for the Family form already;
the page adds one query for them.

## Schema

No schema change. The names and the catalog come from tables the web role
already reads.

## Focused validation

- Two database-free cases: an added assignment normalized, manual and bound
  to the request, unblocked by a seed of the same pair and allowed for an
  address without a rule, a second manual assignment and an invalid address
  refused; and a removal dropping only the manual assignment, refusing a
  seed alone and a missing one, every result satisfying the ordinary policy
  rules.
- Four PostgreSQL cases under the real web and restricted installer roles
  with a promoted catalog: the page offering the catalog's active
  Ministries, the preview stating an exact leader rule's effect, an
  Administrator's needlessness and an unnamed address's lack of effect, an
  assignment added for a Ministry leader, named beside its DUID, bound to
  the request and in force at sign-in, a duplicate, an unknown Ministry and
  an invalid address refused, and the assignment removed, previewed as such,
  with a second removal refused; an address with no rule assigned with the
  preview stating the domain rule it depends on, stale after a promotion
  until previewed again, and the page listing it by domain, beside the
  wordings for a domain rule granting Ministry leader and an exact rule not
  granting it; an assignment to a Ministry the activity
  editor then deactivates still listed, no longer offered, refused for a new
  addition and removed, with an assignment to a DUID the catalog never had
  removed as such; and without a promoted catalog the page offering no
  addition form and refusing an addition while an assignment left behind is
  listed and removed.
- The Portal users page, suggestion and seed review suites pass with the
  named assignments; the grant registry and build contract database-free
  suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation, focused validation and the
[review rounds](stewardship-assignment-editor-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
