# Stewardship fast CI feedback

Human-authorized maintenance increment, September 19, 2026. Branch
`pr/stewardship-ci-feedback` starts at verified main `2c3151e6`; reporting PR #63
is preserved separately at `070aa91`. Resume that PR's remaining review
corrections after this increment lands. This work changes no application schema,
retained database, provider configuration, release or deployment.

## Two feedback stages

The [delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
now keeps intermediate work in draft. Every push runs lint, fresh-model drift,
and a small packaging/grants/CI-contract test set. Developers run focused tests
for changed application behavior during corrections. The fast set deliberately
does not claim complete coverage or automatically infer every affected dependency.
Tune that focused set for each PR's new/changed behavior. Run short tests locally
when faster or comparable, avoiding GitHub startup overhead; do not rerun a
passing focused check remotely merely for its location. The complete candidate
suite remains independent final-head merge evidence.

Marking a reviewed candidate ready triggers full PostgreSQL, browser and
container validation, after the fast checks succeed. Ready-PR pushes and main
pushes still run the full suite. For multi-commit corrections, return to draft
first; marking ready again must validate the current head, not reuse evidence
from an earlier commit. Full suite coverage, all scenario matrices, DCO, three
dual-source review rounds and normal protected merge remain mandatory.

The `ready_for_review` and `converted_to_draft` events are explicit. Superseded
PR runs cancel; main runs remain independent. Required aggregate checks always
run and fail when their prerequisites were skipped, cancelled or failed. Thus
draft full-suite gates intentionally remain red with a readiness explanation;
fast feedback is the `validate` result. This prevents a skipped job's successful
GitHub conclusion from becoming accidental merge authorization. No ruleset
change, alternate success check or privileged merge bypass is introduced.

## Measured scheduling and setup

Successful CI run `35438716036` took 17m18s. The slowest PostgreSQL shard spent
15m16s in measured execution, after about 43 seconds of runner/service/install
startup. A reference confirmation case took 120 seconds but had the default
one-second partition weight; several setup-heavy modules averaged 14–26 seconds
per case with the same default. Updated hints account for those costs. Every
collected test still has exactly one owner; timings never filter the suite.

Module estimates come from successful-run progress logs including per-case
setup/call/teardown, excluding each runner's first case because it includes
session database creation. Exact slow-case estimates override module means.
Future successful shard artifacts include setup/call/teardown timings in a
separate `tests.timings.json` file
to support maintenance without repeatedly scraping logs. These timing fields
are diagnostic only; complete exact-tree execution and coverage receipts remain
the admission authority.

Two setup HTTP rejection matrices now share one unchanged prepared scenario
per workflow. All seven malformed-input assertions remain, with per-case labels
and no-write checks; confirmation additionally proves the same valid token can
still complete after all rejected attempts. Successful mutations, concurrency,
real lease waits and 5,000-Family scenarios retain independent isolation and
their full assertions. This is fixture reuse, not deletion of acceptance cases.

## Validation and remaining delivery

The fast contract set passes 221 tests in 15.47 seconds locally. The unchanged
two-module PostgreSQL baseline passed 13 tests in 49.38 seconds. Record the
post-consolidation comparison, full-suite timing and three review rounds before
acceptance. Full CI is intentionally deferred until the candidate is ready.
Do not claim a measured end-to-end improvement from scheduling estimates alone.

The consolidated PostgreSQL run passes eight grouped tests in 34.17 seconds,
preserving all prior input cases and adding successful confirmation after
rejections. That is 15.21 seconds less in this local comparison (about 31%);
runner variability means it is not a promise for the complete suite. Ruff,
formatting, changed Markdown and whitespace checks pass.

Draft CI run `35440851566` demonstrated the new boundary: `validate` passed in
69 seconds (26 seconds in the fast tests), no expensive suite launched, and all
three required full-suite aggregates stayed blocking. This expected draft
failure is not a failed application test or full-suite acceptance.

## Review round 1

Session `20260919-074315-708841` reviewed `2c3151e6` → `c085e2c`. Both reviewers
completed without failure, mismatch, degradation or salvage. Raw findings were
one Critical, one High, one Medium and six Low. The three validated findings
describe one defect: both reviewers identified the incompatible timing field
in the strict execution receipt; the Medium documentation finding follows from
that same mismatch.

Accepted and corrected together: timing data now uses a separate diagnostic
artifact. The authoritative receipt and strict combiner remain unchanged. The
real pytest-plugin receipt is passed through the real combiner in regression
coverage, alongside existing complete-membership rejection tests. The High and
Medium reports are duplicates of that accepted correction, not waived issues.
Post-fix validation passes all 150 focused CI/browser-runner tests in 14.77
seconds, including the real producer-to-combiner regression. Ruff, Markdown and
whitespace checks pass. Two further completed rounds remain required.

## Review rounds 2 and 3

Round 2, session `20260919-074950-133bd2`, reviewed corrections
`c085e2c` → `38fc074`. Finalization approved with no validated Medium+ findings;
all six raw notes were Low. Round 3, session `20260919-075432-d2a09f`, independently
rechecked the same corrected code and its surrounding workflow/fixture contracts:
approved with two raw Low notes and no validated Medium+. Neither round had
failed reviewers, degradation, verdict mismatch or salvage. The Low notes remain
below the agreed correction floor, not exceptions for unresolved Medium+ issues.

The reviewed tree is `bec276f3d7d871c8fcf46bd505cb731725851e75`. Its focused
post-fix tests and corrected-head fast CI passed. This final ledger addition
changes documentation only. Squash the implementation/fixup history without
changing the resulting tree, then mark the exact final candidate ready. Full
CI/DCO and protected merge remain required; a draft's expected blocking gates
are never passing acceptance evidence.

The first ready run, `35441515281`, found three stale workflow assertions in
`test_database_gate.py`: two expected the gate command without its new explanatory
output, and one forbade any preflight pytest invocation. The correction retains
the success-only aggregate checks and permits only a bounded explicit-module
smoke command, never an implicit full baseline. That module now joins the fast
set. All 166 focused gate/runner/browser-contract tests pass locally in 18.82
seconds, with Ruff and formatting checks passing. This updates test expectations
for the already reviewed policy, not application behavior or acceptance scope;
the next exact-head full CI run must still pass before merging.

After protected delivery, record the merge and actual full-suite timings in the
reporting successor's handoff, rebase preserved PR #63 onto the refreshed main,
and finish its three outstanding third-round admission findings. Its prior
completed review rounds remain recorded; any CI-driven material correction
returns to focused independent review.
