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
policy activation, recovery and login rule edit suites.
