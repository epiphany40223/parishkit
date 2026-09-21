# Stewardship Chairperson seed review

This guide records the seventh slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
the review of suspended Chairperson-seeded assignments and the provenance
action the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#chairperson-suggestions-and-assignments)
requires, under the
[authorization data model](../specs/stewardship/data/spec.md#administration-user-and-policy).
It continues the
[Chairperson confirmation increment](stewardship-chair-confirmation.md) and
follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

When a promoted source stops showing a confirmed Chairperson, the
reconciliation owner suspends the seeded assignment and opens a review
episode. The Portal users page now lists each open episode: the address and
Ministry, the Member the seed was confirmed for, why it is suspended, since
when and under which source generation, when the source last checked it,
whether that Member is a current Chairperson of another active Ministry now,
what the evaluator grants the address and its last successful sign-in. Each
row offers the decisions the specification names, and each exact-address
rule whose Ministry leader role comes only from a seed offers **Keep role
independently**:

- **Restore as Administrator entry** removes the seed and adds a manual
  assignment of the same address to the same Ministry, which the parish
  source never rewrites, so the scope stays in force until an Administrator
  removes it.
- **Remove** drops the seeded assignment alone; the rule and its roles stay,
  and a Ministry leader role held only by that seed then reads as suspended
  until the rule editor changes it or another suggestion is confirmed.
- **Keep role independently** records a manual origin beside the seeded
  Ministry leader grant, so source suppression no longer applies to the role;
  it creates no assignment and broadens no row scope.

The manual assignment editor of ADM-07.04, the autosave queue of .01 and the
tests of .05 remain later slices. No ADM-07 task is checked.

## Design

### Every decision is an ordinary policy request

The ordinary request schema already admits a removed record, a new manual
assignment and a new manual grant origin, each bound by intake and the
installer to the request that carries it, and the reconciliation owner already
closes a review episode when its seed leaves the applied policy at the next
activation. So no decision needs a new schema, a new writer or a page that
touches an episode: pure builders in `chair_review` produce the patch for
each decision and refuse, by closed reason, a missing seed, a role the seed
does not hold and a decision already in effect; the confirmation view
previews and confirms exactly as the rule editor does, under the same
session, capability, CSRF, freshness and policy guards; and the episode
closes as `assignment_removed` when the request activates. A restore is
refused where an Administrator's assignment to that Ministry already exists,
since the scope is already kept.

### The reason lives in the audit

The specification asks for an entered reason with a restore or removal. It
belongs to the decision, not to the applied policy, so it is carried in the
signed preview and recorded in the audit event written in the request's own
durable transaction through the intake's attach hook, as
`chair_review_decided` with the closed decision word, the Ministry and the
reason text, bounded and never an address. The audit context schema admits
exactly those two new fields.

### The page decides nothing new

The open episodes are read under the page's observation lock beside the
applied policy, the overlays and the current source, so a row never pairs one
generation's reason with another's evidence. Suspension reasons are the
overlay's own words; what the address is granted comes through the same
evaluator as every other table. The web role gains SELECT on the review
episodes, the reconciliation receipts that opened them and the retained
evidence: record ids, reasons, times and DUIDs, never contact data.

## Schema

One changed object: the audit context guard `stewardship_safe_context_v1`,
which admits the two new action fields, the closed decision word and the
bounded reason text, exactly as the application schema does. The audit
action itself is an application vocabulary entry with no SQL constraint, and
the three grants are runtime grants on existing tables.

An independent fresh-install audit against verified main `f26050d2`, with the
same catalog inspection the previous increments used, found the predecessor
matching its committed fingerprint, exactly one existing object changed, the
context guard function, and nothing added or removed; the candidate keeps
216 relations, 2,409 columns, 3,330 constraints, 991 indexes, 585 functions,
545 triggers and 28 policies. The strict fixture was updated only after this
inspected comparison. Pre-production fresh-install baseline only; no upgrade
path, and no retained database was deleted.

## Focused validation

- Six database-free cases: keeping a role adds only a manual origin, a
  restore replaces the seed with a manual assignment, a removal drops only
  the seed, each refusing a missing seed, an unseeded role and a decision
  already in effect with every result satisfying the ordinary policy rules;
  and the review rows stating the suspension's reason and time, what the
  evaluator grants now, whether a manual assignment already keeps scope, and
  ordering stably with a nameless Ministry.
- Three PostgreSQL cases under the real web and restricted installer roles
  with a confirmed seed the promoted source then stops showing: the row
  listed with its reason, Member and decision forms, a restore without a
  reason not understood, and a restore installing a manual assignment,
  closing the review at activation, keeping the role and scope at sign-in
  and recording the decision, Ministry and reason in the audit without an
  address; a removal dropping the seed, closing the review and leaving the
  seeded role reading as suspended, with a second decision refused; and
  keeping the role independently keeping it in force after the Chairperson
  goes while the seed stays suspended, with a second keep refused.
- The Portal users page, login rule edit, suggestion and grant suites pass;
  the grant registry and build contract database-free suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete; review/fix rounds, full
exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
