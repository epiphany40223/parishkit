# Financial stewardship detail review ledger

Review evidence for the [financial stewardship detail increment](stewardship-financial-report.md),
PR #76. A round is dual-source, a Claude reviewer and the Codex reviewer both
completing, unless a recorded exemption admits a completed single-source pass.
Round 1 was dual-source; rounds 2 and 3 were single-source under the human's
second September 20, 2026 exemption, granted after Codex ran out of quota
again. Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on
or deliberately deferred.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-financial-report-reviewed` with a tree identical to the
delivered head. That branch is review evidence only and is never merged. The
[protected delivery receipt](stewardship-financial-report.md#checkpoint) records
it once it exists; until then the reviewed SHAs are on the PR branch itself.

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

Claude's 18 findings were three Medium and 15 Low. Acted on from those 15
below-cutoff findings:

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

## Round 2, single-source under the second exemption

Reviewed `f92b5f5`, the 1,737-line round-1 correction delta from `7f92831`. The
Claude reviewer completed with 12 findings, one Medium and 11 Low. The Codex
reviewer exited with status 1 after 258 seconds and produced no structured
output. At the time this was recorded as not a completed round, because the
first September 20 exemption had ended when Codex returned. Later that day the
human confirmed Codex was out of quota again and granted the
[second exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle),
under which this completed Claude-only pass counts as round 2. The findings were
acted on before that decision.

- **Claude, Medium: the archived-campaign proof was untested.** Round 1's fix
  for archived campaigns could have been deleted without failing a test. A new
  case delivers the live response's confirmation through the real mail-dispatch
  owner, closes and archives the campaign, promotes a successor snapshot, and
  asserts under the restricted web role that the proof names the pinned
  snapshot, that its money is shown, and that a proof of the successor withholds.

Acted on from the 11 Low findings:

- Substring scans for excluded amounts covered the whole serialized page,
  including random identities and wall-clock microseconds, where a digit run
  could match by chance about once in 150 runs. They now cover only the money
  the page displays.
- Well-formed but inverted date intervals were refused only by the
  application's parser. Both are now refused at the statement too.
- The pledge-range cast still sat beside its own emptiness guard, against the
  rule the comment stated. An absent bound now becomes NULL, so no term depends
  on another to be safe.
- The versioned-wording lookup was proven only with a stub. A real year-label
  change after a Family answered now proves the row keeps its wording while the
  summary and filter use the new one.
- Merging summary counts by rendered label could merge two offered options
  worded alike. Counts now stay separate per offered identity, and only options
  no longer offered are counted together.
- The shared guard answers unavailable inputs itself, so the new 503 recovery
  page was bypassed for the likeliest case. The view now substitutes it, as the
  directory report does, with an HTTP-level case.
- The proof issued a snapshot query before noticing it had no snapshot.
- A second copy of the page size remained as an SQL default. Neither SQL
  argument has a default now, the application requires the size, and the view
  passes the same constant that drives its paging arithmetic.
- The ledger miscounted Claude's below-cutoff findings and described the
  retained evidence branch as already existing. Both are corrected.

Deferred: share wording substitutes the parish name, which is always the current
one rather than versioned with the row. The guide now says so.

A fresh install again differed from main only by the one added function, now
with two required paging arguments. Post-fix validation: five database-free,
eight PostgreSQL and 17 schema-contract cases passed locally.

## Round 3, single-source under the second exemption

Reviewed `419a0d0`, the 2,031-line correction delta from `7f92831`. The Claude
reviewer completed with 11 findings, one Medium and ten Low. The Codex reviewer
again exited without structured output; the human later confirmed it was out of
quota. Under the second exemption this completed Claude-only pass counts as
round 3. The findings were acted on before that decision.

- **Claude, Medium: the `ValueError` to 503 mapping was untested.** The only
  HTTP 503 case raised `ReadUnavailable`, which the shared guard answers before
  the view's own handler. The case now also raises a `ValueError` carrying a
  Family name and asserts the recovery page, the retry header and that the text
  is not echoed.

Acted on from the ten Low findings:

- The new proof guard repeated the pattern forbidden for ranges: a container
  operator beside its own type guard in one condition, as the original filter
  guard also did. Validation is now ordered statements, each relying only on
  what an earlier one established, and scalars or arrays where an object belongs
  are refused at the statement with the closed refusal.
- Filters were judged before the campaign was admitted, so a malformed filter
  for an unknown campaign produced a filter error whose link led only to a
  denial. The campaign is admitted first.
- One statement used `configuration_id` for both the campaign configuration
  row and a submission's applied version. The former is renamed.
- The remaining amount scans could not fail independently of the exact totals,
  because a wrongly admitted row would be summed, not displayed. They are gone.
- A test local shadowed the imported Family `login` helper; the shared harness
  rebuilt a set per iteration and did not say why a Family group is needed; a
  parameter named `counts` also received share text; two guide paragraphs were
  not rewrapped.

Not acted on, with reasons:

- Mapping `LookupError` and `TypeError` to the recovery page as well. That would
  turn a programming error into a quiet page; an unmapped error stays a logged
  server error. The guide's wording was narrowed to say only `ValueError`.
- Sharing one error-response helper with the directory report. It would change
  unrelated production code in this increment.
- A malformed filter for a campaign without the financial module still reports
  the filter first, because only the projection knows the module is disabled.

A fresh install again differed from main only by the one added function.
Post-fix validation: five database-free, eight PostgreSQL, three shared-harness
and 17 schema-contract cases passed locally.

## Round 4 correction check, single-source under the second exemption

Reviewed `e6d0d020`, the correction delta from `419a0d0`: the ordered SQL
validation, admission before filter parsing, the renamed configuration key,
the recovery-page routes and their tests. Claude only, under the exemption;
Codex was out of quota. Seven findings, one Medium and six Low. Accepted and
fixed:

- **Medium: the shaping `ValueError` route was a quiet page.** It returned the
  503 recovery page and recorded only a failed audit outcome, so a persistent
  defect that every retry reproduces would have stayed invisible to operators,
  the very outcome the round-3 reasoning rejected for other errors. The route
  now records an operational failure event before the page.
- Low: JSON null where an object belongs is refused at the statement, with
  cases for a null document and null filters; the one-Family test's docstring
  and comment describe the exact-total proof rather than a decoy scan that no
  longer exists; the ledger's opening rule names the exemption; and the guide's
  statement order names the page bounds with the object shapes.

Not acted on: threading a `bad` flag through five ordered statements is heavier
than raising in each, but it keeps one refusal site; and a malformed filter for
a campaign that exists without the module still answers 400 before the
projection's denial, as the round-3 ledger already records.

Post-fix validation: five database-free and eight PostgreSQL cases passed
locally, with the observability and build contracts.
