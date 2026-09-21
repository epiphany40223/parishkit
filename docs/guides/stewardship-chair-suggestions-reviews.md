# Stewardship Chairperson suggestions reviews

This ledger records the independent review/fix rounds of the
[Chairperson suggestions increment](stewardship-chair-suggestions.md), under
the delivery cycle's minimum of three rounds per PR. The Codex reviewer has
been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1, single-source under the second exemption

Reviewed `3d62c3d5`, the complete diff from main `b1f80661`. Codex did not
answer; Claude, in one pass, returned three Medium and nine Low. All three
validated findings were accepted and fixed.

- **Medium: the row showed configured roles as what the address has.** The
  evaluator grants an exact address only what the source confirms and a
  hosted domain only to a Google account presenting its claim, so a seeded
  Ministry leader role the source no longer confirmed read as held, and a
  domain rule's roles read as held by an address that may never present the
  claim. The row now shows what the evaluator grants an exact address, marks
  a suspended Ministry leader role and an explicit denial, and shows a
  domain rule's roles as conditional on its claim; the database-free and
  PostgreSQL cases assert the suspended and confirmed readings.
- **Medium: a nameless Ministry would have failed the page.** A Ministry
  payload without a name is valid source; the view returned NULL and the
  row ordering could not sort it. The view coalesces the name and the rows
  guard it, as the pure source suggestions do, with a case for it.
- **Medium: the view's address ownership was a per-row correlated
  aggregate.** Evaluated for every Chairperson roster row under the work
  lock, its cost was the product of the roster and the snapshot's Members.
  Ownership is now aggregated once per snapshot and address and joined.

The nine Low findings concerned wording, counts and naming.

Post-fix validation: the suggestion rows and Portal users page suites, the
Chairperson projection suite, the web grant suite and the schema baseline
passed locally, with the rows, grant registry and build contract
database-free suites, and the users page browser suite on Chromium.

## Round 2, single-source under the second exemption

Reviewed `b39ea2af`, the round 1 result. Codex did not answer; Claude, in one
pass, confirmed the granted-roles rule column, the coalesced Ministry name
and the once-per-snapshot ownership aggregate present and correct, and
validated no finding. Its seven Low remarks concerned wording and naming.
