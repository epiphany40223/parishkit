# Stewardship Chairperson suggestions

This guide records the fifth slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
the Chairperson suggestions the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#chairperson-suggestions-and-assignments)
asks the Administrator to review after every source promotion, shown
read-only on the Portal users page. It continues the
[security event email increment](stewardship-security-event-mail.md) and
follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

The Portal users page gains a fourth table. Each row is a current Chairperson
of an active Ministry in the promoted parish source, with the valid address
the source records: the Member's name and DUID, the Ministry's name and DUID,
whether the contact is publishable, the login rule the address has today with
its roles, any assignment already configured for that Ministry and whether it
is in force, and an ambiguity when the address is used by more than one active
Member. A suggestion grants nothing.

Confirming a suggestion as a reviewed configuration request, which creates
the exact-address rule, the Chairperson-seeded Ministry leader grant and the
seeded assignment with their retained identity evidence, is the next
increment; the **Keep role independently** action, the suspended-assignment
review and the manual assignment editor follow it. No ADM-07 task is checked.

## Design

### One definition of a current Chairperson

The reconciliation owners decide a seed's suspension from the narrow
`stewardship_current_chair` projection: an active Member, a catalog-present
Ministry, a current roster row whose role is Chairperson, and a valid address.
That projection is the one definition, and it carries no names. The restricted
web role already reads the source Member, contact, Ministry and roster tables
for the Family form, so the page could have recomputed the rule from them; it
must not, or the page and the reconciliation would drift. The web role is
therefore not given the projection, and reads instead a schema-owned view,
`stewardship_chair_suggestion`, that joins the projection to the promoted
snapshot's canonical Member and Ministry payloads for the names and adds, per
row, the active Members whose valid address this is. A PostgreSQL case proves
the web role reads the view and is refused the projection.

### The page decides nothing new

The view is read under the observation's work lock, for the promoted snapshot
and its organization only, so a relationship is never paired with another
generation's names. Which of those Ministries are active is decided by
`active_ministries` over the applied document, the rule the reconciliation
owner applies. Row shaping is pure: one row per address and Ministry, several
roster rows for one Member folded into one candidate, every Member retained
rather than one picked, ordered by Ministry name, DUID and address; a Ministry
payload without a name is valid source and shows an empty name. What the
address has today comes from the applied records through the same
`AppliedPolicy` index the other tables use: an exact rule shows the roles the
evaluator grants now, so a seeded Ministry leader role the source no longer
confirms reads as suspended rather than held and an empty role set as an
explicit denial; a hosted-domain rule shows its roles as conditional on the
matching Google claim, since a consumer account on the same suffix receives
nothing; and a seeded assignment reads as suspended exactly when sign-in
would drop it.

A row is ambiguous when its address is used by more than one active Member,
or by a Member other than the Chairperson. The row states how many; a
confirmation must then select the Member explicitly, which is why the count
is shown rather than resolved.

### Privacy, authority and cost

Only an Administrator sees the table, under the page's existing capability,
parameter refusal and unsafe-method refusal. The audit event counts the rows
of all four tables and carries no name or address. The view adds one query
for the current source pointer and one for the relationships, read under the
work lock; address ownership is aggregated once per snapshot and address and
joined, so the cost is bounded by the snapshot's Members rather than their
product with the roster. The identity read is unchanged.

## Schema

One added relation: the view `stewardship_chair_suggestion`, a
security-barrier projection over `stewardship_current_chair`, the snapshot
Member and Ministry bindings and their canonical payloads, registered after the
security event email schema. The web role gains SELECT on it and nothing else;
the grant registry's projection list names it. The container build context
re-includes the schema file by name.

An independent fresh-install audit against verified main `b1f80661`, with the
same catalog inspection the previous increments used, found the predecessor
matching its committed fingerprint, exactly one added relation with its
eleven columns, and no existing object changed or removed. The candidate has
215 relations, 2,401 columns, 3,316 constraints, 987 indexes, 583 functions,
543 triggers and 28 policies. The strict fixture was updated only after this
inspected comparison. Pre-production fresh-install baseline only; no upgrade
path, and no retained database was deleted.

## Focused validation

- Six database-free cases for the rows: grouping and Ministry-name order,
  a nameless Ministry sorting and showing empty, locally inactive Ministries
  omitted, shared addresses ambiguous with every Member kept, the rule
  showing what the evaluator grants, suspended, denied, conditional on a
  domain claim or confirmed, with the assignments from the applied records,
  and a manual assignment beside a suspended seed both shown in either
  record order.
- Three PostgreSQL cases under the real web role with real normalized source
  promoted: the row naming the Member and Ministry with the publication flag,
  the audit count without an address, the view readable and the projection
  refused, the projection agreeing with the pure source candidates; two
  Chairpersons on one address both listed with the ambiguity stated and a
  seeded rule and assignment reading as current, suspended until the overlay
  confirms them; and no promoted source rendering the empty statement.
- The Portal users page suite, the Chairperson projection suite, the web
  grant suite and the schema baseline pass; the grant registry and build
  contract database-free suites pass with the view and schema file.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-chair-suggestions-reviews.md) are complete,
single-source under the second September 20, 2026 Codex exemption, with
every accepted finding fixed; a correction check of the third round's fix,
full exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
