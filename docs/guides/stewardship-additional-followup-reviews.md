# Staff follow-up review ledger

[Scope and validation](stewardship-additional-followup.md).

## Incomplete first attempt

Session `20260919-103615-da16ad` reviewed `b60e39f4..4786542`.
Claude completed with one Medium and nine Low raw findings. Codex produced no
structured artifact; finalize reported `agents_failed: codex-reviewer` and
`codex-reviewer: result artifact missing — codex produced no structured output`.
This degraded attempt counts as zero completed dual-source rounds.

The validated Medium notes-length finding is fixed: native textarea CRLF/CR
is normalized to LF before command validation, storage and replay comparison.
A 5,000-character multiline regression proves accepted length and stable
resubmission. The claim that ordinary CRLF grows on every browser re-save is
not required for the valid transport-length defect or its fix.

Separately, draft CI caught the missing explicit Docker allowlist entry for
the new SQL asset. Both default-deny copies now admit that exact file; no broad
directory grant was added. The existing packaging test covers this correction.
The failed fast run had 295 passing tests and one packaging failure, not a
PostgreSQL or browser failure. Fresh full-diff dual review is required next.

## Round 1

Session `20260919-104321-086c55` reviews `b60e39f4..7239b5c` in full. Both
vendors completed without failures, degradation or verdict mismatch. Raw
severities: two Medium, eleven Low, zero High/Critical. The Codex process
completed successfully in 353 seconds; its completion receipt was verified
before finalization.

Both Medium findings are accepted and fixed:

- Claude: use the existing weekly history function for the queue's reported
  and corrected indicators. Its actual mode is `production`, not the suggested
  `live`. A completed cohort, not one delivered recipient, establishes completed
  correction coverage. Narrow the shared history projection instead of widening
  web permissions. Existing actual-role partial/completed-cohort scenarios now
  verify queue parity.
- Codex: Staff edits must not invalidate post-close digest resolution. Shared
  SQL digest tokens represent immutable submitted text's actionable/superseded/
  withdrawn state independently from optimistic Staff versions. Snapshot capture,
  SQL validation and post-close coverage all use that token. Both real closed
  resolution scenarios prove Staff edits preserve coverage while a later Family
  response still invalidates it.

Low findings also addressed: compute digest history once rather than correlated
per-item scans; centralize page size; expose the cross-module page parser; deny
non-live route targets early; add cross-campaign denial and displayed-version
history-cap tests; and verify actual browser POST URL/body behavior with and
without scripts. Remaining Low suggestions about broader localization, neutral
gate wording and Family-name helper extraction are deferred: current English
labels match the existing shared portal, gated writes remain denied, and current
source fallback parity is explicitly tested. The two below-cutoff Codex Low
findings were not retained by finalization; no Medium-or-higher is unrecorded.

Validation: six corrected report/digest PostgreSQL scenarios pass in 42 seconds;
the two post-close cases passed in the prior combined run. That combined run
also caught six permission failures in the initial shared-history integration,
all resolved by the narrow projection above. Nine strengthened browser cases
pass in 20 seconds. Ruff check/format pass. A repeated independent fresh-schema
audit verifies only the explicitly described additions and three changed
objects; the revised strict fixture follows that inspection.

## Round 2

Session `20260919-105631-158a10` reviews `7239b5c..53cd911`. Both vendors
completed, with no failed/degraded reviewer or verdict mismatch. Codex completed
in 257 seconds with no findings. Claude reported one Medium and five Low; no
High/Critical findings occurred.

Accepted Medium: completed correction coverage may represent an explicit
post-close skip, not provider delivery. Rename the flag `correction_resolved`
and describe both completed delivery and explicit post-close decisions. The
query continues to use the shared semantic history, without falsely asserting
that a skipped message was sent. Browser fixtures/regressions cover the actual
resolved wording. Add a Python comment explaining why captured digest tokens
are not optimistic Staff versions (Low).

Other Low dispositions: a same-campaign rehearsal item cannot exist under the
existing live-only additional-information SQL guard, so do not fabricate an
impossible retained row to test defense-in-depth mode filtering. The function
is installed early because the later post-close view resolves it at creation;
fixed search paths preserve namespace safety. Keep the daily coverage branch
consistent rather than change its established empty-item contract. Whole-history
cost is bounded and computed once; the reviewer explicitly requires no change.
These are not unresolved Medium-or-higher findings.

## Round 3

Session `20260919-110309-5fdcc2` reviews `53cd911..70113dc`. Both vendors
completed without failed/degraded reviewers or verdict mismatch; Codex completed
in 146 seconds. Raw findings: one Medium and three Low, zero High/Critical.

Accepted Medium: an entirely revoked correction-recipient cohort can complete
as empty, without delivery or an explicit post-close decision. Use neutral
resolution wording that explicitly says resolution does not necessarily mean
email delivery, rather than attempting an exhaustive list of resolution causes.
The existing correction test now also exercises an entirely revoked cohort.

Address both retained Claude Low gaps: browser fixtures/assertions render both
resolved and unresolved explanations; existing real post-close subset scenarios
now assert the Staff queue's resolved flags. No unsupported database state is
fabricated. The remaining below-cutoff Codex Low was not retained by finalization.
No accepted Medium-or-higher finding remains unimplemented. These corrections
and passing post-fix checks complete this third round; no High/Critical finding
requires extending the loop under the controlling delivery policy.

Final round validation: four actual PostgreSQL correction/resolution scenarios
pass in 62 seconds; nine three-engine browser checks pass in 15 seconds. Ruff
and Markdown checks pass. The required three completed dual-source rounds have
zero unresolved accepted Medium-or-higher findings and no High/Critical in the
final round. Protected exact-head CI/DCO and merge still remain required.

## Candidate CI correction

Candidate `32b2642` had the same tree as preserved reviewed history `7c6ed8c`.
Full run `35451133180` passed validation, all three browser engines, Compose
and all four operational scenarios. PostgreSQL shard 2 passed 328 tests but
found one incomplete test-registry entry: the all-model immutability inventory
assumed a conventionally named trigger for the new information revision table.
Its combined insert/update/delete guard already exists and rejects rewrites.

Register that exact trigger/function in the existing shared-guard map and
conditional-insert set. The unchanged assertions still require an enabled
row-level BEFORE INSERT/UPDATE/DELETE trigger, SQLSTATE 23514, and rejection
of every non-insert. This is a test-inventory correction, not a runtime/schema
change or an immutability exemption. The registry test and existing actual-role
history/replay/direct-SQL test pass together: two cases in 15 seconds. Ruff
check/format pass. No material implementation change requires another review
round; the three completed rounds and their dispositions remain applicable.

Returning the PR to draft cancelled unfinished shards through workflow
concurrency; those shards supply no passing evidence. Full corrected-head
CI/DCO remain mandatory before protected merge.
