# Stewardship Chairperson seed review reviews

This ledger records the independent review/fix rounds of the
[Chairperson seed review increment](stewardship-chair-review.md), under the
delivery cycle's minimum of three rounds per PR. The Codex reviewer has been
out of quota since September 20, 2026; under the human's second single-source
exemption, recorded in [the overall plan](../plans/stewardship/overall.md), a
completed Claude-only pass counts as a round through September 25, 2026, and
each round records which sources answered.

## Round 1, single-source under the second exemption

Reviewed `d7e2d03c`, the complete diff from main `f26050d2`. Codex did not
answer; Claude, in one pass, returned three Medium and thirteen Low. All
three validated findings were accepted and fixed.

- **Medium: a decision could be made about a seed the source confirms.** The
  page offered restore and removal only on suspended rows, but the route
  accepted any address and Ministry, so a confirmed seed could have been
  converted to a manual assignment or removed, with a decision audit for an
  episode never opened. The preview now observes the applied policy and the
  open episodes under the work lock and refuses a restore or removal of a
  seed with no open episode by a closed reason; a case posts both for a
  confirmed seed and proves the refusal and the absent audit.
- **Medium: "chairs another active Ministry" counted the Ministry under
  review.** The flag was judged from any current relationship of the Member,
  so a Member still chairing the suspended Ministry, inactive locally or
  reached by a changed address, read as chairing another. It is now judged
  per review against the seed's own Ministry and the applied activity, with
  a case in which the Member returns chairing a different Ministry.
- **Medium: the new audit fields had no regression cases.** Database-free
  cases now hold the action schema to the closed decision word and the
  bounded reason text in both directions and for another context kind, and
  a PostgreSQL case holds the SQL context guard to the same cases.

The thirteen Low findings concerned wording and naming.

Post-fix validation: the review, Portal users page and audit context suites
passed locally under the restricted roles, with the review and rows
database-free suites and the users page browser suite on Chromium.
