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
  use one optional-help adapter. Six fault-injection cases inject ConfigError,
  DatabaseError and ValueError at the public-help boundary for login and denial
  help, preserving fixed responses and omitting diagnostic values.

The round-1-corrected image at `78bcb67`, before R2-01, passed all **30 Compose
cases** and all **eight operational cases** (727.27 seconds), including configured, initial, completed
and cancelled setup under both development and production-shaped profiles.
These use synthetic credentials and do not perform a deployment or real provider
operation. R2-01's targeted content/authentication run passed all 40 tests in
55.05 seconds. At that checkpoint, the third review and corrected-tree full
coverage remained pending.

## Round 3 and supplemental integration coverage

Round 3, session `20260914-145034-079615`, reviewed `78bcb67` through
`81dcbf8cb6502a3cb0f7841df943f1f1635c4d28`, clean tree
`6f885f6d9235931766e82e6a4f835d730ce3b02a`. Both vendors completed successfully;
Codex took 326 seconds. The finalized result was Approve, six raw Low findings
and none Medium/High/Critical. Artifact SHA-256:
`8bb517c8650ec78f7577884a4ca3b7f208a16ddd0e63445a3ce88aa53799f6b3`.

Claude freshly inspected the complete staged-credential replacement/testing,
prepared-manifest, completion/abort boundary (G2-I1), closing its round-2 gap,
and rechecked G2-I5. It explicitly reused earlier evidence for G2-I4 and parts
of I2/I3/I6/I7/I8. Codex reported all IDs freshly inspected. This was the third
successful dual-source round, but the remaining promised fresh negative-case
coverage was not declared complete from that count alone.

Supplemental session `20260914-145833-72e135` therefore reviewed the same clean
endpoints with focus limited to current I2/I3/I4/I6/I7/I8 integrations. Both
vendors explicitly covered all six IDs, identifying current interacting owners
and negative cases. I1/I5 were deliberately not repeated. Parent browser
execution and visual inspection supply the execution evidence; reviewers did
not claim manual screen-reader or real-provider validation. Codex completed in
358 seconds. Neither session has failed agents, degradation, mismatch or salvage.
Supplement artifact SHA-256:
`eefc35ff11476c7b076ddf4a65b0918eaca7f9097d6b052e90642c708b7fb607`.

The supplement reported three raw Medium and three Low findings, no High or
Critical. Pika retained one Medium and filtered two only because their paths
were outside the correction diff. Those two were still inspected as part of
the explicitly requested integrated gate scope, rather than assumed invalid:

- **S-01 / Medium, accepted:** optional help was suppressed inside presence's
  existing transaction without a savepoint. A real SQL failure could discard
  session revocation and its audit. The adapter now rolls back its own nested
  transaction before suppressing the error. The regression uses actual division
  by zero in PostgreSQL on the presence-denial path, checks lookup invocation,
  safe response, persisted revocation and the additional audit event. The six
  earlier fault cases also now assert exact invocation counts.
- **S-02 / Medium, rejected as false-positive:** the alleged live hidden-question
  withdrawal requires changing `additional_information` after Production
  activation. `campaigns/admission.py` excludes that field from the editable
  set, so it is structurally locked; the real installer rejects the change. The
  new restricted-HTTP regression retains that rejected operation, verifies
  unchanged active configuration and actionable text, then resubmits unchanged
  text without creating duplicate work. The cited Testing-only test creates no
  live follow-up. An exploratory implementation was removed after this failed
  premise was established; no schema or live follow-up semantics changed.
- **S-03 / Medium, later-phase consumer clarified:** `submission_confirmation`
  is the parish-authored receipt-email block owned by BG-07, while browser
  completion uses `thank_you`. The Admin label, data contract, BG-07 plan and
  acceptance boundary now explicitly identify that email consumer. Actual
  delivery remains the existing Phase 4 dependency, not claimed implemented.

### Below-cutoff Low dispositions

All raw Lows remain visible here, including duplicates across rounds. Later
hardening owners do not waive those owners' eventual acceptance criteria.

| Origin | Disposition and evidence / later owner |
| --- | --- |
| Historical attempt: proposed-Member source-change wording | Defer cosmetic wording to FAM-08 final copy/accessibility pass; the separate proposed-household heading already identifies these values as requests. |
| R1: final Testing checkbox deletion wording | Defer copy refinement to FAM-08; explicit deletion and non-campaign-response notices are present at entry/completion, and final consent says disposable test. |
| R1: duplicate-name Edit accessible labels | Defer ordinal disambiguation to FAM-08 manual screen-reader pass; section focus and automated AA checks pass. |
| R1: nullable completeness source witness | Reject the hypothesized valid path: the parent guard enforces exact active household and closed field vocabulary; both source helpers return explicit boolean availability for those admitted inputs. Schema-owner corruption is not an admitted submission. |
| R1: stronger private-route baseline/pin assertions | Defer additional negative assertions to FAM-08/OPS-09; present admission, private-byte, no-submission and baseline-authority tests remain required. |
| R2 denial-help cost; R3 and supplement repeat | Defer duplicate public configuration work and cheap 429/503-path optimization to OPS-08 load/abuse measurements. It exposes only campaign-wide public content; rejection and rate-limit outcomes are unchanged. |
| R2 duplicate GET configuration reads; R3 repeat | Same OPS-08 optimization; no private form or submission is admitted by the public-help read. |
| R2 fault matrix; R3 and supplement repeats | Basic six-case exception coverage and actual transactional SQL failure now pass. Exhaustive link/429/503 permutations remain FAM-08/OPS-09 hardening; callers share the tested adapter. |
| R2 blank nonpublic projection constants | Defer docstring refinement to OPS-09 maintenance. Blank constants are not private values; actual Family and financial inputs have their separate concurrency owners. |
| R2 fixture guard-restoration failure | Reject a falsely green aggregate: restoration failure errors the test and invalidates its shard receipt. Restoration is verified before the tested web mutation; strict schema checks remain independent. |
| R3 and supplement missing injected-call assertion | Fixed: each original fault case now requires exactly one invocation, as does the actual SQL-failure case. |
| R3 silent invalid-slot/rendering failure signal | Defer redacted operational notification to OPS-08; current callers use two constant supported slots. Safe error fallback is intentional and must not log exception text. |
| R3 ledger and correction in one commit | Reject: the commit is one verified correction plus its directly related review evidence; its body explicitly describes both. |
| R3 ambiguous prior-image wording | Fixed above: `78bcb67` image evidence is explicitly before R2-01. |
| Supplement boundary-injection versus renderer execution wording | Fixed above: the original cases inject three exception types at the public-help boundary, not actual renderer faults. |

## Corrected-tree validation checkpoint

At `81dcbf8`, all 720 browser cases passed (240 per engine), with Chromium
180.03 seconds, WebKit 270.36 seconds and Firefox 553.90 seconds. Fresh
320-/1,280-pixel screenshots were visually inspected with no clipping or
horizontal overflow; the full flow's keyboard, focus and automated AA checks
passed. The rebuilt image passed all 30 Compose cases in 123.82 seconds.
Ruff, formatting and all tracked Markdown passed.

The complete four-shard database attempt executed 2,534 cases: 2,522 passed
and 12 failed. All failures were unexpected synthetic Admin OAuth callback
denials in setup/share-option/task-status tests. Its 5,299-test baseline passed;
the prior six corrupt-history fixture regressions are resolved. No passing
coverage aggregate is claimed. All 49 affected-module cases passed in an
isolated diagnostic rerun, so the full-run cause is not yet established and a
complete corrected-tree run is still required. The known 120-second setup
cleanup diagnostic completed successfully at 142.54 seconds.

S-01's actual SQL regression passed in the initial targeted run. Four exploratory
S-02 tests failed precisely because live structural edits were rejected; their
incorrect assumption and unnecessary implementation were removed. The final
confirmed-correction suite passes all 88 presence/content/HTTP/schema tests in
99.26 seconds; all 11 content-form unit tests pass. Complete coverage and the
renewed focused review remain pending. Gate 2 is still open.

## Final correction review and passing local gate

Session `20260914-153915-538b9b` reviewed `81dcbf8` through
`44455b541c65d46d6ba6346070774414a3b3532d`, clean tree
`33084974b08d2aeee70d356f1d4391ed759b3fc7`. Both reviewers completed successfully
and explicitly confirmed S-01, S-02 and S-03. Codex took 377 seconds. The result
is Approve: five raw Low findings, no Medium/High/Critical, and no failed agent,
degradation, mismatch or salvage. Artifact SHA-256:
`2f8f480d4aff566bdac0b4b29cadfa279f30f7ddb6105b38e45b6f63e6b3fde3`.
This is the fifth successful dual-source round; the earlier four are retained,
not reset by the correction-focused check.

Final Low dispositions: corrected the ambiguous structural-lock wording and
historical pending-review tense above, and wrapped the prior-image paragraph.
An exact structural-rejection failure-code assertion is deferred to OPS-09
test hardening; current code inspection and the same patch's successful Testing
case establish the rejected live premise. The receipt block's precise composition
with the confirmation email template remains an explicit BG-07 implementation
detail, not a claimed current consumer. Both versions must remain coherent under
that owner's existing content/delivery contract.

The complete frozen-tree rerun passed **5,299 baseline tests** and **all 2,536
PostgreSQL tests**: shards 643/632/628/633, with no enabled skips or missing cases.
The independent receipt/partition combiner passed and measured **94.03% line
coverage and 85.62% branch coverage**, both above the 80% floor. The longest
database shard took 899.98 seconds; the baseline-bearing shard's database part
took 740.40 seconds. The earlier 12 synthetic OAuth denials did not recur.
Their cause remains unconfirmed, rather than attributed to a speculative fix.

After the server-only correction, the full review/edit/submit scenario was run
again at 320 and 1,280 pixels and both complete review-page screenshots were
visually inspected. Both cases passed keyboard/focus, AA scanning, all section
edits and final submission, with no clipping or horizontal overflow. The earlier
720-case browser run covers the unchanged Family JavaScript. Current model drift,
Ruff, formatting, Markdown and whitespace checks pass. The 88-case targeted
suite includes the current strict schema checks; S-01 introduced no schema change.

All local Gate 2 acceptance and review requirements are satisfied, with the
documented Low deferrals and later-phase boundaries above. Standing human
merge/continue authority applies under the controlling automated delivery cycle.
Protected delivery is still pending: require final-head CI, the normal merge
queue, all merge-group checks, and verified `origin/main` ancestry before
releasing Phase 4. No deployment, release or real-provider authority is implied.

## CI correction: initial-setup installer contention

PR #30's first exact-head run (`34890151052`, head `51d612a`) passed every
check except the development-profile setup-completion scenario and its Compose
aggregate. The worker encountered `ConfigurationBusy` during final promotion;
the unclassified exception retained leases until abandoned-task recovery and
missed the browser's completion deadline. This was not a PostgreSQL assertion
failure or a reason to extend the test deadline.

A regression using an independent PostgreSQL session holding the actual
installer advisory lock reproduces that same exception on the original code.
The final-setup owner now classifies this post-read contention as a safe retry:
reject the unpromoted observation, release the source claim, preserve its HTTP
drainage deadline, and recheck the original setup authority on retry. The lock,
atomic configured marker, unknown-error handling and database guards are not
weakened. The test also exercises successful completion through the same Task
root after the real exclusion window drains, without rewriting clocks or fences.

Correction-specific PostgreSQL/operational validation, focused dual-model review
and replacement exact-head CI are in progress. This correction reopens the local
checkpoint above; neither protected delivery nor Gate 2 exit is yet claimed.

### Contention correction review

Session `20260914-161453-aeb717` reviewed `51d612a` through
`ad325726544d031e002e610d4b4052c376054457`, clean tree
`b763dda4a31b28a33b9c10857bfd8196a198d3f9`. Both vendors reviewed all three
changed files and the finalization/installer/task/source relationships. Codex
completed in 183 seconds with no findings. Claude reported one Medium and three
Low findings; there were no failed agents, degradations, mismatches or salvage.
Finalized artifact SHA-256:
`6493138b627fcd99d38cfba1b580f0600deaf7010e8e819a62fae9ce1d3c309a`.

- **Medium / independent retry clocks:** accepted. The regression now waits for
  both the retained HTTP exclusion and TaskRun backoff using database time. Slow
  staging cannot make success depend on their accidental relative ordering.
- **Low / preserved drainage assertion:** accepted. The regression observes the
  actual deadline before the unchanged release function and requires that exact
  deadline after settlement, rather than assuming it remains in the future on
  every machine. Missing-claim contention is also explicitly required to propagate.
- **Low / source-held event wording:** defer a distinct configuration-contention
  diagnostic to OPS-08. The existing typed event records held source work with
  the finalization task/correlation; no exception text or unreviewed event enters
  durable logs. Retry behavior does not depend on the diagnostic name.
- **Low / partially readable manifest:** reject the stated premise. Authority
  selection uses a same-directory fsynced temporary file and atomic replacement;
  readers cannot observe a half-written manifest. Genuine unreadable/corrupted
  authority remains fail-closed rather than being reclassified as harmless
  contention.

Local validation exposed an overbroad new assertion that included the separate
ready setup-catalog snapshot; it now checks only the finalization task. The other
12 focused cases passed, and both real development/production-profile setup
completion scenarios pass against the corrected image (258.22 seconds total).
The superseded head's CI run `34891795985` was cancelled to avoid spending runner
time on the known test-only error. Final regression validation and renewed
correction review remain required before the replacement CI run and merge.
