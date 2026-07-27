# Campaign-domain implementation plan

This plan implements the cross-cutting contracts in the
[root stewardship specification](../../specs/stewardship/spec.md). Subsystem
plans own their concrete models and views; this plan owns shared terminology,
policy behavior, lifecycle invariants, and end-to-end coherence.

## Outcomes

- One parish deployment with one current campaign through draft, scheduled,
  active, closed, and archive preparation.
- Explicit, audited lifecycle transitions and a post-archive return to Testing
  before a successor can be created.
- One authorization vocabulary and capability matrix used consistently by
  services, templates, reports, and tests.
- Parish-local-day/date behavior with UTC persistence and deterministic DST
  handling.
- Shared presentation behavior for responsive UI, numbers, percentages,
  currency, browser-local timestamps, and historical campaign selection.

## Work packages

### DOM-01: Domain vocabulary and decision records

1. Create a short architecture-decision index under the Stewardship package for
   irreversible choices: one-current-campaign sequencing, Testing/Production
   separation, ParishSoft source-of-truth overlays, low-sensitivity manual
   codes, immutable submissions, and no public API.
2. Implement shared enums/value objects for lifecycle, system mode, modules,
   roles, local-day intervals, money, percentages, and source/proposal value
   terminology. Keep database-specific enums in the data app but expose one
   canonical Python vocabulary.
3. Add pure tests proving serialization stability and rejecting unknown states.
4. Document which interfaces are internal implementation details and which are
   persisted contracts that require migrations when changed.

Dependencies: ARC-01. This package must complete before models or URLs encode
state names independently.

### DOM-02: Campaign interval and lifecycle policy

1. Implement pure policy functions that resolve parish-local start/end dates to
   the authoritative half-open UTC interval, including gaps and folds.
2. Implement a lifecycle transition registry describing allowed source/target
   states, required guards, actor type, reauthentication, and audit action.
3. Centralize predicates for portal access, scheduled work admission, structural
   setting locks, reopen eligibility, archive eligibility, return to Testing,
   and successor creation.
4. Ensure date gates deny access/work at the exact boundary even if persisted
   lifecycle state temporarily lags the scheduler.
5. Add exhaustive transition-table, DST, race-precondition, and property-style
   invariant tests.

Dependencies: DOM-01, DAT-01, DAT-02. Concrete transactions are delivered by
DAT-02, BG-02, and ADM-04 through ADM-06.

### DOM-03: Authorization capability policy

1. Encode the specification capability matrix as named policy functions rather
   than scattered role comparisons.
2. Implement Administrator role implication, explicit Staff workflow-write
   exceptions, Ministry-assignment row scope, and Family-own-record scope.
3. Define report column-level privacy helpers so Ministry leaders cannot obtain
   financial, unrelated Family, or manual-code data.
4. Make authorization functions usable from HTML views, internal partials,
   background-job creation/claim, export download, and audit detail.
5. Build parameterized tests covering every role/capability/object-scope tuple,
   including immediate role and assignment revocation.

Dependencies: ARC-04 and DAT-05. Portal and report work cannot pass its review
gate until these policies are used server-side.

### DOM-04: Shared presentation and client contracts

1. Establish the responsive design-system primitives, parish branding slots,
   accessible navigation, banners, forms, tables, empty/error states, and task
   progress components.
2. Add formatting helpers for US grouping, USD, percentages, `X out of Y`, UTC
   to browser-local timestamps, and parish-local dates.
3. Implement localization-ready strings without claiming a translated UI.
4. Establish supported-browser progressive-enhancement behavior and keyboard/
   screen-reader expectations used by both portals.
5. Add unit tests for formatters and a small accessibility/browser component
   smoke suite before feature pages multiply.

Dependencies: ARC-02 and OPS-09. ADM-03 and FAM-02 consume these components.

### DOM-05: Cross-domain acceptance harness

1. Create factories/builders for a parish, campaigns in every lifecycle state,
   source snapshots, role combinations, Families, submissions, outbox work, and
   historical campaigns.
2. Add scenario helpers that advance authoritative time without using wall-clock
   sleeps and that exercise parish/browser timezone differences.
3. Map each acceptance scenario in the
   [operations specification](../../specs/stewardship/operations/spec.md#acceptance-scenarios)
   to one or more executable tests and owning subsystem work packages.
4. Add a traceability check or maintained table ensuring every specification
   section and master-plan work package has an acceptance owner.
5. Run the final scenarios in Phase 7 of the overall plan; do not defer creation
   of the harness itself until that phase.

Dependencies: initial factories begin after DAT-01 and grow throughout the
project. Final completion depends on all subsystem plans.

## Review handoffs

- At Review Gate 1, review DOM-01 through DOM-03 for inconsistent state or role
  logic before feature views are built.
- At Review Gate 1, review DOM-04's shared component/formatting foundation; at
  Review Gate 2, recheck it and the first DOM-05 vertical scenarios on mobile
  and desktop.
- At Review Gate 5, close every traceability entry and run all lifecycle and
  cross-timezone scenarios.

The shared [review-gate protocol](overall.md#review-gate-protocol) applies.

## Completion criteria

- No subsystem defines a conflicting lifecycle, role, local-day, or formatting
  rule.
- Policy behavior is independently unit tested and enforced again at persistent
  transaction boundaries.
- The complete acceptance suite covers the root specification's end-to-end
  sequence and campaign history rules.
