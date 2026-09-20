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
rules show their roles, how many sign-ins have presented that signed Google
hosted-domain claim, the latest such sign-in and warnings. Exact-address rules
show the configured roles with each grant's provenance, the roles a sign-in
receives now, Ministry assignments with their source and whether the parish
source currently confirms them, the last sign-in and warnings. An empty role set
is labelled **Explicit deny**. A third table appears only when an assignment
belongs to someone with no exact rule, who relies on a domain rule.

This slice reviews and changes nothing. Role edits, rule creation and removal,
autosave with its queue and conflict recovery, security-event acknowledgement,
Chairperson suggestions, the assignment editor and suspended-assignment review
are later ADM-07 slices, so no ADM-07 task is checked here. Ministry names are
not shown beside assignment DUIDs yet; the assignment editor needs them and will
add them.

## Design

### One evaluator

Row shaping is pure and decides nothing. Effective roles and active Ministry
scope come from `resolve_roles`, the same evaluator every sign-in uses, given
the same applied canonical records and the same confirmed-assignment identities.
The page therefore cannot show an authority a sign-in would not receive: a
Chairperson-only Ministry leader role shows as suspended exactly when sign-in
would drop it, and an Administrator shows the roles Administrator implies.

### Warnings state facts, not advice

Each warning is derivable from applied policy and recorded sign-ins: a domain
rule no sign-in has yet satisfied, because an email suffix alone never matches;
an exact address that replaces its domain rule; a suspended Chairperson role; a
Ministry leader with no active assignment; an assignment with no role to use it;
a disabled Google identity; and an assignment for someone whom no rule could
make a Ministry leader.

### Last sign-in

The last sign-in is the Google identity's `verified_at`, which every completed
sign-in updates. It is durable, unlike a session row, which is pruned. An
address that has never signed in shows **Never**.

### Privacy and authority

Only an Administrator may see who else holds access, checked through
`Capability.MANAGE_USERS`; Staff and Ministry leaders are denied. The page takes
no parameters and refuses a query string, so an address never reaches a URL or a
log. Reads run under the shared work lock so the applied policy, the source
overlays and the identities are one coherent observation, and current access is
rechecked before the response is built. Each view records one audit event
carrying an outcome and a count, never an address or a role. The response is
never cached.

## Schema

No schema change. The audit action is an application vocabulary entry with no
SQL constraint, and the restricted web role already reads every table this page
uses, which the PostgreSQL cases prove by running under that role.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- Four database-free cases, under one second: sorted domain rows needing a real
  hosted claim rather than a matching suffix; address rows with effective roles,
  provenance, explicit denial and the domain-replacement warning; a seeded
  leader suspended until the source confirms a Chairperson, with the assignment
  and disabled-identity warnings; and assignments relying on a domain rule.
- Four PostgreSQL cases under the real web role, 15 seconds: the rendered tables
  and warnings over rules installed through the real configuration owner, a
  refused query string and POST, one audit row free of addresses, denial of
  Staff and Ministry leaders with no audit row, and an Administrator losing the
  page once their rule is removed. Chairperson-seeded authority cannot be
  installed by an ordinary patch, so its suspension is proven in the
  database-free cases and in the evaluator's own database tests.
- Six browser cases on Chromium, Firefox and WebKit, 9 seconds: accessibility
  scans of three states at 320 and 1280 pixels, keyboard-reachable named table
  regions, each warning in its row, and no control that could change a rule.
- Ruff lint and format and Markdown lint are clean.

Draft CI's ten-module fast selection is unchanged by this increment for now: the
open financial increment edits the same lines, so the substitution is made when
the delivery order is known.

## Checkpoint

Implementation and focused validation are complete. Dual-source review/fix
rounds, full exact-head CI, DCO and protected delivery remain open. M5 and
Gate 3 remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
