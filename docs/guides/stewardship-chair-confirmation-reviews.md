# Stewardship Chairperson confirmation reviews

This ledger records the independent review/fix rounds of the
[Chairperson confirmation increment](stewardship-chair-confirmation.md), under
the delivery cycle's minimum of three rounds per PR. The Codex reviewer has
been out of quota since September 20, 2026; under the human's second
single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1, single-source under the second exemption

Reviewed `d2bfa172`, the complete diff from main `3fd51b5a`. Codex did not
answer; Claude, in one pass, returned three High, three Medium and fourteen
Low. All six validated findings were accepted and fixed.

- **High, two findings: a stale seed wedged the installer.** A confirmed
  Chairperson the promoted source no longer showed was refused by the
  evidence guard inside the activation, after the manifest was selected and
  the `yaml_activated` checkpoint written, and nothing recorded the refusal
  or restored the manifest, so every later pass would have failed the same
  way and blocked all configuration work. The installer now judges every
  seed under the activation's own lock, which source promotion takes too,
  before applying anything; a seed no longer true records the request as
  `invalid_candidate` under that lock and restores the previous
  configuration exactly as an actor refusal does. A case confirms, promotes
  the Chairperson away, installs, and proves the failure code, the restored
  policy, no seeded rows, and an ordinary change installing afterwards.
- **High: the installer could not take the evidence guard's lock.** The
  guard share-locks the current source pointer, which needs one update
  privilege the installer did not hold, so a confirmation would have failed
  under the deployed role while the cases installed as the owner. The
  installer gains the id-only column grant the actor recheck already uses,
  and every case now installs under the restricted installer role.
- **Medium: one radio group for every ambiguous row.** Only one row could
  name its Member per submission. Each ambiguous row now carries its own
  choice under the same field, and a case confirms two ambiguous rows at
  once with a partial answer refused.
- **Medium: the preview's observation was not one read.** The rows and the
  snapshot signed into the preview came from separate reads outside the work
  lock, so a promotion between them could sign rows from one snapshot as
  another's. The preview now observes the configuration, the rows and the
  snapshot together under the lock.
- **Medium: the confirmation schema admitted other sections.** A valid
  parish edit could travel in a confirmation request. The schema's patch is
  now login rules only, with a case for an appended edit refused.

The fourteen Low findings concerned wording and naming.

Post-fix validation: the confirmation, suggestion, login rule edit and
configuration service suites passed locally under the restricted roles, with
the confirmation and grant registry database-free suites and the users page
browser suite on Chromium.

## Round 2, single-source under the second exemption

Reviewed `09105962`, the round 1 result. Codex did not answer; Claude, in one
pass, confirmed the durable refusal, the installer grant, the per-row Member
choice, the locked preview observation and the login-rules-only schema
present and correct, and validated one Medium and eleven Low. The Medium was
accepted and fixed.

- **Medium: the confirmability judgement omitted one of the guard's facts.**
  The evidence guard also holds the applied configuration's ParishSoft
  organization to the seed's; the judgement compared the seed only with the
  source pointer, so a candidate naming another tenant would have reached
  the guard inside the activation and wedged the installer the way round 1
  removed. The judgement now reads the candidate's configured organization
  as the reconciliation owner does and refuses a mismatch or an absent
  organization before anything is applied, with a case for both.

The eleven Low findings concerned wording and naming.

Post-fix validation: the confirmation suite passed locally under the
restricted roles, with the confirmation database-free suite.
