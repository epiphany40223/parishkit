# Stewardship Chairperson confirmation

This guide records the sixth slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
confirming the Chairperson suggestions the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#chairperson-suggestions-and-assignments)
shows the Administrator, as the sole path that creates Chairperson-seeded
authority under the
[authorization data model](../specs/stewardship/data/spec.md#administration-user-and-policy).
It continues the
[Chairperson suggestions increment](stewardship-chair-suggestions.md) and
follows the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

The suggestion table on the Portal users page gains a selection column and a
review button. An Administrator selects suggestions, chooses the Member where
several active Members use one address, and reviews exactly what each
confirmation creates: a new exact-address rule with the roles it inherits
from its hosted-domain rule preselected and Ministry leader added, or an
existing rule widened by the seeded Ministry leader grant, and one
Chairperson-seeded assignment per Ministry, each naming the selected Member.
Confirming records one configuration request that the installer activates,
after which the seed is in force on the page and at sign-in.

The **Keep role independently** action, the suspended-assignment review with
its restore and removal, and the manual assignment editor of ADM-07.04 remain
later slices, with the autosave queue of .01 and the tests of .05. No ADM-07
task is checked.

## Design

### Seeded authority has one door

The ordinary policy schemas refuse a new Chairperson-seeded assignment and a
new or changed seeded grant origin, so a confirmation cannot be an ordinary
policy request. It carries its own request schema, `chair-seed-patch-v9`,
built over the newest document format with every fence of that format and one
substitution: `validate_seed_change` replaces the ordinary policy-change rule
with exactly a confirmation's effects. A new rule must have the seed as its
creation origin, the roles its domain rule inherited as manual grants and
Ministry leader with the seeded origin; an existing rule may gain only that
origin; every seeded assignment must be new; nothing may be removed or
otherwise changed; each touched address must gain a seeded assignment and
each seeded assignment must belong to a rule carrying the seeded grant; and
every seeded origin and assignment names one operation, which intake holds to
the request's own identity. The pure builder `seed_patch` produces exactly
that shape and refuses, by closed code, an already seeded Ministry and an
empty selection.

### The selected Member reaches the installer

The applied YAML never names a Member, and the specification requires the
confirmed Member to be retained so a seed is never transferred when an
address is shared or reused. The selection therefore travels beside the
request: the signed preview carries the chosen Member for each seeded
assignment record in the patch, and confirmation writes one immutable
`ChairSeedIntent` per record inside the request's own durable transaction,
through a new `attach` hook of the intake. A deferred SQL constraint binds
each intent to a request of the confirmation schema, by the same actor, whose
patch adds that very seeded assignment bound to the request's identity; the
installer refuses a confirmation request lacking an intent for any seeded
assignment it adds.

Inside the activation transaction, under the lock that source promotion also
takes, the installer first judges every seed the request adds by the facts
the evidence guard demands: the candidate's configured ParishSoft
organization is the seed's, the selected Member is a current Chairperson of
that Ministry at that address in the promoted snapshot for that organization,
and the Ministry is active under the candidate. A seed no longer true is refused there, recorded
as `invalid_candidate` under that lock and followed by the same restore of
the previous configuration an actor refusal receives, so a suggestion is
confirmable only while it is still true and a stale one can never wedge the
installer. Otherwise, after the activation row has moved the runtime pointer
and before the Chairperson reconciliation judges the new seed, the installer
records `ChairSeedEvidence` from each intent and the projection's roster
keys, so a confirmed seed is born confirmed rather than `missing_binding`;
the evidence guard remains the backstop, and it now also holds every row a
deployed role inserts to an intent naming that assignment, organization and
Member, so the installer's insert can record only what an Administrator
chose and no installation or refresh can infer a Member from an address. It
share-locks the current source pointer, so the installer holds the same
id-only update grant the actor recheck uses. The web role gains the intent
table and the installer the evidence insert; nothing else may write either.

### The page decides nothing new

The form's choices are the page's own suggestion rows, so a foreign selection
is refused before anything is built. A row whose address several active
Members use is confirmed only with an explicit Member, chosen per row so
several such rows confirm together. The preview observes the configuration,
the rows and the promoted snapshot as one read under the work lock, as the
page does, and is signed against the applied digest and that snapshot; a
policy change or a source promotion between review and confirmation refuses
the confirmation as stale. The confirmation schema's patch may touch login
rules only. Every guard of the rule editor applies: current session and
capability, CSRF, the actor rechecked at intake and activation, the
last-Administrator rule of the complete resulting policy.

## Schema

One added relation, `stewardship_chair_seed_intent`, with its immutable guard
and binding constraint trigger, registered after the suggestion view and
re-included in the container build context; and two changed objects: the
request schema check on `stewardship_config_request`, admitting
`chair-seed-patch-v9` in the model, its migration state and the SQL baseline,
the checkpoint transition rule, which admits the installer's
`invalid_candidate` refusal of a confirmation request after
`yaml_activated`, never from the web role, exactly as it admits an actor
refusal, and the evidence guard, which now holds every row a deployed role
inserts to a selection bound to a confirmation request, so the installer's
new insert grant can record only what an Administrator chose.

An independent fresh-install audit against verified main `3fd51b5a`, with the
same catalog inspection the previous increments used, found the predecessor
matching its committed fingerprint, exactly one added relation with its eight
columns, primary, unique and check constraints, foreign key and indexes, two
added functions and two added triggers, and exactly three existing objects
changed, the request schema check, the checkpoint transition function and
the evidence guard function; nothing removed. The candidate has 216 relations, 2,409 columns, 3,330
constraints, 991 indexes, 585 functions, 545 triggers and 28 policies. The strict fixture was updated only after this
inspected comparison. Pre-production fresh-install baseline only; no upgrade
path, and no retained database was deleted.

## Focused validation

- Fifteen database-free cases: a new rule inheriting domain roles as manual
  grants beside the seed, an existing rule gaining only the seeded origin, an
  already seeded Ministry and an empty selection refused, ten tampered shapes
  each refused by the seed rule while the ordinary rule refuses the genuine
  confirmation, the schema refusing an appended parish edit, and an applied
  seed read as ordinary provenance afterwards.
- Seven PostgreSQL cases under the real web and restricted installer roles
  with real promoted source: nothing selected refused by the builder's own
  words, then a confirmation previewed, confirmed and installed creating the
  seeded rule, grants, assignment, intent and retained evidence with the
  seed born confirmed on the page and the same Ministry refused again; a
  confirmation request recorded without its selections refused by the
  installer as an invalid candidate; a shared address chairing two Ministries refused
  without its Members or with a partial answer and confirmed with both; a
  Chairperson promoted away after confirmation failing the request as an
  invalid candidate with the previous policy restored and an ordinary change
  installing afterwards; a candidate naming another organization, or none,
  judged not confirmable; seeded authority refused to the ordinary schema, a
  foreign intent refused by SQL and evidence refused to the web role; and a
  source promoted after the preview making it stale.
- The suggestion, Portal users page, login rule edit, Chairperson projection,
  grant and storage suites and the schema baseline pass; the grant registry
  and build contract database-free suites pass with the new table and file.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-chair-confirmation-reviews.md) are complete,
single-source under the second September 20, 2026 Codex exemption, with
every accepted finding fixed; a correction check of the third round's
fixes, full exact-head CI, DCO and protected delivery remain open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
