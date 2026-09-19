# Additional-information export reviews

Scope and acceptance: [complete exports](stewardship-information-exports.md).
[PR #67](https://github.com/epiphany40223/parishkit/pull/67) starts from verified
main `6a636680`. Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
Draft fast CI is feedback only; full corrected-head CI/DCO and three completed
dual-source review/fix rounds remain mandatory before protected delivery.

## Round 1

Pika session `20260919-121930-af6566` reviewed the complete increment
`6a636680..7c9dae1`. Exact permission preflight passed. Claude and Codex both
completed without degradation, mismatch or failed agents; Codex took 365 seconds.
Raw findings: no Critical/High, five Medium (one duplicate), nine Low below
the configured cutoff. Finalize retained four Medium findings.

All four are accepted and corrected under the delegated triage authority:

1. Status-page snapshot loading: defer the complete JSON document and select
   its retained source generation separately. A real-role native regression
   checks that no status query selects the document column.
2. XML-illegal XLSX characters: use visible, reversible Unicode notation at the
   format boundary, with a report legend; do not rewrite captured/source text.
3. PDF memory: count wrapped lines without storing them, then render bounded
   34-line pages. Avoid eagerly constructing all row/field pairs too. The test
   verifies consumption at each page save, not library memory internals.
4. PDF missing glyphs: inspect the exact bundled font used for drawing and
   visibly escape unsupported code points. Literal backslashes are doubled so
   escapes are unambiguous. Non-Latin, emoji and control-character regression
   tests assert preserved representations and absence of missing-glyph warnings.

Nineteen focused rendering/native-control tests pass in 1.2 seconds. Actual-role
status/all-format worker/download/regeneration validation passes in 17 seconds.
No schema changes are required by
these corrections. No accepted Medium-or-higher finding is deferred.

## Round 2

Pika session `20260919-123002-3b3a27` reviewed correction delta
`7c9dae1..368c545` with the surrounding export implementation and Round 1
dispositions. Exact permission preflight and both vendors completed without
degradation, mismatch or failed agents; Codex took 395 seconds. Raw findings:
no Critical/High, one Medium and six Low below cutoff. The retained Medium is
accepted: Unicode control/format characters can map to invisible font glyphs.
The PDF boundary now escapes categories Cc/Cf (except structural newline)
before consulting the font map. Regression coverage includes mapped U+FEFF,
joiner U+200D and DEL; original CSV/XLSX Unicode remains unchanged. Focused
rendering validation passes all eight tests in 1.08 seconds at `aa3b4b7`.

## Round 3

Pika session `20260919-123812-fe3ef9` reviewed `368c545..aa3b4b7` with the
prior corrections and complete export contracts available as context. Exact
permission preflight and both vendors completed without degradation, mismatch
or failed agents; Codex took 196 seconds. Raw findings: no Critical, High or
Medium; three Low below cutoff. Finalize returned APPROVE with no retained
findings. All three rounds are complete and all five accepted Medium findings
are fixed with passing focused validation.

## Candidate handoff

Preserve the final unsquashed tree on
`pr/stewardship-information-exports-reviewed`, then squash the feature/fixup
history and verify identical trees before the lease-protected push. Record the
resulting exact candidate and full CI receipt on PR #67. Draft validation passed
at all three reviewed checkpoints, but skipped suites are not merge evidence.
Full exact-head CI/DCO and protected delivery remain pending. No integrated
Phase 5/Gate 3, deployment or release approval is implied.
