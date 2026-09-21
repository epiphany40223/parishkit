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

## Round 1, single-source under the second exemption

Reviewed `8fb7f211`, the complete diff from main `1070fd28`. The Claude reviewer
completed with 19 findings, three Medium and 16 Low. The Codex reviewer exited
without structured output, for the fourth consecutive attempt across the three
open PRs. At the time this was recorded as not a completed round. Later that
day the human confirmed Codex was out of quota and granted a
[second exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
through September 25, 2026, under which this completed Claude-only pass counts
as round 1. The findings were acted on before that decision.

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

## Round 2, single-source under the second exemption

Reviewed `d14a395a`, the complete diff from main `1070fd28`. The Claude reviewer
completed with nine findings, one Medium and eight Low; Codex exited without
output. Under the exemption this counts as round 2. Accepted and fixed:

- **Medium: the cost case could not detect a per-row actor lookup.** Its five
  hundred bulk entries carried no actor, so the page issued no actor read at
  all and the assertion was satisfied trivially. The entries now name five
  distinct actors, and the case asserts exactly one actor lookup by identifier
  set beside one bounded read of each log table.
- Low: the date-range case derives its day from a stored entry rather than the
  wall clock; dates are parsed once as dates; the audit action is named
  `SYSTEM_LOGS_VIEWED` after its stored value and the capability; a query
  string is refused with its own explanation rather than filter guidance, since
  the mistake is where the filters were sent; the guidance names the day
  bounds; and the guide's checkpoint is rewrapped.

Not acted on: the view repeats the delivery pages' capture, render, recheck and
audit shape rather than sharing one helper with them. Extracting it would change
unrelated production code in this increment; it is a candidate for the export
increment, which adds a second logs view.

Post-fix validation: four database-free, nine PostgreSQL and nine browser cases
passed locally.

## Round 3, single-source under the second exemption

Reviewed `e89bbd9a`, the complete diff from main `1070fd28`. The Claude reviewer
completed with 11 findings, one Medium and ten Low; Codex exited without
output. Under the exemption this counts as round 3. Accepted and fixed:

- **Medium: the query-string refusal was tested only by its status.** The
  round-2 correction gave it its own explanation, but over HTTP nothing checked
  that the explanation appears, that the filter guidance does not, or that the
  submitted identifier is not echoed. The case now asserts all three, as the
  refused filter values already did.
- Low: the error helper takes an explicit `query_string` flag instead of a
  tri-state `guidance` value; the validated days are converted once by a
  `days` property, as the cursor already was; the levels are one ordered
  source, `LEVELS` being derived from the labels; the page's introduction
  says an actor's address is looked up for display and is not part of an
  entry, rather than that entries never hold addresses; the browser fixtures
  render the query-string refusal too, and the browser case asserts each of
  the three error states shows only its own text; the cost case asserts each
  of the five actors' addresses appears ten times, since "actor" also matched
  the form; and the guide's checkpoint is rewrapped.

Left as is: the PostgreSQL helper filters audit rows by the literal stored
type `system_logs_viewed` rather than the enum member, because the positive
case asserts one such row and would fail loudly if the stored value changed,
which is the pin intended; and the `log-level` and `log-kind` classes carry
no stylesheet rules, since this increment adds none and they name what the
cell is, for the tests and for a later stylesheet alike.

Post-fix validation: four database-free, nine PostgreSQL and nine browser cases
passed locally.
