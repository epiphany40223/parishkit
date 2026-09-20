# System logs review ledger

Review evidence for the [system logs increment](stewardship-admin-logs.md),
PR #78. Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on or
deliberately left.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-admin-logs-reviewed` with a tree identical to the delivered
head. That branch is review evidence only and is never merged. Until it exists
the reviewed SHAs are on the PR branch itself.

## Round 1 attempt: not a completed round

Reviewed `8fb7f211`, the complete diff from main `1070fd28`. The Claude reviewer
completed with 19 findings, three Medium and 16 Low. The Codex reviewer exited
without structured output, for the fourth consecutive attempt across PRs #76,
#77 and #78, so this is **not** a completed dual-source round and is not counted
toward the required three. The cause is unverified: the `codex` command is
outside this session's shell allowlist, which was deliberately not widened or
bypassed. The
[September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
had ended and was not reused; restoring Codex or granting a new exemption is the
human's decision. The Claude findings were acted on meanwhile.

All three Medium findings were accepted and fixed.

- **A far-future date overflowed to an unhandled error.** The grammar accepted
  any canonical day, and advancing the last representable day to its exclusive
  upper bound raises an arithmetic error no handler caught. Days are now bounded
  to 2020 through 2999 in the grammar, with the refusal tested there and over
  HTTP.
- **The actor filter was unusable for the actors most searched.** A resolved
  actor showed only an address, while the filter takes an identifier the page
  never displayed. The identifier is now shown beneath the address.
- **Cursor ties and a cross-table cursor were unproven in the database.** Only
  the pure merge covered them. A case now walks a cursor across both tables
  through thirty entries sharing one instant and asserts each appears exactly
  once in one total order, which also proves PostgreSQL and Python order
  identifiers alike.

Acted on from the 16 Low findings: the detail whitelist was by shape, so an
unreviewed flat text field would have rendered, and is now closed over the
reviewed context field names with bounded text; the post-render restore check
is tested; every PostgreSQL request now runs under the web role, as the guide
claims; filter guidance appears only when a filter was the problem, not for a
denied reader or an outage; times use the shared UTC instant filter; one
production context builder serves the view and the browser fixtures; the
duplicated identifier pattern is gone, since detail keys are now matched against
the reviewed field names; the cost case asserts one read
of each log table and no per-row actor lookup; a helper moved beside the others;
and the guide states how far before/after detail is met.

Left as is, with the guide saying so where it matters: no index yet serves
ordering by time; the two sources are read by separate statements, so an entry
committed between them can be missed by a reader already past that instant; the
shared local-time enhancement shows minutes; HEAD stays refused rather than
becoming an audited view; and the access-lost case substitutes the recheck
rather than performing a real demotion mid-request.

Post-fix validation: four database-free, nine PostgreSQL, 16 navigation and nine
browser cases passed locally.
