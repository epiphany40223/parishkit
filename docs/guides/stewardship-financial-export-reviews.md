# Financial export review ledger

Review evidence for the [financial export increment](stewardship-financial-exports.md).
Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on or
deliberately left.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-financial-export-reviewed` with a tree identical to the
delivered head. That branch is review evidence only and is never merged. Until
it exists the reviewed SHAs are on the PR branch itself.

## Round 1, dual-source

Reviewed `f0ed37f3`, the complete diff from main `f3bdac13`. Both sources
completed: Codex answered despite its quota, with three validated Medium
findings, all in the document builder; Claude returned nine findings below the
cutoff. All three Medium findings were accepted and fixed.

- **A Family's share wording could exceed a spreadsheet cell.** A configuration
  may offer a hundred options and each Other text two thousand characters, and
  openpyxl truncates a cell beyond 32,767 characters silently. Whole entries
  now continue in further rows for the same Family, marked as continued and
  carrying only the Family, its DUID and the response reference, and a case
  recovers every character from a workbook round trip.
- **A long share label as a metadata key broke the PDF.** The shared renderer
  wraps a value to the width left after its key, and the default online-giving
  label expands past that width for an ordinary parish name, so the PDF raised
  on its own metadata. Frequency and share counts are now wrapped values, one
  label per line, under fixed keys, with a 150-character label rendered in the
  test.
- **The giving read's own time was missing.** A Family-only source refresh
  keeps an older giving read, so a file could show a recent source time beside
  older money. The giving read's observation time is now stated as its own
  metadata line, apart from the source promotion time, or as unavailable.

Acted on from the nine Low findings: the service refreshes only the capture's
header after the insert, never the document with every Family's money, and the
case asserts it; the immutability case runs as the schema owner so the
trigger, not a missing grant, is what refuses, and the worker case says that
it is the missing grant it proves; the browser fixture renders the gated state
and the case checks every export control is disabled; the replay check compares
one tuple with a tolerant lookup of the previous filters and raises once; the
task map's earlier sentence about the exports no longer contradicts the
paragraph that records them; the export sentence uses a pluralized block
translation; regeneration of a retained capture no longer requires the
financial module to still be on. Left as is: the request and publication
guards' financial branches are exercised on the happy path only, like their
sibling branches, whose refusal paths the shared guard suites cover on the
same code.

Post-fix validation: seven database-free, two PostgreSQL and nine browser cases
passed locally.

## Round 2, single-source under the second exemption

Reviewed `aa3475a0`, the complete diff from main `f3bdac13`. The Claude
reviewer completed with seven findings, one Medium and six Low; Codex exited
without output. Under the exemption this counts as round 2. Accepted and
fixed:

- **Medium: the cell limit ignored the spreadsheet's own escaping.** The
  writer doubles every backslash, and an Other text may be nothing but
  backslashes, so a cell packed to the raw limit could still be truncated. The
  limit is now half the spreadsheet maximum, so the worst case fits, and the
  round-trip case's Other text is backslashes.
- Low: the campaign and its giving proof are loaded only for a fresh capture,
  after the replay lookup, so a replay recomputes nothing and regeneration
  loads nothing it does not use; the Ministry leader's SQL denial matches the
  capture trigger's own refusal; the gated browser case checks the timezone
  control too; the continuation rows are rendered as CSV and PDF as well; a
  continuation row leaves the status column empty rather than writing a marker
  a filter on that column would see.

Left as is: the guide's link to PR #76's protected-delivery receipt resolves
once this branch is rebased onto the merged PR #77, which carried that receipt;
at the reviewed base the heading does not yet exist.

Post-fix validation: seven database-free, two PostgreSQL and nine browser cases
passed locally.

## Round 3, single-source under the second exemption

Reviewed `9ffa0688`, the complete diff from main `f3bdac13`. The Claude
reviewer completed with six findings, one Medium and five Low; Codex exited
without output. Under the exemption this counts as round 3. Accepted and
fixed:

- **Medium: the halved limit still assumed the wrong worst case.** The
  spreadsheet writer also spells a character XML cannot carry as an escape up
  to six characters long, and the Family form accepts the noncharacters that
  need it, so a cell packed to half the maximum could still expand past it.
  Cells are now packed by the length the writer really stores, escapes and
  the continuation prefix included, against the true maximum, and the
  round-trip case's Other text is noncharacters and backslashes.
- Low: a replay case now changes what the proof would compute to and asserts
  the same export with the original proof; the continuation rows' CSV layout
  is parsed and asserted, not merely produced; the frequency cell drops a
  wrapper that suggested a lazy value; the task map's retained sentence about
  the financial detail increment defers to its receipt rather than calling
  its delivery pending.

Left as is: neither PostgreSQL fixture turns the financial module off, so a
fresh capture on a campaign without the module and regeneration after the
module is switched off are not exercised here; the interactive report's own
suite proves the module-off denial that the fresh capture shares, and the
regeneration path renders a retained capture with no read of the module.

Post-fix validation: seven database-free, two PostgreSQL and nine browser cases
passed locally.
