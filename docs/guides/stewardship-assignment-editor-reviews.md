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

## Round 2

Claude only (Codex out of quota). Twelve raw findings, two validated, both
corrected:

- Medium: a removal still read the catalog through the Ministry activity
  editor's state, which is unavailable without a promoted source of the
  configured organization, while the page offered the removal button for
  every Administrator entry, the same class of gap round 1 corrected in a
  narrower precondition, and the page gated additions on a looser condition
  than the route. A removal now reads the applied configuration and, for the
  Ministry's name only, the promoted snapshot if any; one shared definition
  of a usable catalog decides both the page's addition forms and the route.
- Medium: three preview wordings and the dropped-from-catalog removal were
  unasserted. The exact rule without Ministry leader, the domain rule with
  it, a removal by DUID alone beside a catalog and a removal without any
  catalog are now asserted.

The ten findings the validation step did not confirm were not carried
forward.

## Round 3

Claude only (Codex out of quota). Twelve raw findings, two validated, both
corrected:

- Medium: the confirmation rechecked only the applied digest, so a source
  promotion between preview and confirmation, which changes no digest, could
  confirm an addition to a Ministry the new catalog dropped. The preview now
  signs the promoted snapshot it judged the catalog against and the
  confirmation rechecks it, as the Ministry activity and Chairperson review
  editors do; a removal signs whatever was promoted, nothing included.
- Medium: the assertions that the removal button is offered on an
  exact-address row matched the rule editor's own removal button, so the two
  removal-availability corrections were proven vacuously. They now match the
  assignment form's own label, count it per assignment, and the catalog-free
  case also asserts no addition is offered on the row.

The ten findings the validation step did not confirm were not carried
forward.
