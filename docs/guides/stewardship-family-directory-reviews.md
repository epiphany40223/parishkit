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
