# Multi-Ministry packet review ledger

Review evidence for the [multi-Ministry packet increment](stewardship-ministry-packets.md),
PR #75. The Codex reviewer returned during this PR: its workspace had credits
again and its findings were folded into Round 1. These rounds are therefore
dual-source, and the [September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
no longer applies, by its own terms. Severities are the raw reviewer values.
Findings below the tool's Medium/confidence cutoff are counted but listed only
where they were acted on.

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
  chairs pass now joins and groups on them instead of re-parsing JSON.
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
body. Post-fix validation: 18 database-free, three PostgreSQL and 15 browser
cases passed locally.
