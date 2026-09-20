# Financial stewardship detail review ledger

Review evidence for the [financial stewardship detail increment](stewardship-financial-report.md),
PR #76. Every round is dual-source: a Claude reviewer and the Codex reviewer
both completed. Severities are the raw reviewer values. Findings below the
tool's Medium/confidence cutoff are counted but listed only where they were
acted on or deliberately deferred.

The delivered branch squashes the review corrections into logical commits. The
complete commit-by-commit history, including every reviewed SHA below, is
retained on `pr/stewardship-financial-report-reviewed`, whose tree is identical
to the delivered head. That branch is review evidence only and is never merged.

## Round 1

Reviewed `7f92831`, the complete 1,710-line diff from main `1070fd28`. Both
sources completed. 25 raw findings, 18 from Claude and seven from Codex; five
validated, all Medium, of which two were raised independently by both sources;
no High or Critical. They reduce to three distinct defects. All accepted and
fixed.

- **Both: the giving proof was not bound to what SQL reads.** The application
  proved completeness for the current snapshot and handed SQL a bare Boolean,
  while SQL chose its own snapshot and configuration in a later READ COMMITTED
  statement. A promotion between the two would have summed money from a
  snapshot never proven for this window, breaking the rule that the proof can
  only withhold. For an archived campaign the two were different snapshots by
  construction, and a wrong comment claimed otherwise. The proof now names the
  snapshot and configuration it proved, the application proves the same snapshot
  SQL selects (an archived campaign's own pinned source), and SQL honors the
  proof only when both identities equal what it selected. Any mismatch withholds.
- **Both: SQL predicates and multi-row behavior were unproven.** Every case had
  one Family on the first page, so the fund mapping, the comparison window, the
  giving cutoff, the whole-result summary, ordering and paging could all have
  been deleted without failing a test. The reporting Family now has its own
  rows in an unmapped fund, before and after the window, and after the cutoff,
  each with a distinctive amount asserted absent, plus boundary rows on the last
  window day and the cutoff day asserted present. A backdated cutoff keeps that
  case independent of today's date. Three real Families, submitted through the
  real form at distinct campaign-clock times, now prove per-Family attribution,
  a proven complete zero, all six sort orders, positive cases for every filter
  dimension, disjoint ordered pages at a page size of two with an identical
  summary on each, a page past the end, and the complete unpaged mode. A
  51-Family dataset was not built: submissions are real by repository rule, and
  the page size became a bounded argument, so real boundaries are proven with
  three Families instead.
- **Codex: label wording was only half versioned.** The option template came
  from the configuration the Family answered, but its substitutions came from
  the current one, so a later year-label edit reworded a retained answer. Rows
  now carry their configuration identity and are worded by the Family form's own
  `option_labels` rule against that immutable configuration, once per
  configuration rather than once per row. A second copy of the substitution set
  is gone. The whole-result summary and the filter can only use current wording.

Acted on from the 18 below-cutoff Claude findings, all Low:

- The SQL share filter was only length-bounded while the application required a
  pattern. Share options are UUIDs, so both sides now require a canonical one.
- Range casts sat beside their own grammar guard in one condition, whose terms
  SQL may evaluate in any order, so a malformed bound could surface as a cast
  error echoing the value. Ranges are now compared in a second condition after
  the grammar has passed, with statement-level cases for both bounds present.
- The page size was a literal in SQL and a separate constant in Python. It is
  now a bounded argument the caller passes.
- A page past the end showed the count beside "No matching pledges." It now says
  the page is past the last match, and Previous returns to the real last page.
- Share methods were ordered by opaque identity. Rows, summary and filter now
  use the order the parish configured, with options no longer offered last and
  counted together rather than as repeated identical summary entries.
- Every `ValueError` while shaping data was reported as a filter error. Only the
  requester's own filters are now a 400; a later one is a 503.
- A refused filter reached a bare text page. It now reaches an accessible error
  page that explains the money format without echoing any value, as the sibling
  reports do.
- A function named `money` beside the `money` module, and an SQL constant
  shadowing the `money` type name, were renamed.
- The guide and a test comment said a zero pledge has no frequency. It may omit
  one but is allowed to carry one; the wording is corrected and the case tested.
- A campaign without the financial module is now tested to have no entry link
  and a denied report.

Not acted on: the re-resolved principal ending a report after a role change is
asserted only by the shared guard's own tests, not again here.

A fresh install after the corrections again differed from main only by the one
added function, now with a page-size argument, with no existing definition
changed, before the fingerprint was updated. Post-fix validation: five
database-free, six PostgreSQL, 17 schema-contract and nine browser cases passed
locally.
