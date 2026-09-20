# Ministry follow-up review ledger

Review evidence for the [Ministry follow-up increment](stewardship-ministry-followup.md),
PR #72. Every round is single-source under the
[September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle):
the Codex reviewer's workspace is out of credits, so each Codex review aborted
on its first turn with `Your workspace is out of credits` and produced no
findings. Raw severities are the Claude reviewer's. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on.

## Reviewed endpoints

The branch was rebased onto main after each unrelated CI correction landed.
`git range-diff` showed every patch identical each time, so reviewed content is
unchanged; the table maps each reviewed SHA to its current equivalent.

The delivered branch squashes that history into logical commits. The complete
commit-by-commit history, including each reviewed SHA below, is retained on
`pr/stewardship-ministry-followup-reviewed`, whose tree is identical to the
delivered head. That branch is review evidence only and is never merged.

| Round | Reviewed SHA | After final rebase | Scope |
| --- | --- | --- | --- |
| 1 | `6374f65f` | `0d338a1` | Complete diff from main, 3,036 lines, two shards |
| 2 | `35f5bc0b` | `053f072` | Round 1 corrections, 351 lines |
| 3 | `3ef0e3b1` | `3ef0e3b` | Round 2 corrections, 130 lines |

## Round 1

30 raw findings; six validated, all Medium; no High or Critical. All six were
accepted and fixed in `053f072`.

- **Assignee choices leaked across Ministries.** The queue computed assignee
  choices from the caller's Ministry filter, validated only as a number. SQL
  returned no rows for a Ministry outside a leader's scope, but the page still
  listed who may follow it up, revealing other Ministries' leaders. The filter
  is now honoured only when SQL placed that Ministry in the caller's scope.
- **A stale bulk selection read as a malformed form.** Each row's change was
  built before its version was checked, so a row that closed since the page
  loaded returned 400 instead of the promised 409. Replay, version and open
  state are now decided before the change is built.
- **The source instant rendered empty.** It reached the template as JSON text,
  which Django's date filter renders as nothing; the browser fixture's real
  datetime hid it. The metadata instant is now parsed.
- **Guard branches were unproven.** The revision guard's assignee, outcome,
  contact-time and length branches were never reached because the application
  rejects first. Direct web-role inserts now reach them.
- **Assigning a New request needed two controls.** New and Assigned are now
  derived from the assignee in the single edit as they already were in bulk.
- **A revoked assignee vanished silently.** The form fell back to Unassigned
  without explanation. It now names them and says the request cannot stay
  assigned to them, since authority is rechecked on every edit.

Post-fix validation: 46 PostgreSQL and database-free cases and 24 browser cases
passed locally.

## Round 2

16 raw findings; one validated Medium; no High or Critical. Accepted and fixed
in `3ef0e3b`, with five Low notes adopted alongside it.

- **Medium: the guard-branch inserts could pass vacuously.** They proved the
  refusal happened at the statement but not which branch refused, and both
  guard exceptions share one SQLSTATE. A positive control now shows the
  unmodified row passes the guard in the same savepoint, and each variant
  matches its branch's message.
- Low, adopted: a test that one bulk form key cannot be replayed to another
  assignee or version; a direct assertion on the parsed instant; parsing the
  assignee once; parsing only the displayed metadata instant; and the
  revoked-assignee notice as one translatable sentence.
- Low, recorded as limits: bulk assignment repeats the replay, version and
  open-state checks that `_revise` also makes, because the row's state must be
  known before its change can be built; the New/Assigned derivation lives with
  the form parser and bulk service rather than inside `update_request`, because
  `WorkflowChange` validates a complete state at construction; and a revoked
  assignee is surfaced on the mutable request page, not yet in the queue.

One run of the post-fix selection reported three `403` responses at the shared
leader sign-in fixture, during a local stall in which the tool call itself hung
for about six minutes. The same code then passed as a single case, as the
complete file, and as the full selection, and the change touches no sign-in
code. It is recorded as a transient local condition, not counted as a pass.

Post-fix validation: 46 PostgreSQL and database-free cases and 12 follow-up
browser cases passed locally.

## Round 3

Three raw findings, all Low and below the cutoff; no validated finding and no
High, Critical or Medium. The reviewer confirmed the Round 2 corrections: the
positive control is sound, because the inner savepoint is rolled back, the
outer work transaction commits nothing and the deferred effect trigger has no
surviving row to fire for; each refusal message matches the guard branch that
actually fires; the rebinding test cannot pass vacuously, since an unbound key
would raise a stale error instead; and the `blocktranslate` variable remains
auto-escaped.

The three Low notes are deferred with rationale rather than changed after the
final review:

- The SQL still projects `observed_at`, which nothing displays, beside the
  parsed `source_as_of`. It is harmless today; removing it would change a
  function body and need another schema audit for no behavioral gain. A later
  edit of that function should drop it.
- Three guard-branch variants change more than one value, because the revision
  constraints require a contact's channel and time together and an outcome's
  state. Each still matches the one branch message that refuses it.
- The bulk rebinding test pins a changed assignee and a changed version, not a
  changed selection. The per-row key is derived from the request itself, so a
  different selection produces different keys rather than a rebinding.

No correction was needed, so `3ef0e3b1` is the reviewed implementation content.
The exit criteria are met: three completed rounds, no validated High or
Critical finding in any round, all seven accepted Medium findings fixed, and
passing post-fix validation. Full exact-head CI, DCO and protected delivery
remain required.
