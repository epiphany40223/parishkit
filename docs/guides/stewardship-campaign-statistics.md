# Campaign statistics calculation service

## Scope and dependencies

This Phase 4 increment starts from PR #44's verified merge `80754a49`, after all
24 exact-head checks plus DCO and all 24 protected merge-group checks passed.
It implements the remaining statistics prerequisites of
[RPT-02](../plans/stewardship/reports.md#rpt-02-population-and-calculation-library)
before BG-07. Follow the [population and calculation contract](../specs/stewardship/reports/spec.md#population-and-calculation-rules)
and [campaign statistics](../specs/stewardship/reports/spec.md#campaign-statistics).
Full report UI, age/Ministry-specific calculations, concrete digest ownership,
and Gate 3 remain open. No new external route, mail dispatch or schema upgrade
is introduced by this service increment.

## Observation and calculation boundary

The authorized reader holds the existing campaign guard through consumption and
rechecks current Admin/Staff policy before handing off data. A single PostgreSQL
statement captures configuration, promoted source membership, live submission
watermark/latest annual pledge, and unresolved Family/organization-scoped mail
refusals. Multiple READ COMMITTED queries cannot safely replace that capture.
Statement MVCC protects detachment against concurrent source compaction; it does
not create durable asynchronous ownership.

Only calculation inputs leave the database: source flags and relationships,
eligible active-head email addresses, annual pledge values and comparison pledge
records within the mapped comparison funds/period for the active/ever-eligible
report population. Refusals must match an
eligible head's current address in that Family/organization; unrelated historical
refusals never enter the document. A separate numeric whole-snapshot pledge count
preserves the completeness proof without copying unrelated household finances.
Names, addresses, phones, census answers, codes, link tokens and additional text
are not part of the detached document. Canonical JSON text prevents shallow
dataclass immutability from exposing mutable nested inputs. Its representation
omits private data; decoding returns a fresh copy.

Giving normalization validates fund identities and effective dates before
staging, emitting canonical integer strings and ISO dates in
`source/giving.py`. The SQL comparison projection relies on that promoted-source
contract; it does not admit arbitrary external financial payloads. Valid head
addresses are projected once for refusal matching. Invalid source email text
and heads of email-ineligible Families are never detached.

The pure calculation service distinguishes eligible email from deliverable
email and supplies their exact complement. Current active Families are the
promoted Portal-eligible set. Include inactive adds a separately labeled subtotal
of formerly eligible Families outside that set, never arbitrary never-eligible
source households and never a larger active denominator. Only the latest live
response contributes the annual pledge; its existence counts that Family once.

Money uses the existing exact-cent primitives and mapped giving-period/coverage
proof. Missing coverage remains Unavailable; complete empty coverage is zero.
A formerly eligible Family removed from the source has no observed comparison,
so its inactive comparison subtotal is unavailable rather than an invented zero.
An unfinished draft financial mapping leaves known population counts available
while its financial cards remain unavailable, distinct from disabled financial
stewardship. Live financial configuration cannot change its enabled modules;
structural locking precedes live submissions. An unexpectedly missing live
annual pledge stays unavailable, matching the participation calculation policy.
A Family-only source delta retains the earlier giving observation date rather
than presenting its newer census watermark as newer financial information.
Archived selection uses the campaign's retained source reference instead of a
successor's financial window; missing/compacted membership remains unavailable.

BG-07 must transactionally retain the exact observation and required source
protection with its concrete digest occurrence. A frozen Python value does not
establish durable ownership. Chart day boundaries and statistics observation
instants remain separately labeled inputs; this service does not rewrite chart
facts or pretend the current cards and a previous-day graph share one cutoff.

## Acceptance checkpoints

Initial implementation passes 19 PostgreSQL tests in 50.00 seconds, including
the 5,000-Family single-statement capture/calculation budget, actual-role reads,
archived giving, real live/Test submissions and a concurrent refusal. The 59
focused calculation/grant tests pass in 0.36 seconds. Repository Ruff/formatting
and changed Markdown checks pass. Full regression and independent review follow.
Required evidence includes
actual-role reads and denied leaders/revoked users; source, response and refusal
coherence; missing versus observed-empty source; active/inactive denominators;
latest live money and Test exclusion; financial mapping/as-of; safe detached
projections; exact input round trips; and reference-population bounded queries.

Complete repository checks, three successful dual-source review/fix rounds,
exact-head CI/DCO and all protected merge-group checks before delivery under
the [automated cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
Earlier passing local checks are not a substitute for those delivery gates.

## First independent review and regression evidence

At `482444e`, the baseline passes 6,085 tests in 75.33 seconds (4,218 profile
skips and two existing Valkey-client warnings). All 3,374 PostgreSQL tests pass
across eight isolated partitions, with complete same-tree accounting and
93.99% line / 85.16% branch coverage. Tracked Markdown and Django model-state
drift checks pass. This increment adds no schema migration.

Round 1, `20260916-225956-138056`, reviewed the full `80754a49..482444e` change.
Both sources completed successfully, with no failed/degraded reviewers or
mismatches. Claude reported two Medium and six Low findings; Codex reported
three Medium findings. All raw findings, including those below the confidence
cutoff, were examined:

1. The three Codex Medium findings are accepted: narrow raw pledge, refusal and
   head-address projections to the populations that use them, with negative
   privacy regression tests and a separate whole-snapshot count proof.
2. Claude's Medium successor-while-closed scenario is rejected: the single-
   current-campaign contract and configuration admission prohibit a successor
   until archive and Return to Testing. Archived source binding is already tested.
3. Claude's Medium enabling-financial-after-live-submission scenario is rejected:
   module/fund structural locking precedes Production submissions. Do not weaken
   the explicit missing-money rule to accommodate a forbidden transition.
4. The Low removed-Family comparison issue is accepted; missing observation no
   longer becomes an observed zero in the inactive subtotal.
5. The Low repeated JSON parsing and private-helper coupling issues are accepted:
   materialized payload projections parse once, and the existing exact Family
   sum has an explicit shared public name.
6. The Low request to remove point-in-time evidence is declined: this task-linked
   historical ledger is required by the controlling delivery cycle, not a promise
   of permanently unchanged test counts or timings.
7. The Low request to loosen/isolate the performance test is declined: the
   existing two-second budget and 20-sample nearest-rank p95 (index 18) are
   deliberate acceptance checks. The reference test passes; limits are unchanged.
8. The Low mixed Test/live gap is addressed with an actual transition test:
   higher Testing sequences are excluded, required cleanup removes them, and the
   first live version starts its own count. No invalid mixed state is fabricated
   by bypassing cleanup/SQL guards. Refusal projection tests also cover unrelated
   inactive Families and late evidence for an obsolete address, preserving the
   explicit source-organization predicate and existing identity guards.

Round-1 corrections pass 94 focused calculation/financial/grant tests in 0.62
seconds. A broader 59-test PostgreSQL run passes in 116.00 seconds, including
the existing financial response workflows after the shared helper rename.
The final projection/mapping corrections pass all 28 statistics/source database
tests in 53.68 seconds and the full baseline passes 6,090 tests in 62.37 seconds
(4,224 profile skips and the same two existing warnings). Repository
Ruff/formatting, changed Markdown and whitespace checks pass. Round 1 is complete;
the remaining independent rounds and protected delivery still follow.

## Second independent review and corrections

Round 2, `20260916-232414-8a4dc4`, reviewed `482444e..bdf505a` with surrounding
context. Both reviewers completed successfully with no degradation, failure or
verdict mismatch. Its six raw Low findings (five Claude, one Codex), including
all below the finalizer cutoff, received these dispositions:

1. Codex's invalid-email projection finding is accepted: detach only valid email
   entries from email-eligible active heads. Actual-source tests cover both an
   invalid-only head and an invalid companion to a usable address.
2. Claude's malformed giving exclusion concern is already handled by the source
   normalizer's canonical fund/date contract, now explicitly documented above.
   The statistics capture reads promoted normalized source, not external records.
3. The detached-list-length and unmapped-fund test gaps are accepted. New tests
   independently exercise the count guard and an in-period, in-population pledge
   outside the selected fund mapping. Its fixture includes the referenced fund
   rather than bypassing source relationship validation.
4. The repeated refusal projection concern is addressed with a materialized
   distinct Family/address relation; email arrays are expanded once, not within
   every refusal's correlated membership predicate. Existing actual refusal and
   5,000-Family single-query timing regressions pass without a relaxed budget.
5. The removed-Family comparison comment request is accepted, explaining why a
   missing Family observation differs from a missing pledge row.

Round-2 corrections pass 37 focused unit tests in 0.30 seconds, all 31
statistics/source PostgreSQL tests in 58.67 seconds and the complete baseline
(6,091 passed, 4,227 profile skips, two existing warnings) in 60.90 seconds.
Repository Ruff/formatting, guide Markdown and whitespace checks pass. A third
independent round and final-head protected delivery remain required.
