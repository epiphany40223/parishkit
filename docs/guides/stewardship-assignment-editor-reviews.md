# Stewardship manual assignment editor reviews

This ledger records the independent review/fix rounds of the
[manual assignment editor increment](stewardship-assignment-editor.md), under
the delivery cycle's minimum of three rounds per PR. The Codex reviewer has
been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1

Claude only (Codex out of quota). Nine raw findings, three validated, all
corrected:

- High: the active-catalog check gated removals as well as additions, so an
  Administrator entry assignment to a Ministry since deactivated, or dropped
  from the catalog, could not be removed although the page offered the
  button. The catalog now gates additions only; a removal is judged by the
  applied policy, the catalog serves it the Ministry's name, and the preview
  says when the catalog no longer has one.
- Medium: the preview judged the effect from a rule's configured roles, so
  an Administrator's exact rule was said not to grant Ministry leader, which
  the evaluator and the users page contradict. The roles now come from the
  policy evaluator over the resulting policy; an Administrator is told the
  assignment adds no scope, and a removal states that its scope ends.
- Medium: the route cases left the branches most likely to regress
  untested. The exact-rule, Administrator, unnamed-address and removal
  wordings are now asserted, and a new case deactivates the Ministry
  through the activity editor and then removes the assignment.

The six findings the validation step did not confirm were not carried
forward.
