# Stewardship security event acknowledgement reviews

This ledger records the independent review/fix rounds of the
[security event acknowledgement increment](stewardship-policy-security-events.md),
under the delivery cycle's minimum of three rounds per PR. The Codex reviewer
has been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1, single-source under the second exemption

Reviewed `7fc1913b`, the complete diff from main `4f0465fa`. Codex did not
answer; Claude returned one High, three Medium and eight Low. All four
validated findings were accepted and fixed.

- **High: any Administrator other than the actor settled the event.** The
  clearing rule let an acknowledgement by any identity other than the
  granting actor's clear the event for everyone, including the account the
  grant created and any Administrator who did not exist at activation, so
  an Administrator who granted Administrator to a second account they
  control could sign in as it and wave the alert away before any existing
  Administrator saw it. The rule is now keyed on the event's recorded
  recipients: only a recipient other than the actor settles the event for
  everyone; the actor's own settles it only when no other Administrator
  existed; and any other acknowledgement, the actor's, a newer
  Administrator's or the granted account's, clears it for that address
  alone. Cases prove the granted account cannot wave its own grant through,
  without a database and under the real roles.
- **Medium: acknowledgements were judged by identity, recipients by
  address.** Several Google identities may share one address, so the actor
  acknowledging through a second identity would have counted as another
  Administrator and settled the event, and viewing through it would have
  offered a row that could never be recorded again. Every judgement is now
  by normalized address, and whether an acknowledgement is the actor's own
  is decided when it is recorded, from the actor's identity or current
  address, so a later change of address cannot turn it into another
  Administrator's. A case acknowledges through a second identity at the
  actor's address and proves nothing more is recorded and nothing settles.
- **Medium: the "you have acknowledged" wording was unreachable.** Under
  the rule an event the viewer has acknowledged has always left their
  dashboard, so the note, its flag and the guide sentence describing it are
  gone.
- **Medium: coverage gaps.** Cases now cover the granted account
  acknowledging before any recipient, a second identity at the actor's
  address, the query-string refusal and a configuration under restore
  review.

The eight Low findings concerned wording and duplication already addressed
by the fixes above, or repeated the validated findings.

Post-fix validation: nine database-free, three PostgreSQL and six browser
cases passed locally, with the schema baseline, immutable-record inventory,
policy activation, recovery and login rule edit suites. The fresh-install
comparison was run again for the added own-flag column and found only that
column added; the strict fixture was updated after it.

## Round 2, single-source under the second exemption

Reviewed `60948b5b`, the complete diff from main `4f0465fa`. Codex did not
answer; Claude returned four Medium and eight Low. All four were accepted
and fixed.

- **Medium: an event with no recipients could never settle.** The trigger
  records a deployment's root activation, and any activation whose
  predecessor named no Administrator, with an empty recipient list, and the
  rule let neither clause fire unless the recorded actor's own identity
  acknowledged, so every present and future Administrator would have seen
  the first Administrator's own grant until each dismissed it. Such an event
  had nobody to await and is now settled by any acknowledgement, with a
  database-free case and a database case on the fixture's root event.
- **Medium: "no other Administrator existed" was judged against a mutable
  address.** The actor's own acknowledgement settled the event when the
  recipients were a subset of the address it was recorded under, which is
  the actor's address at acknowledgement time; an address changed since
  activation would have left the event for every later Administrator to
  dismiss. Since the actor of a portal-driven activation is always among the
  recipients, the rule now judges the recipient list by its length, with a
  case for a changed address.
- **Medium: the address branch of the own decision was untested.** The
  actor's first identity always acknowledged before the second, so the
  second's request was refused by the unique constraint before its own
  value mattered. The second identity now acknowledges first, and the case
  proves the row is the actor's own, the event gone for both identities and
  not settled for the other recipient.
- **Medium: the guide's audit totals were stale.** They predated the
  own-flag column; the section now reports the second comparison's totals
  and says the fixture was updated after each comparison, and the round-1
  entry records that comparison.

The eight Low findings concerned wording or repeated the validated ones.

Post-fix validation: ten database-free, three PostgreSQL and six browser
cases passed locally, with the schema baseline, immutable-record inventory,
policy activation, recovery and login rule edit suites.

## Round 3, single-source under the second exemption

Reviewed `08991a80`, the complete diff from main `4f0465fa`. Codex did not
answer; Claude returned one Medium and eight Low. The Medium was accepted
and fixed.

- **Medium: a recovery grant could be settled by its beneficiary.** The
  recovery-recipient trigger names the account operator recovery grants
  among the event's recipients, so that it is told, and a recovery event has
  no portal actor, so the rule let that account's acknowledgement settle the
  event for every Administrator who existed before it, the outcome round 1
  closed for portal-driven grants. The account the event grants now clears
  the event for itself alone, and settlement is judged against the
  recipients less that account, so a root activation with nobody else to
  await is still settled by anyone. Cases cover a recovery event
  acknowledged by its target, by a prior recipient, and one with nobody
  else.

The eight Low findings concerned wording or repeated the validated one.

Post-fix validation: eleven database-free, three PostgreSQL and six browser
cases passed locally, with the schema baseline, immutable-record inventory,
policy activation, recovery and login rule edit suites.
