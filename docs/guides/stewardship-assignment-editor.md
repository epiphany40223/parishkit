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
other Ministry by closed reason; without a promoted catalog the page offers no
form and the route is unavailable. An assignment takes effect only through a
rule granting Ministry leader, so the preview states through which rule it
would: the address's exact rule, its hosted-domain rule and that rule's
Google claim, or none until such a rule exists. The address needs no rule
yet, and the page then lists the assignment among those relying on a domain
rule with its warning.

### Privacy, authority and cost

The route is the rule editor's route in every guard: session and capability,
CSRF, the applied digest signed into the preview, the actor rechecked at
intake and activation, and the complete resulting policy validated before
anything is signed. The Ministry names the page shows come from the snapshot's
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
- Three PostgreSQL cases under the real web and restricted installer roles
  with a promoted catalog: the page offering the catalog's active
  Ministries, an assignment added for a Ministry leader, named beside its
  DUID, bound to the request and in force at sign-in, a duplicate, an
  inactive Ministry and an invalid address refused, and the assignment
  removed with a second removal refused; an address with no rule assigned
  with the preview stating the domain rule it depends on and the page
  listing it by domain; and without a promoted catalog the page offering no
  form and the route unavailable.
- The Portal users page, suggestion and seed review suites pass with the
  named assignments; the grant registry and build contract database-free
  suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete; review/fix rounds, full
exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
