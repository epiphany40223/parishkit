# Multi-Ministry packet review ledger

Review evidence for the [multi-Ministry packet increment](stewardship-ministry-packets.md),
PR #75. The Codex reviewer returned during this PR: its workspace had credits
again and its findings were folded into Round 1. These rounds are therefore
dual-source, and the [September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
no longer applies, by its own terms. Severities are the raw reviewer values.
Findings below the tool's Medium/confidence cutoff are counted but listed only
where they were acted on.

The delivered branch squashes the review corrections into logical commits. The
complete commit-by-commit history, including every reviewed SHA below, is
retained on `pr/stewardship-ministry-packets-reviewed`, whose tree is identical
to the delivered head. That branch is review evidence only and is never merged.

## Round 1

Reviewed `c2ebbe39`, the complete 1,857-line diff from main `bd5522f6`. Both
sources completed. 19 raw findings; five validated, all Medium, three from
Claude and two from Codex; no High or Critical. All five accepted and fixed.

- **Claude: chain behavior unproven for packets.** The PostgreSQL case never
  resubmitted, so every asserted value came from chain depth zero, and only the
  withheld-contact branch was asserted for a leader. A same-intent Family
  resubmission now proves the successor rows carry contact dates recorded on
  their predecessors and that history never resurrects the predecessors, and
  the leader capture asserts the published phone as well as the withheld email.
  A changed-action resubmission cannot be expressed through the real Family
  form in this harness, because a Member can only ask to leave a Ministry they
  are in and to join one they are not; that predicate stays with the shared
  chain function.
- **Claude: the selection form could dead-end.** A default "all" radio above
  unscripted checkboxes meant ticking Ministries without switching the radio
  reached a bare 400. The radio is gone: no ticked Ministry means every
  authorized one and any tick is the exact selection, so a native form cannot
  reach an ambiguous state.
- **Claude: chair names rescanned the roster per Ministry.** A correlated
  subquery parsed every roster row once per selected Ministry, inside the
  capture trigger while it holds the shared work lock, so a parish-wide packet
  could stall Staff follow-up. Chairs are now computed in one materialized pass
  grouped by Ministry.
- **Codex: spreadsheet cells truncated silently.** The library cuts a string at
  32,767 characters without error. Rendering now refuses such a value rather
  than publish an incomplete packet that looks complete; CSV and PDF keep the
  complete value.
- **Codex: invalid worksheet titles.** Truncation could leave a trailing
  apostrophe and `History` is reserved. Titles are trimmed again after
  truncation and reserved names are avoided.

A fresh install after the corrections differed from the first audited candidate
only in the packet function body, with nothing added or removed, before the
fingerprint was updated again. Post-fix validation: 18 database-free, three
PostgreSQL, 17 schema-contract and 15 browser cases passed locally.

## Round 2

Reviewed `8ce7d22b`, the 451-line correction delta from `c2ebbe39`. Both sources
completed. 11 raw findings, all Low and below the cutoff; no validated finding
and no High, Critical or Medium. The reviewer verified all five Round 1
corrections: the grouped chairs pass is equivalent to the former subquery with
no cross-Ministry leakage and an empty list for a chairless Ministry; the form
grammar is closed and unambiguous; XLSX refuses an over-limit cell before any
byte is written; titles are re-trimmed and `History` is reserved; and the new
cases exercise chain depth one and the published-contact branch.

Low notes adopted:

- The roster has typed, indexed `member_key` and `ministry_key` columns, so the
  chairs pass now joins and groups on them instead of extracting those two keys
  from JSON. The payload is still parsed once per roster row, because `current`
  and the role name exist only there.
- Worksheet titles trim before truncating as well as after, so spaces replacing
  leading forbidden characters no longer use the 31-character budget; an
  unreachable fallback was removed.
- The limit constant says what it measures, and the reserved-title comment
  distinguishes the application's `History` from this workbook's own sheet.
- Tests now assert that PDF keeps an over-limit value as one unbroken token,
  that a chair's name matches rather than merely counting one, and that the
  unticked native form posts no selection.

Low notes deferred with rationale:

- The spreadsheet refusal is deterministic, but the shared export worker
  retries every render failure up to five times before failing. A permanent
  failure class belongs to that shared job machinery, not this increment. The
  outcome is still correct, only slower, and the input needs a single cell
  beyond 32,767 characters, such as about 1,500 chairs in one Ministry.
- The same silent truncation exists in the shared complete-text spreadsheet
  renderer. Its inputs are bounded well below the limit today, so that guard
  is left to a change of that renderer rather than widened here.
- The limit is counted in code points, which is where the library cuts. The
  application's own limit is in UTF-16 units, so a value made largely of
  characters outside the Basic Multilingual Plane could still exceed it.

A third fresh install differed from the second only in the packet function
body. Post-fix validation: 18 database-free, three PostgreSQL, 17
schema-contract and 15 browser cases passed locally. The schema-contract cases
were run after Round 3 pointed out that this line had omitted them.

## Round 3

Reviewed `25602c3e`, the correction delta from `8ce7d22b`. Both sources
completed. Six raw findings, all Low and below the cutoff; no validated finding
and no High, Critical or Medium. The reviewer confirmed that the typed roster
keys are guaranteed text-equal to the payload's keys by the roster's payload
guard trigger, which refuses a row whose columns differ from its canonical
payload and refuses updates, that the removed title fallback was unreachable,
and that the rename left no stale reference.

Two notes corrected this ledger, as recorded above: Round 2 had overstated how
much JSON parsing the typed keys removed, and had omitted the schema-contract
cases from its validation line, which were then run and passed.

The remaining Low notes are deferred with rationale rather than changed after
the final review:

- The sibling Ministry report still filters the roster through payload keys.
  Aligning it is a separate change to a reviewed query with its own audit.
- The browser case registers the same route handler twice. It is harmless and
  was left to avoid changing a test after its final review.
- The over-limit PDF assertion exercises the paginator rather than the drawn
  bytes; the page writer is shared with, and covered by, the complete-text
  renderer's cases.
- The chair-name assertion compares two values derived from the same source
  name expression. It does prove that the typed Member join resolves to the
  same Member as the request row, which was the point of that change.

No correction to the implementation was needed, so `25602c3e` is the reviewed
content. The exit criteria are met: three completed dual-source rounds, no
validated High or Critical finding in any round, all five accepted Medium
findings fixed, and passing post-fix validation. Full exact-head CI, DCO and
protected delivery remain required.

## Post-review correction

After Round 3, with the pull request ready and its complete CI running, the
implementing agent found its own defect while reading the financial form for
the next increment. The guide claimed that no stewardship year is stored and
justified omitting it, but the application already has a single campaign-year
rule, the Admin-configured year label or otherwise the start year, used by
Admin previews, page blocks and share labels. The specification asks for the
stewardship period and year, so the packet was incomplete and its recorded
rationale was false. No reviewer had raised it.

Auto-merge was disabled and the pull request returned to draft before it could
land. SQL now captures the raw year label, and the application applies the one
shared rule, so the rule is not duplicated in SQL. The header and the report
information both show the year, and the guide's false statement is replaced. A
fourth fresh install differed from the third only in the packet function body.
19 database-free, three PostgreSQL and 17 schema-contract cases passed locally.
This material correction returns to independent review as Round 4 without
resetting the completed rounds.
