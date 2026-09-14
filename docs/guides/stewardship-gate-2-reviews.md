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
The first fresh dual-model integration round is recorded below; Gate 2 remains open.

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
corrected-tree validation and remaining fresh review rounds are still required.

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

## Round 1: current PR and Family integrations

Session `20260914-091641-185fb1` reviewed base `e406ecfa` through
`14f2ec4eee749ca2c9248fb83d630df5d84551f4`, clean tree
`2100c88e1d5b900b39a873e429ad8bcf852836c0`. One Claude and one Codex reviewer
completed successfully; Codex took 801 seconds and exited zero. There were no
failed agents, degradations, verdict mismatches or salvage requirements. The
finalized artifact SHA-256 is
`605e2c063e7803b738eacd6e5b332b195015f1007e04115f552aa611827bfc13`.
Both vendors explicitly covered G2-I3/I4/I6/I7/I8 and current interacting owners.
Claude recorded all 35 manifest files with none skipped. G2-I1/I2/I5 remain
assigned to round 2; submission-facing inspection alone does not close them.
Codex could read the workspace and complete review but could not run pytest in
its read-only temporary environment; parent-run tests provide execution evidence.

The verdict was Request Changes: eight raw findings (one High, three Medium,
four Low), with four Medium+ retained. All four were verified and corrected:

- **R1-01 / Medium, Claude:** selected `login_help` and `access_denied` blocks
  now have public-only consumers. Invalid manual codes and opaque links retain
  identical denial content; optional help fails safely to the fixed denial
  during configuration/database outages. Admin denial behavior is unchanged.
- **R1-02 / Medium, Claude:** test completion has its own prominent heading and
  explicitly says the campaign response was not recorded. Disposable/return
  instructions use live-campaign wording. Parish-authored Thank You content is
  retained for testing, under an explicit preview-only heading rather than
  presented as a real campaign acknowledgement.
- **R1-03 / High, Codex:** deferred checks require exact follow-up predecessor
  disposition and replacement identity, not merely the right latest text. An
  unchanged response cannot mint a duplicate text occurrence. Four ordinary/
  restricted-web cases reject false withdrawal and duplicate-history derivation,
  prove rollback, then verify correct linked history on normal retry.
- **R1-04 / Medium, Codex:** displayed content's effective public substitutions
  participate in the concurrency projection. Website, phone and non-financial
  campaign-year edits now require fresh review when interpolated into selected
  content; unused settings do not invalidate the form. Rendering and dependency
  selection use the same slot registry and public-value adapter.

Post-correction focused validation passes 45 history/authority/revisit database
tests, 35 initial content/HTTP cases, 66 Family-flow browser cases across three
engines, and the complete 5,299-test baseline. The broader 95-test database
run also passes: selected/unused substitutions, public help, Family
authentication, independent response authority and all strict schema regressions.

### Full-run regression and correction

At the reviewed tree, all 720 browser cases, 12 container-isolation cases and
17 runtime/provisioning cases passed. The independent 17-case schema suite,
baseline, lint/formatting/Markdown and model-drift checks also passed. Corrected
mobile and desktop review screenshots were visually inspected.

The complete four-shard database run executed all 2,510 cases: 2,504 passed and
six failed, so **no passing coverage aggregate is claimed**. All six failures
were old authority-test setup helpers that deliberately fabricated incomplete
historical submissions. The new parent completeness guard now rejects those
fixtures before their independent child-authority assertions. Their corrupt
history is now explicitly injected by the disposable test schema owner; the
parent guard is restored and its enabled state verified before every tested
restricted-web mutation. No production guard, expected denial or current-state
assertion was weakened. All six cases pass in the 45-test targeted rerun.

The setup-cleanup scenario again emitted its 120-second diagnostic stack dump,
then passed; the slowest shard completed in approximately 17.5 minutes. This
was not an unfinished or abandoned test. The new disposable shard containers
were stopped after all four processes exited; retained databases were untouched.

### Round-1 correction schema audit

A separate new reference database installed immutable `14f2ec4` and matched its
strict catalog fixture before comparison. Again only
`stewardship_submission_effects_v1()` changed, with all object counts, ownership
and grants unchanged. The corrected function fingerprint is
`3a5c2ed32a9437695f76e39907188ee38777d381368e96e34ccecca1fb186847`.
This audit does not replace the pending corrected-tree full coverage run.

## Round 2: setup, source and session integrations

Session `20260914-094624-4d43f5` reviewed the corrections from `14f2ec4` through
`78bcb67c9ceea335f16c56fcaeca70d212647394`, clean tree
`407bd9be8b2b7bbf7e5429e9fe0e35c71a020eb2`. Both vendors completed successfully
with no failures, degradations, verdict mismatches or salvage. Codex completed
in 282 seconds; Claude recorded all 17 manifest files without skips. The final
artifact SHA-256 is
`2ae2f552db40147805c2cf952cb0f46a2239234e210eaca66d212759e190f590`.

Both summaries explicitly covered G2-I1/I2/I5 and the round-1 corrections.
Claude inspected setup completion/rollback and access gating but relied on
PR #22 for staged-secret internals. Round 3 must explicitly revisit that
credential replacement/testing → final prepared manifest → completion/abort
boundary, as well as all integration IDs' negative cases; component reuse alone
does not substitute for that fresh boundary check.

There were no High/Critical findings. Six raw findings comprised one Medium and
five Low; the single Medium was retained and verified:

- **R2-01 / Medium, Codex:** optional login-help database/configuration/rendering
  failures could bypass the safe fallback used by denial help. Both callers now
  use one optional-help adapter. Six fault-injection cases independently raise
  configuration, database and rendering exceptions for login and denial help,
  preserving fixed responses and omitting diagnostic values.

The corrected image at `78bcb67` passed all **30 Compose cases** and all **eight
operational cases** (727.27 seconds), including configured, initial, completed
and cancelled setup under both development and production-shaped profiles.
These use synthetic credentials and do not perform a deployment or real provider
operation. R2-01's targeted content/authentication run passed all 40 tests in
55.05 seconds. The third review and corrected-tree full coverage remain pending.
