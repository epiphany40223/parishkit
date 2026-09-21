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
