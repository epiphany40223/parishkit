# Portal users review

This Phase 5 increment begins at verified main `1070fd28`. It is the first slice
of [ADM-07](../plans/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
a read-only Administrator page showing who may sign in to the portal and why.
The [portal user management specification](../specs/stewardship/admin-portal/spec.md#portal-user-management)
controls behavior.

It is independent of the open financial stewardship detail increment and
branches from main, not from that unmerged work.

## Scope and acceptance

An Administrator opens **Portal users** from the Admin navigation and sees the
applied login rules in the specification's two sorted tables. Hosted-domain
rules show their roles, how many recorded Google accounts the rule really
authorizes, the latest successful sign-in among them and warnings. Exact-address
rules show the rule's origin, the configured roles with each grant's origin, the
roles current policy grants now, Ministry assignments with their source and
whether the parish source currently confirms them, the last successful sign-in
and warnings. An empty role set is labelled **Explicit deny**. A third table
appears only when an assignment belongs to someone with no exact rule, who
relies on a domain rule, and says whether it is in effect now.

This slice reviews and changes nothing. Role edits, rule creation and removal,
autosave with its queue and conflict recovery, security-event acknowledgement,
Chairperson suggestions, the assignment editor and suspended-assignment review
are later ADM-07 slices, so no ADM-07 task is checked here. Ministry names are
not shown beside assignment DUIDs yet; the assignment editor needs them and will
add them.

## Design

### One evaluator

Row shaping is pure and decides nothing. Every role and Ministry scope shown
comes from `resolve_roles`, the evaluator every sign-in uses, over the same
applied canonical records. The confirmed Chairperson assignments come from
`confirmed_seeded`, one query that sign-in and this page now share, so the two
cannot drift apart. A Chairperson-only Ministry leader role therefore shows as
suspended exactly when sign-in would drop it, and an Administrator shows the
roles Administrator implies.

The evaluator is given the hosted-domain claim a recorded Google identity really
presented, never an email ending. A domain rule's evidence counts only accounts
it authorizes: the email suffix, the signed claim and the rule must agree, no
exact rule may replace it, and the identity must be usable. A Workspace alias
domain presents the primary claim with another suffix, so a bare claim match
would mislead. An assignment for someone without an exact rule is judged the
same way; only an address never seen is described from policy alone, and then
as a condition rather than a fact about that person.

### Policy and identity are separate facts

What policy grants an address does not depend on which Google identity asks, but
whether an identity may sign in at all does. Google's stable subject owns
identity, so one address can have several identities, and disabling is per
identity. The page shows what policy grants now and states separately, with
counts, when recorded identities for that address are disabled and cannot use
it. It never hides a grant inside an identity's state or the reverse.

### A sign-in is a successful one

Every verified Google attempt records an identity and refreshes its verification
time, including an attempt that policy then refuses and a stranger's. That time
is therefore not a sign-in. The page uses the durable login audit event, written
only when a session is actually issued, and labels it the last successful
sign-in. An explicit deny never shows one merely because someone tried, and an
address with none shows **None on record**.

### Warnings state facts, not advice

Each warning is derivable from applied policy and recorded identities: a domain
rule that authorizes no recorded account yet; an exact address that replaces its
domain rule; a suspended Chairperson role, which is not also told to obtain a
role it already has; a Ministry leader with no active assignment; an assignment
with no configured role to use it; disabled identities; and an assignment that
no usable identity or rule can make effective.

### Privacy, authority and cost

Only an Administrator may see who else holds access. The page and its navigation
entry check the same `Capability.MANAGE_USERS`; Staff and Ministry leaders are
denied. The page takes no parameters and refuses a query string, so an address
never reaches a URL or a log, and refuses every unsafe method. Each view records
one audit event carrying an outcome and the number of rows disclosed across all
three tables, never an address or a role. The response is never cached.

The observation runs under the shared work lock so the applied policy, the
source overlays and the identities are one coherent read. That lock serializes
the whole system's admissions, so only the observation, the recheck of current
access and the audit run inside it; shaping and rendering happen after release.
The identity read is bounded by the addresses and domains the policy names, not
by how many strangers have attempted a sign-in, and the rows index the records
once rather than rescanning them per address.

Like the Admin editors beside it, the page is unavailable before setup completes
and during a restore review.

## Schema

No schema change. The audit action is an application vocabulary entry with no
SQL constraint, and the restricted web role already reads every table this page
uses, including the audit events, which the PostgreSQL cases prove by running
under that role.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- Five database-free cases, under one second: a domain rule counting only the
  accounts it authorizes, excluding an explicit deny, a disabled identity, a
  Workspace alias and a suffix without the claim; address rows with granted
  roles, origins, explicit denial and a refused attempt that is not a sign-in; a
  seeded leader suspended until the source confirms a Chairperson, with its
  complete warning list; several Google identities for one address in either
  query order; and assignments relying on a domain rule judged by the real
  hosted claim, with the audit count of all three tables.
- Seven PostgreSQL cases under the real web role, 20 seconds. Rules installed
  through the real configuration owner, with a refused sign-in attempt, a real
  domain-rule sign-in and a disabled identity: each row's content, a refused
  query string, a CSRF-carrying POST refused with 405, and one audit row free of
  addresses. Chairperson-seeded rules installed the way the evaluator's tests
  install them, with real overlays, agreeing with `current_principal` for a
  confirmed and an unconfirmed Chairperson. Staff and Ministry leaders denied
  with no audit row. Access lost between admission and the in-lock recheck
  disclosing and auditing nothing. An Administrator refused with 403 once their
  rule is removed. Forty strangers' identities adding no query and no row.
- The evaluator's own database and unit tests and the Chairperson reconciliation
  suite, 335 cases with the above, pass with the shared confirmed-assignment
  query. The 16-case navigation suite pins the entry to Administrators.
- Six browser cases on Chromium, Firefox and WebKit, 9 seconds: accessibility
  scans of three states at 320 and 1280 pixels, keyboard-reachable named table
  regions, each warning in its row, a refused attempt showing no sign-in, a
  consumer account on a domain suffix not in effect, and no control that could
  change a rule.
- Ruff lint and format and Markdown lint are clean.

Draft CI's ten-module fast selection is unchanged by this increment for now: the
open financial increment edits the same lines, so the substitution is made when
the delivery order is known.

## Checkpoint

Implementation, focused validation and the first
[review/fix round](stewardship-portal-users-reviews.md) are complete; that round
was single-source under the second September 20, 2026 Codex exemption, with
every accepted finding fixed. Two more rounds, full exact-head CI, DCO and
protected delivery remain open. M5 and Gate 3 remain open. No deployment, release, live-provider
write or database deletion is authorized by this increment.
