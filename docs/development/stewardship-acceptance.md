# Stewardship acceptance ownership

[Task sequence](../tasks/stewardship/overall.md) ·
[DOM-05 tasks](../tasks/stewardship/campaign-domain.md#dom-05-cross-domain-acceptance-harness) ·
[Normative scenarios](../specs/stewardship/operations/spec.md#acceptance-scenarios)

The [ownership manifest](stewardship-acceptance.yaml) maps every normative
specification section (including nested sections), all planned work packages,
and each numbered acceptance scenario to responsible implementation packages.
It references headings rather than copying requirements. CI fails if a heading,
scenario, or package gains or loses coverage without updating this mapping.

Each acceptance scenario starts `planned` with no executable evidence. As
vertical slices land, add exact pytest node IDs in `tests` and set the scenario
to `partial`. Mark `complete` only after every case in that normative numbered
scenario passes its required integration/acceptance suite. A unit test of a
helper is not evidence that an entire end-to-end scenario passes. Task and gate
approval still live in their owning checklists, not this manifest.

The manifest checks resolve referenced test files/functions, catch stale or
unknown owners, and ensure every planned package appears somewhere. This is
traceability enforcement, not a replacement for running the referenced tests
or independently reviewing their assertions.

## Deterministic time

Policies accept the `Clock` protocol from `parishkit.stewardship.clock`. The
process default explicitly reads UTC. Tests can inject `ManualClock` from
`parishkit.stewardship.testing`, supplying an aware instant and advancing a
nonnegative elapsed `timedelta` without sleeping or globally patching time.
Advancement occurs in UTC so DST gaps/folds do not change elapsed-time meaning.

Capture one instant for a decision, then project it separately with
`in_timezone(instant, parish_zone)` and `in_timezone(instant, browser_zone)`.
The parish's local date determines reporting days; a browser timezone only
changes presentation. These helpers do not resolve campaign boundaries or
authorize lifecycle transitions; DOM-02 owns those rules. Manual clocks are
explicit test dependencies, never deployment-configurable time overrides.

DAT-01 will introduce database-backed factories and the transaction-authoritative
clock integration needed for concurrency. The current harness is deliberately
database-free. All ten full scenarios remain planned until their vertical
slices exist, and final acceptance is required in Phase 7.
