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
fund queries. Financial inputs participate in projection version `family-inputs-v7`;
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

## Review round 1

Session `20260914-050258-d58f40` reviewed the complete branch from
`c0ab9a1259c6a2c459b6568917e2da56278f061b` to
`6cd1fe6f2f3dba37d2f4d29badddb92a1444afe3`, tree
`6ef88c944d2408c27080a0e2de8d8c3e8e06ea4b`. Pika generated two Claude shards
and one Codex reviewer; all completed successfully without degradation,
mismatch or salvage. Raw severities: seven Medium and fourteen Low; six
distinct validated Medium findings, all accepted and corrected:

- Share labels, Admin previews and page blocks use one campaign-year helper;
  upcoming-period placeholders retain their distinct meaning.
- Independent INSERT tests now forge disabled-module JSON, scalar and combined
  financial intent and require rollback.
- Withdrawing a removed share method cannot leave an invisible conflict that
  blocks review forever; all three browser engines exercise the concurrent-note
  case.
- Unchanged giving-observation timestamps do not force review; changed scoped
  amounts/availability still do. The data specification records the distinction
  and retained reviewed/validation snapshots preserve exact observation context.
- Disabled census supplies only an effective household count from retained
  unresolved terminal/proposed intent, tested through two subsequent responses
  and zero/one/many browser wording without exposing disabled request details.
- The parish name participates in the financial dependency projection, so a
  renamed parish's share wording requires an explicit refreshed review.

Finalized artifact SHA-256:
`4106976f726a9bde4834208005b4448f6fad9e6a80c1ea07e588b0dbda3424c7`.
Post-correction validation passed 5,287 default-suite tests, 79 PostgreSQL
financial/baseline/validation tests and 42 financial browser cases. Additional
combined census/Ministry/financial terminal checks passed one PostgreSQL and
three browser cases. Ruff lint/format, tracked Markdown and whitespace checks
pass. No accepted Medium-or-higher finding remains from this round.

Before corrections, the complete browser suite passed 669 cases (223 per
engine), Compose passed all 30 checks in 145.16 seconds, and schema drift found
no changes. The longer serial PostgreSQL coverage run was deliberately stopped
before source corrections; it is not completed coverage evidence. Final-head
coverage, full applicable regression validation, two further review rounds and
protected CI/delivery remain required.
