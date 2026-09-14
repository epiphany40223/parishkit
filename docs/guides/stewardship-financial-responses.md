# Financial Family response increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[Family tasks](../tasks/stewardship/parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps) ·
[Controlling plan](../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice) ·
[Financial specification](../specs/stewardship/parishioner-portal/spec.md#financial-stewardship)

## Boundary

Branch `pr/stewardship-financial-responses` starts from PR #28's verified
main merge `c0ab9a1259c6a2c459b6568917e2da56278f061b`, after final-head and
complete merge-group CI passed. Deliver FAM-05.03/.04/.05 and the remaining
financial/terminal interactions in .06 as one demonstrable vertical slice.
Include the associated DAT-06 submission/concurrency and RPT-02 money/
unavailable calculation primitives. Report screens, payment execution,
financial source writes and Phase 4 receipt dispatch are not part of this PR.

Follow the linked specifications rather than copying their normative rules.
Keep all edits tab-local until atomic final Submit, preserve every existing
census/Ministry contract, and enforce enabled-module and exact source/Family
boundaries in Python, database guards and browser behavior. This is still
pre-production: edit the fresh-install schema baseline where needed, with no
historical upgrade path/tests and no retained development database deletion.

## Internal checkpoints

1. Add exact shared money/unavailable calculations and pure financial input/
   answer validation, with focused unit tests.
2. Load only the Family's configured source-window aggregates from its pinned
   snapshot, proving coverage/as-of rather than treating absent data as zero.
   Extend the form-definition/dependency projection and test relevant versus
   unrelated changes.
3. Add immutable financial answers and the annual pledge scalar to the existing
   final transaction, with independent SQL guards, rollback, repeat/test-mode
   and restricted-web-role PostgreSQL tests.
4. Complete the mobile editor/review, stable share options, effective household
   wording, decimal installments, unavailable warnings, and in-memory conflict
   handling. Exercise all browser engines and enabled-module combinations.
5. Run full applicable validation and at least three dual-model review/fix
   rounds under the controlling cycle. Fix accepted Medium+ findings, then
   require final-head and complete protected merge-group CI before delivery.

Gate 2 remains open for integrated Family-flow/privacy/UX acceptance. These
checkpoints are not separate PRs or routine human approval stops. No deployment,
release, provider write or retained-data destructive action is authorized.

## Status and evidence

Implementation has started. The initial pure money/answer checkpoint passes
93 unit tests: exact signed source cents and unavailable/zero distinction,
context-independent aggregation/installments, bounded annual pledge input,
versioned share identity/free-text validation and effective household wording.
These helpers are not yet exposed by the portal. No financial task is claimed
complete until its source, transaction and browser integration passes.

The source checkpoint adds campaign/fund/period coverage proof, retained giving
observation timestamps, Family-bound aggregates and qualified snapshot/Family/
fund queries. Financial inputs participate in projection version `family-inputs-v6`;
the enabled-module gate still rejects financial campaigns until the complete
submission/browser owner is wired. The web role receives read-only giving
tables and the snapshot cursor, with no source-write grant.

Financial pure/adapter tests now total 125 passing cases. The full default
suite passed 5,261 tests. Real PostgreSQL validation passed 20 existing
baseline/operational-authority checks and three new source tests using actual
journaled configuration, staging, promotion and restricted web-role reads.
These prove cross-Family exclusion and complete-zero versus unavailable data.
No financial response has yet been accepted through the portal.
