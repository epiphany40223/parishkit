# Stewardship CI bootstrap reuse

Continue the requested [test-efficiency work](stewardship-test-efficiency.md)
from [PR #53's protected delivery](stewardship-periodic-health.md#protected-delivery).
Branch `pr/stewardship-ci-bootstrap-reuse` starts at verified main `113fcd0f`.
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle):
three successful dual-source review/fix rounds and exact-head CI/DCO precede
protected merge. This maintenance increment completes no new BG-10 feature.

## Measured reason and bounded change

PR #53's twelve PostgreSQL partitions execute in under twelve minutes each, but
the full workflow takes 16m10s. Twenty jobs run while five initially wait for
capacity. The matrix includes eight independent operational jobs that each
install the same Python dependencies and build the same development image.
In successful intermediate run `35319798078`, each repeated installation costs
17–29 seconds and each image build costs 35–45 seconds, before the actual
operational scenario's 69–165 seconds.

Pair the development and production variants of each setup state on one runner.
Four operational jobs now perform one installation/build each and run two exact
pytest nodes in one process. All eight scenarios remain mandatory and visible
in test progress: configured, initial, completed setup and cancelled setup,
each in development and production topology. Their credentials, source state,
database, UUID Compose project and runtime volume remain separately initialized
and torn down per test. Only immutable image/dependency setup is shared.

The paired job has a fifteen-minute ceiling, replacing each single scenario's
ten-minute ceiling; application command/lease/drain deadlines are unchanged.
Pytest continues to the second case after a first-case assertion failure, while
the final job fails. Matrix fail-fast remains disabled. The existing protected
`stewardship-compose` aggregate still requires both core and every matrix job
to succeed, rejecting failure, cancellation or skipped work. Its name is unchanged.

The workflow-contract regression checks the exact two-node invocation and
independently compares all selected nodes with actual pytest collection.
Future added/changed parameter cases cannot silently disappear from CI. Browser
jobs remain separate: serializing their three complete suites could introduce
a new bottleneck. PostgreSQL's twelve isolated clusters and complete same-tree
coverage receipts remain unchanged.

## Relevant-test cleanup

Remove only the duplicate positive event-registry loop from the source-failure
module. The existing individually parametrized SQL-admission test still checks
every event. Move the unknown-event rejection alongside that test, with a
rolled-back SQL transaction and an assertion for the exact rejecting constraint.
This removes unrelated source singleton/transactional-flush setup without
removing the database allowlist contract that caught PR #53's integration defect.
No PostgreSQL-engine tests, application behavior, authorization checks, real
lease/drain waits or reference-scale scenarios are weakened.

## Acceptance evidence

Focused workflow/collection and real SQL event-contract checks precede the draft
PR: 57 workflow/build checks pass in 3.37 seconds and 24 real SQL event-contract
checks pass in 10.15 seconds. Repository Ruff/formatting and changed Markdown
checks pass. All eight actual Compose scenarios run in CI using the paired commands;
do not duplicate that complete matrix locally. Final CI duration, paired job
results, exact head and three review rounds are recorded in the PR handoff,
then linked by the successor's delivery receipt. Savings above are measured
bootstrap costs, not an unverified promise of final wall-clock improvement.

## Protected delivery

[PR #54](https://github.com/epiphany40223/parishkit/pull/54) merged on September
18, 2026 at 13:01:18 UTC as `17d5d4d9e919a5bd050ceb02996afbbb2771071e`, verified
on freshly fetched `origin/main`. Three successful dual-source rounds reviewed
`113fcd0f..e56e9a4`: sessions `20260918-040748-6a88f4`,
`20260918-041140-45b726`, and `20260918-071320-3f46ba`. The first round's four
Low notes were evidence-dispositioned in the PR; subsequent rounds had no
findings. No accepted Medium-or-higher issue remains.

Exact-head CI run `35322728438` passed all 24 jobs and DCO at `e56e9a4`, tree
`8a660366ba9644ecbd37d1174e5cb509aa3d833f`. Stewardship coverage is 93.90% lines
and 84.86% branches. Paired operational jobs took 4m57s–6m53s; PostgreSQL jobs
took 7m21s–12m49s. Initial queue delays overlapped predecessor main validation,
so this run does not establish an end-to-end speedup.

The human authorized replacing the obsolete operational required-check names
with all four paired-job names. Ruleset `18237970` was read back and verified
against that exact change: every other rule, application binding, condition,
enforcement setting and bypass list is unchanged. No protection was bypassed.
The [source-health increment](stewardship-source-health.md) follows from this
verified merge; broader BG-10 and Gate 3 remain open.
