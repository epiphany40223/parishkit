# Family directory reviews

Scope: [interactive Family/postal directories](stewardship-family-directories.md),
[PR #68](https://github.com/epiphany40223/parishkit/pull/68). The first round
reviews the complete increment; correction rounds use recorded endpoints and
surrounding contracts under the [delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).

## Round 1

Session `20260919-132652-432b8d` reviewed `75a20c0a..2361c23`.
Exact Claude permission preflight passed; both vendors completed without
degradation, failed agents or mismatch. Codex took 433 seconds. Raw findings:
zero Critical/High, four Medium, ten Low below cutoff. All four Medium findings
are accepted and fixed under delegated triage authority:

1. Main navigation now links the full Family directory and postal outreach.
   The old DUID/code-only route remains explicitly documented as source-loss
   recovery, not the normal report. It is not silently converted to a report
   that cannot work without a source snapshot.
2. A distinct directory audit action records closed filter enums, identifying
   filter presence flags, page, displayed count and matching count. Neither
   raw searches, codes nor contact values enter audit. Python and SQL both
   reject private values in these dimensions; correlation fingerprints are
   unnecessary for this report.
3. Blank/null address components never render as Python `None`. Missing records
   remain unavailable; present but blank records say no address was supplied.
4. Materialize the bounded page before aggregating display-only heads/phones
   and loading full address details. Population filtering uses recipient facts,
   phone existence and address search only; exact complement/counts remain
   source-coherent in the same statement.

Focused validation: 26 parser/privacy/address tests pass in 0.35 seconds; all
six actual-role PostgreSQL directory cases pass; nine browser cases pass in
14.5 seconds. The existing 52-Family fixture also checks actual query-plan
contact aggregation is bounded by the 50-row page. Full lint passes.

An independent installed-catalog comparison against the verified predecessor
found exactly one changed function, `stewardship_safe_context_v1`, and no
added/removed objects or changes in any other catalog class. Only that function
fingerprint is updated. This changes the fresh-install baseline, not database
upgrade compatibility; no retained database is deleted. Candidate full CI and
Rounds 2/3 remain pending.

## Round 2

Session `20260919-134140-98e2ef` reviewed correction delta `2361c23..f6cd8c0`.
Both vendors completed with no degradation, failed agents or mismatch; Codex
took 240 seconds. Raw findings: zero Critical/High, two Medium describing the
same issue, four Low below cutoff. The duplicate is folded into one accepted
correction: the directory's 503 page now links the retained code-only recovery
listing, explains its source-independent limits and preserves normal access
checks. The real-role regression follows that link while directory source
selection is unavailable and confirms the response remains private/no-store.
The test caught the shared guard's plain 503 bypassing the report error
template; the view now replaces only that non-streaming failure with its safe
recovery page after guard cleanup. No failed read is retried without admission.

The first permission probe was denied because Claude appended a stray `.` to
the validator command. The driver inadvertently started Pika/Codex after the
failed probe; Claude launch was held until the exact-command retry passed
all permission/result/byte checks under the human's standing retry authority.
No permission was widened and the failed probe is not counted as a review.
The final round repeats the proper preflight-before-review sequence.

The corrected source-loss navigation regression passes in 13.75 seconds;
Ruff lint/format and Markdown validation pass before the next round.

## Round 3

Session `20260919-134832-ae6485` reviewed `f6cd8c0..71ef82f`, with the earlier
corrections and report/read-guard contracts as context. Exact permission
preflight passed before launch. Both vendors completed without degradation,
failed agents or mismatch; Codex took 132 seconds. Raw findings: zero
Critical/High/Medium and one Low below cutoff. Finalize returned APPROVE.

All three review/fix rounds are complete. The five distinct accepted Medium
issues across Rounds 1/2 are fixed and validated; none remain unresolved.

## Candidate handoff

Preserve the unsquashed reviewed tree on
`pr/stewardship-family-directories-reviewed`, squash to one signed-off logical
feature commit and verify identical trees before the lease-protected push.
Record exact candidate/full CI/DCO on PR #68. Fast draft CI passed at each
reviewed checkpoint, but its skipped suites are not merge evidence. Protected
merge and the next increment require full candidate success and refreshed-main
confirmation. Full RPT-05 awaits complete-result exports; M5/Gate 3 remains open.
