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

The initial pure money/answer checkpoint passed
93 unit tests: exact signed source cents and unavailable/zero distinction,
context-independent aggregation/installments, bounded annual pledge input,
versioned share identity/free-text validation and effective household wording.
No financial task is claimed complete until its full validation/review and
protected delivery evidence is recorded below.

The source checkpoint adds campaign/fund/period coverage proof, retained giving
observation timestamps, Family-bound aggregates and qualified snapshot/Family/
fund queries. Financial inputs participate in projection version `family-inputs-v6`;
the complete submission/browser owner now admits financial campaigns under
`family-response-v1`. The web role receives read-only giving
tables and the snapshot cursor, with no source-write grant.

Financial pure/adapter tests now total 125 passing cases. The full default
suite passed 5,261 tests. Real PostgreSQL validation passed 20 existing
baseline/operational-authority checks and three new source tests using actual
journaled configuration, staging, promotion and restricted web-role reads.
These prove cross-Family exclusion and complete-zero versus unavailable data.

The integrated owner now persists exact annual pledge scalars and complete
financial answers only in the final transaction. Independent SQL guards enforce
module boundaries, canonical amounts, known frequencies, stable option IDs and
normalized bounded text. Existing receipt, source pin, test/live, repeat and
rollback owners remain in that transaction. No payment or source write is added.

The browser adds mobile/desktop financial context, exact-cent installments,
share details and effective zero/one/many wording. Refreshed forms retain actual
edits, require explicit competing-value/removal decisions, and never resubmit
automatically. Disabled sections cannot retain submitted answers. Selected
methods whose text requirement changes retain their old note until explicit
discard. Page/share content uses only the existing inert substitutions.

Integration checkpoint: 43 PostgreSQL response/guard cases pass, including
restricted-web-role HTTP, all four financial module combinations in test and
live mode, repeat replacement, concurrent responses, all-terminal households,
source refresh and rollback. The default suite passed 5,284 cases. Twenty new
pure presentation/definition tests pass. All 27 initial financial browser cases
pass in three engines; full-suite/desktop coverage and delivery reviews follow.

Initial browser failures were test defects: action checkboxes remove themselves
before Playwright can verify a retained checked state, and the proposed-Member
helper assumed insertion order despite UUID sorting. Tests now exercise the
actual action and identify newly added controls by identity, without retries or
increased timeouts. The complete validation and review loop remains pending.
