# Calendar-independent closed weekly skip test

This correction begins at verified main `c08fd51d` after PR #71's
[protected delivery](stewardship-ministry-exports.md#protected-delivery). It
changes one database test and no application behavior. It is separate from the
[Ministry follow-up increment](stewardship-ministry-followup.md) because it is
an unrelated defect that blocks every ready candidate.

## Defect

Post-merge main CI `35514478571` failed PostgreSQL partitions 8 and 11, both in
`test_closed_receipt_and_weekly_skips_are_durable_not_provider_acceptance`,
with the same assertion. PR #71 does not touch that area, the case passed in
the September 19 runs, and it reproduced locally on unmodified main.

The shared `scheduled` fixture starts its campaign two days after the real
date, while the weekly digest has a fixed configured weekday. The case captures
its weekly report at start plus nine days, so the number of weekly slots that
have elapsed by then depends on the day the suite runs. When an earlier slot
has elapsed, weekly planning correctly coalesces that missed slot into the
selected occurrence and records a `coalesced` `ScheduleFulfillment`. The case
asserted that the occurrence had no fulfillment at all.

Offsetting the fixture start on unmodified main gave Monday pass, Tuesday fail,
Wednesday fail and Thursday pass. Under a failing alignment the only record
present was `('coalesced', '2026-09-23')`, the earlier slot. Delivery behavior
is correct; the assertion was broader than its intent.

## Correction

The case now asserts its intent directly. It snapshots every fulfillment before
both post-close cancels and requires the set to be unchanged afterwards, so a
cancel that wrongly wrote coverage fails on every calendar rather than being
excused. It separately requires that what capture recorded for the occurrence
is exactly one `coalesced` record per predecessor occurrence that it replaced,
which is two empty sets on single-slot calendars.

The fixture stays relative to the real clock because it must describe a
genuinely future campaign. Both resulting calendar shapes are ones production
campaigns produce, so neither is pinned away.

## Validation

Both parametrizations passed for all seven start weekdays, 14 of 14, after each
correction; about 58 seconds per pair on the disposable local PostgreSQL 18.6
cluster. Ruff lint and format are clean. The temporary fixture offsets used to
map weekdays were restored after every probe and are not part of this change.
No retained database was deleted.

## Review rounds

Every round is single-source under the
[September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
In each, the Codex reviewer started a thread and aborted on its first turn with
`Your workspace is out of credits`, producing no findings. Round 1's first
attempt predates the exemption and was initially held as not completed.

### Round 1

Reviewed `ed9553da`, the complete diff from `c08fd51d`. Raw Claude severities:
two Medium, three Low below the reporting cutoff.

- Medium, accepted and fixed: permitting any earlier-slot `coalesced` record
  was unconditional. That record is written at capture, before the cancel, so a
  cancel that itself wrote one would pass on every calendar. The reviewer
  proposed the before/after snapshot, which was adopted.
- Medium, rejected with evidence: pin the weekday to remove the variability at
  its source. Pinning would freeze one calendar shape and drop the coalescing
  shape real campaigns produce, and the shared fixture must remain genuinely
  future. With the accepted fix the assertion is strict on every alignment, so
  what remains is coverage variety rather than nondeterministic failure.

Post-fix validation passed 14 of 14.

### Round 2

Reviewed `e68fea67`, the complete diff. No validated finding; four Low notes.
The reviewer independently confirmed that the snapshot cannot excuse a cancel
that writes coverage, because `ScheduleFulfillment` is immutable.

- Low, adopted: verify the tolerated records positively against the coalesced
  predecessor occurrences rather than merely permitting them.
- Low, adopted through the same change: stop relying on lexical ordering of
  slot keys, which also hold non-date keys.
- Low, adopted: snapshot before the receipt cancel too, covering both cancels.
- Low, rejected: residual calendar dependence, for the Round 1 rationale.

Post-fix validation passed 14 of 14, producing `660b6037`.

### Round 3

Reviewed `660b6037`, focused on the Round 2 corrections with the complete
diff as context. No validated finding; no High, Critical or Medium.

The reviewer verified the exact-equality assertion against weekly planning,
the fulfillment insert guard and the post-close view: planning writes each
coalesced occurrence and its fulfillment together, the insert guard already
binds every coalesced fulfillment to a coalesced original replaced by that
occurrence, and sorted lists preserve multiplicity. It is deterministic on
both calendar shapes and does not excuse a cancel that writes coverage. Its
independent calendar analysis matches the measured weekday map.

- Low, deferred with rationale: the non-empty coalescing comparison runs only
  on the two real weekdays that produce a predecessor, so a regression limited
  to that branch would surface on those days. This is the residual variability
  already weighed in Round 1. The assertion is exact on every day it runs, and
  a dedicated deterministic coalescing case belongs with the weekly planning
  owner's tests rather than this delivery-closed case.

No correction was needed, so `660b6037` is the reviewed test content. The exit
criteria are met: three completed rounds, no validated High or Critical finding
in the final round, no unresolved accepted Medium-or-higher finding, and
passing post-fix validation. Full exact-head CI, DCO and protected delivery
remain required.
