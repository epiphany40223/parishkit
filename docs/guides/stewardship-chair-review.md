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
since the scope is already kept. A restore or removal is offered, and
admitted, only for a seed with an open episode: the preview observes the
applied policy and the episodes under the work lock and refuses a seed the
source confirms, so no decision audit is ever written for an episode that
was not opened. Keeping a role independently needs no episode.

### The reason lives in the audit

The specification asks for an entered reason with a restore or removal. It
belongs to the decision, not to the applied policy, so it is carried in the
signed preview and recorded in the audit event written in the request's own
durable transaction through the intake's attach hook, as
`chair_review_decided` with the closed decision word, the Ministry and the
reason text. The audit context is not a container for personal data, so the
reason is bounded and refused, by the form and by the Python and SQL context
guards alike, when it carries an address-like token; the guards admit exactly
those two new fields. The preview pins the promoted snapshot as well as the
applied digest, since the episodes a decision rests on are written by source
promotion: a promotion between review and confirmation makes the decision
stale.

### The page decides nothing new

The open episodes are read under the page's observation lock beside the
applied policy, the overlays and the current source, so a row never pairs one
generation's reason with another's evidence. Suspension reasons are the
overlay's own words; what the address is granted comes through the same
evaluator as every other table; and whether the retained Member chairs
another active Ministry now is judged per review against the seed's own
Ministry and the applied activity, the rule the suggestion table applies, so
a Member still chairing the suspended Ministry never reads as chairing
another. The web role gains SELECT on the review
episodes, the reconciliation receipts that opened them and the retained
evidence: record ids, reasons, times and DUIDs, never contact data.

## Schema

One changed object: the audit context guard `stewardship_safe_context_v1`,
which admits the two new action fields, the closed decision word and the
bounded reason text without an address-like token, exactly as the
application schema does. The audit
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

- Twenty-six database-free cases: keeping a role adds only a manual origin,
  a restore replaces the seed with a manual assignment, a removal drops only
  the seed, each refusing a missing seed, an unseeded role and a decision
  already in effect with every result satisfying the ordinary policy rules;
  the review rows stating the suspension's reason and time, what the
  evaluator grants now, whether a manual assignment already keeps scope, and
  ordering stably with a nameless Ministry; and the action audit schema
  admitting the closed decision word and the bounded reason text in both
  directions and refusing them under another context kind.
- Six PostgreSQL cases under the real web and restricted installer roles
  with a confirmed seed the promoted source then stops showing: the SQL
  context guard held to the same audit cases; the row listed with its
  reason, Member and decision forms, a restore without a reason not
  understood, one whose reason carries an address refused, one whose
  preview a promotion overtakes refused as stale with no audit written, and
  a restore installing a manual assignment, closing the
  review at activation, keeping the role and scope at sign-in and recording
  the decision, Ministry and reason in the audit without an address; a
  removal dropping the seed, closing the review and leaving the seeded role
  reading as suspended, with a second decision refused; a restore or removal
  of a seed the source confirms refused with no audit written; a Member
  returning as Chairperson of a different Ministry read as chairing another;
  and keeping the role independently keeping it in force after the
  Chairperson goes while the seed stays suspended, with a second keep
  refused.
- The Portal users page, login rule edit, suggestion and grant suites pass;
  the grant registry and build contract database-free suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-chair-review-reviews.md) are complete,
single-source under the second September 20, 2026 Codex exemption, the third
validating nothing; full exact-head CI, DCO and protected delivery remain
open. M5 and Gate 3 remain open. No deployment, release, live-provider write
or database deletion is authorized by this increment.

## Protected delivery

PR #86 delivered candidate `41b6f1ab`, three logical commits plus the PR #85
receipt and the fast-selection rotation, whose tree `6b1eea25` is identical
to the retained commit-by-commit review history on
`pr/stewardship-chair-review-reviewed` (`0f5e251f`) and to the landed tree.
The three [review/fix rounds](stewardship-chair-review-reviews.md) were
single-source under the second exemption, with every accepted finding fixed
and the third validating nothing. The pull request was marked ready before
the candidate was pushed, but the ready-for-review run for the previous head
started after the push's run and cancelled it under the workflow's
concurrency group, so the request was marked ready again to create the
ready-candidate run for the candidate. In that run PostgreSQL shard 11
passed every case and then failed to upload its evidence artifact, a
GitHub-side 403 unrelated to the change; its failed jobs were re-run on the
same head. Exact-head ready-candidate CI `35646656758` and DCO then passed
all 25 checks, from 19:43:59 to 20:17:23 UTC on September 21, 2026 (33
minutes 24 seconds, including the re-run). `origin/main` had no intervening
commits since the candidate's base `f26050d2`. Protected auto-merge landed
as `a69cb0983343ba6adc480461867e43b81d5a56ba` at 20:18:46 UTC and was
verified on freshly fetched `origin/main`, whose second parent's tree is the
candidate's, before the next increment started. This used the standing
delivery authority, without deployment or release, and supersedes the
checkpoint above. The cancelled and retained-history runs, and the failed
first attempt of the shard's evidence upload, are not counted as acceptance.

The Chairperson seed review increment is delivered: the Administrator sees
each suspended seeded assignment with its Member, reason and source state,
and restores it as a manual assignment, removes it, or keeps a seeded
Ministry leader role independently, each an ordinary policy request whose
entered reason is recorded in the audit. ADM-07 stays open for the manual
assignment editor and the autosave queue. M5 and Gate 3 remain open.
