# Gate 2 integration reviews

[Controlling gate](../plans/stewardship/overall.md#review-gate-2-family-data-privacy-and-ux) ·
[Acceptance and test map](stewardship-family-acceptance.md)

## Approved procedure and scope

On September 14, 2026, the human approved reusing recorded reviews of merged PRs
plus focused dual-model integration reviews with explicit coverage of every
Gate 2 requirement. The preserved historical baseline is
`48be3666f0c89cc15586cb67465cd1ba0504203c`; the current acceptance branch starts
at PR #29's merge `e406ecfa5af136aa06b67e5461bc1389360b2a8b`.
This is a review-procedure change, not a relaxation of any acceptance criterion.
Three successful dual-model review/fix rounds remain required for this PR.

The integration scopes below are cumulative. Both vendors inspect current
cross-component owners, not only the changed lines. Their completion evidence
must identify the relevant scope IDs; an unexamined integration is not closed
merely because a component's old PR passed. Corrections to reused components
receive current regression tests and renewed focused review.

## Reused component evidence

| Merged increment | Review evidence reused, with endpoints and dispositions in its ledger |
| --- | --- |
| PR #22 setup/source and fresh-install baseline | [Three full Phase 2 rounds](stewardship-phase-2-reviews.md), [two successful supplemental consolidation rounds](stewardship-phase-2-consolidation-review.md), and [replacement-baseline delivery](stewardship-phase-2-simplification.md); obsolete pre-consolidation tests are not current-schema proof |
| PR #23 baseline/atomic response/revisit | [Three Phase 3A rounds and delivery](stewardship-phase-3a-reviews.md) |
| PR #24 Family census | [Three household review/correction rounds](stewardship-family-census-reviews.md) |
| PR #25 Member census | [Three rounds plus CI correction review](stewardship-member-census-reviews.md) |
| PR #26 terminal/proposed Members | [Three rounds and preserved reviewed-tree mapping](stewardship-member-requests-reviews.md) |
| PR #27 Ministry responses | [Three rounds and protected delivery](stewardship-ministry-responses-reviews.md) |
| PR #28 browser/CI execution evidence | [Five rounds, complete partitions and delivery](stewardship-browser-ci.md) |
| PR #29 financial responses | [Three rounds, schema audit and protected delivery](stewardship-financial-responses.md) |

Each linked ledger retains successful review endpoints, artifact digests,
corrections/rejections and validation. Failed/degraded attempts in those ledgers
are explicitly excluded from the count. Historical open checkpoints are not
substituted for the later successful completion and protected delivery record.

## Fresh integration coverage

| ID / controlling requirement | Reused components | Required current integration check |
| --- | --- | --- |
| G2-I1: first-Admin staging, abort/expiry, secret replacement/testing | PR #22 | Original setup identity → selected-but-unapplied rollback → atomic configured marker; current sessions/Family access cannot observe unfinished or cancelled setup. Inspect setup completion/disposal and current access gates. |
| G2-I2: source fences, full/delta validation, promotion/fallback | PR #22/#23/#27/#29 | Promoted snapshot → retained form pins → current submission validation and reconciliation; stale/failed source owners cannot change effective truth or Family proposal provenance. |
| G2-I3: immutable submission and derived-work rollback | PR #23–#29 | Every accepted enabled-section answer has complete atomic census, Ministry, additional-text, financial, receipt-intent and selector effects under restricted SQL authority, including no-op and late-failure derivation. |
| G2-I4: three-way merge/provenance and test/live isolation | PR #23–#29 | Revisit/source catch-up, Admin-resolved/edited proposals, terminal/proposed Members, hidden Ministry requests and financial intent retain correct namespace and attribution across all module combinations. |
| G2-I5: every authentication/boundary/session path | PR #22/#23 | Code/link → server acknowledgement → form/submit/presence/keepalive/logout; setup, interval, eligibility, mode and epoch changes fail closed before private bytes or writes. |
| G2-I6: no intermediate storage or answer leakage | PR #23–#29 | Current UI transitions/network failures, content rendering, diagnostics, pins and audit preserve tab-only drafts and expose no hidden source values, credentials or answers in passive requests. |
| G2-I7: mobile navigation, validation, focus, expiry and accessibility | PR #23–#29 | Full seven-module-combination flow, section editing/final consent, large household/Ministry lists, stale review and expiry. Inspect narrow-mobile and desktop rendering after corrections. Full release/manual matrix remains Gate 5. |
| G2-I8: parish-facing terminology/content | PR #22–#29 | All configured lifecycle/Member/Family blocks use the sanitized versioned renderer and correct dependencies; no ParishSoft implementation terms leak into ordinary Family pages. |

Fresh round 1 covers the whole current PR and G2-I3/I4/I6/I7/I8. Round 2 covers
its corrections plus G2-I1/I2/I5 and their submission-facing relationships.
Round 3 rechecks corrections and the cross-boundary negative cases for all IDs.
Additional scoped review is required if a listed integration is skipped or a
material correction broadens its affected owners. Gate exit needs completed
coverage for every row, not just three successful tool invocations.

## Superseded historical attempt: not a completed round

Session `20260914-074451-66dc69` covered the entire historical diff at `64ecb4e`.
Pika generated 59 Claude shards; none was launched. Its automatic Codex reviewer
completed in 899 seconds, exit zero. Following the human's procedure decision,
the attempt was finalized solely to retain its findings, not to claim approval.
All 59 absent Claude outputs are recorded as failed/missing agents; there is
no dual-model completion. The finalized artifact SHA-256 is
`6131ff7939a9df678b451c0e2666fa373ff85da37107e8a4710c5762e78b6c1d`.

It reported one raw High, two Medium and one Low; three Medium+ findings were
retained. G2-D1 concerns deferred completeness for census/additional-text effects;
G2-D2 concerns unused `member_census` content/dependencies; G2-D3 concerns unused
`pre_start`/`post_end` content. All three were verified and corrected below.
No fresh dual-model integration round is claimed complete yet.

## Pre-round corrections

- **G2-D1 / High:** the deferred submission guard now independently checks
  complete census/proposed-Member effects and earlier actionable-work
  reconciliation, preserving the existing retained-terminal exceptions. It also
  requires the correct live follow-up selection and each changed nonempty text
  occurrence. Sixteen no-op-owner cases cover Member, household, terminal,
  proposed-Member, census withdrawal, and new/replaced/withdrawn follow-up work
  under ordinary and exact restricted-web connections. All earlier rows, source
  pins, baseline and session state survive rejection, and an ordinary retry
  succeeds. The combined atomicity/submission/revisit/terminal suite passes
  **79 tests**. Initial development runs exposed a PL/pgSQL CASE parse error and
  an omitted-versus-null terminal-date exception; both were corrected before
  the complete passing run.
- **G2-D2 / Medium:** the selected Member introduction is displayed once before
  Member controls and omitted when census is disabled. Its immutable revision
  identity participates in the reviewed configuration projection. Non-legacy
  content slots select the unique content row in the exact YAML snapshot, not
  the legacy campaign reference mapping; tests use that existing Admin contract.
- **G2-D3 / Medium:** pre/post-campaign pages render the selected public content
  through the existing escape-and-sanitize pipeline alongside the mandatory
  fallback date/status text. The shared defaults leave Family names, codes and
  private links empty. Real HTTP tests cover selected/replaced revisions,
  disabled census, public placeholders and Member-introduction revision conflicts.

Content corrections pass **79 unit tests**, **27 HTTP tests**, and **six browser
tests** across Chromium, Firefox and WebKit. The earlier complete acceptance
run remains in the [acceptance ledger](stewardship-family-acceptance.md); final
corrected-tree validation and the fresh review rounds are still required.

### Fresh-install schema audit

A new retained reference database was installed from immutable `64ecb4e` SQL
and verified against that commit's complete catalog fingerprints. Comparing the
candidate fresh install found exactly one changed function:
`stewardship_submission_effects_v1()`. All 314 functions retain their ownership
and ACLs; only that function body changed. No other catalog category changed:
129 relations, 1,483 columns, 2,125 constraints, 667 indexes, 312 triggers and
28 policies. The strict function fingerprint is now
`6f452e6c6fff0085f263fe4ccab656199c51679fc7f9aa178d848a5d6095c2b4`.
No retained database was upgraded or deleted; this remains a fresh-install
baseline correction, not an upgrade/downgrade compatibility change.
