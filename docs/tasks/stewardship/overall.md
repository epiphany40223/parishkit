# Stewardship top-level task execution plan

Start here to coordinate all eight [subsystem task lists](README.md). This is
the task-level navigation companion to the
[controlling implementation plan](../../plans/stewardship/overall.md), which
owns dependency ordering, allowed phase scope, and gate exit requirements.
Use [milestones](milestones.md) for demonstration and review status.

Standing scope override: follow the
[pre-production development policy](../../specs/stewardship/operations/spec.md#pre-production-development-policy)
for all remaining work. Historical upgrade/downgrade implementation and tests
are not dependency-ready requirements until production-readiness work is
explicitly activated; older evidence describes what was tested then, not work
to recreate after baseline consolidation.

## How to select the next work

1. Find the earliest incomplete phase whose preceding review gate has passed and
   whose preceding phase PR has merged. Follow the controlling plan's
   [automated phase delivery cycle](../../plans/stewardship/overall.md#automated-phase-delivery-cycle)
   for branch creation, delegated decisions, review rounds, CI, and standing merge authority.
2. Follow the ordered package links below; within a package, inspect its full
   plan item, task evidence, and dependencies before selecting work.
3. Deliver a coherent subphase/vertical slice, verify it, and update its owning task lists.
   Record partial scope when a package spans phases; do not mark a whole package
   done because its first consumer works. Continue through dependency-ready tasks
   within the batch without routine approval stops. Individual storage records,
   migrations and helpers are internal checkpoints, not default PR boundaries.
4. Complete the phase demonstration and required review/fix rounds, then create
   or update its PR and correct CI failures. At a formal gate, also validate and
   review the complete integrated gate scope. Follow the controlling cycle's
   standing merge authority and remaining explicit approval boundaries; wait for
   the merge to land on `origin/main` before branching the next increment.
5. On handoff, record active phase, completed task IDs, implementation SHA,
   validation evidence, remaining scope, and the next dependency-ready task.

The sequences below reference packages; a suffix such as `DOM-02.01` identifies
one task. Ranges mean every intervening package in the linked subsystem file.
M0 through M7 and G1 through G5 are evidence checklists in [milestones](milestones.md),
not duplicate implementation tasks. Each phase heading links to its complete
source scope so exceptions and demonstrations are not redefined here.

## Phase 0: Skeleton

Source scope: [Phase 0: Skeleton](../../plans/stewardship/overall.md#phase-0-reproducible-project-skeleton).

Execution checkpoint (September 8, 2026): the human approved the ARC-02 phase
split now recorded in the controlling plan. See its
[partial completion evidence](architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup).
ARC-01 and DOM-01 evidence is recorded in their owning checklists. The
[OPS-01 scaffold](operations.md#ops-01-development-and-production-compose-topology)
now passes local Compose demonstrations and host/image baseline parity. Reserved
services still refuse startup pending their owning implementation packages.
Phase 0 [OPS-09 CI/coverage](operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline)
and [DOM-05 clock/traceability](campaign-domain.md#dom-05-cross-domain-acceptance-harness)
are implemented for their admitted scope. Eight M0 review/fix rounds and
post-correction validation are complete; see the latest
[milestone evidence](milestones.md#phase-0-skeleton). PR #8 merged through the
human-approved queue on September 8, 2026, with all CI checks passing; M0 is
complete and Phase 1 is authorized. No formal review gate is released. Do not wait for
Phase 1's database-backed ARC-02 integration or mark that work complete early.

1. [ARC-01](architecture.md#arc-01-dependency-decisions-and-package-skeleton) → [DOM-01](campaign-domain.md#dom-01-domain-vocabulary-and-decision-records) → [ARC-02](architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup) → [OPS-01](operations.md#ops-01-development-and-production-compose-topology).
2. Start [OPS-09](operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline) baseline CI/coverage and [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) clock/traceability work.
3. Complete M0 evidence and the scaffold correction pass before Phase 1.

## Phase 1A: Schema and policy

Source scope: [Phase 1A: Schema and policy](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

Execution checkpoint (September 9, 2026 UTC): the human merged TaskRun storage
as PR #16, merge `e5706c8a745d4cd33198918eb006180485be50b9`, after all four CI
checks passed at `4bf013ce69cf9420350accae9247a0d250665627`.
Branch `pr/stewardship-phase-1a` starts at that refreshed `origin/main`.
The human approved larger coherent delivery batches rather than further
single-component foundation PRs. The next batch targets the remaining
dependency-ready Phase 1A schema, lifecycle and authorization foundation in the
ordered packages below. Complete remaining DAT-01 prerequisites before their
consumers; retain explicit later integration ownership instead of claiming
unimplemented runtime/secret/recovery behavior is complete. Internal commits and
tests do not need routine approval. The complete batch gets three review/fix
rounds and human PR merge approval; Gate 1 still reviews the integrated foundation
before Phase 2.

Current batch checkpoint: versioned authorization, provenance, installer security
effects and offline-recovery records/services are implemented together with the
DOM-02 interval prerequisite. See the
[integration boundary and batch rationale](../../guides/stewardship-authorization-foundation.md).
This substantial policy state-machine batch precedes the separate campaign/
lifecycle/schedule state-machine batch; it does not complete Phase 1A. Remaining
DAT-01 secret/runtime integration stays explicitly open. Continue with DAT-02
and remaining DOM-02 after this PR's human-approved merge. The three full-branch
review/fix rounds and final local validation are complete; see the
[batch evidence](milestones.md#authorization-and-recovery-batch).
Human PR merge approval and passing PR CI are required before the next batch.

September 10, 2026: the human merged PR #17 as
`4e8ac93a6ec8a8c835ce1b6854bc78e25ee80b9e`. Branch
`pr/stewardship-campaign-lifecycle` starts from that refreshed `origin/main`.
The campaign configuration/lifecycle-policy batch groups versioned drafts,
schedule revisions, atomic Testing runtime selection and pure lifecycle policy.
See its [boundary](../../guides/stewardship-campaign-foundation.md) and
[evidence](milestones.md#campaign-configuration-and-lifecycle-policy-batch).
DAT-02 remains incomplete; its remaining storage/read-guard work is next before
Phase 1B consumers. No formal review gate has been released.
The batch's three independent review/fix rounds and final local validation are
complete at implementation `1edef35`; see the linked evidence. PR CI and human
merge approval are required before continuing that next batch.

The merged configuration-preparation increment completed three dual-model
review/fix rounds and final CI; see its
[dispositions and validation](milestones.md#configuration-preparation-increment).
The request-intake increment's three-round review/fix cycle is recorded in the
[milestone evidence](milestones.md#configuration-request-intake-increment).
PR #11 merged after all four CI checks passed. The activation increment completed
three independent review/fix rounds, all accepted Medium+ corrections, and full
local validation; see its
[final evidence](milestones.md#configuration-activation-increment).
PR #12 merged with all four checks passing. The secret-request increment gets
its own human-approved PR after three completed independent review/fix rounds,
all accepted Medium+ corrections and passing local validation; see its
[final evidence](milestones.md#secret-request-storage-increment).
PR #13 merged with all four checks passing. The audit-ownership increment has
completed three independent dual-model review/fix rounds and final local
validation; see its [evidence](milestones.md#audit-ownership-increment).
PR #15 merged with all four checks passing. The TaskRun increment completed
three independent dual-model review/fix rounds, final local validation and CI,
and merged as PR #16; see its
[evidence](milestones.md#taskrun-storage-increment). None of these increments
releases the incomplete Phase 1 or Gate 1.

Integrate ARC-02's concrete materializer and database-backed digest/mode checks
with DAT-01. Complete its production prerequisite and recovery verification in
Phase 1C, following the controlling plan; all existing review gates remain.

1. [DAT-01](data.md#dat-01-storage-conventions-and-base-records) → [DOM-02](campaign-domain.md#dom-02-campaign-interval-and-lifecycle-policy).01 interval resolver → [DAT-02](data.md#dat-02-campaign-lifecycle-and-schedule-schema) → remaining [DOM-02](campaign-domain.md#dom-02-campaign-interval-and-lifecycle-policy) policy.
2. [DAT-05](data.md#dat-05-portal-users-and-authorization-policy-records) → [DOM-03](campaign-domain.md#dom-03-authorization-capability-policy); start database-backed [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) factories after [DAT-01](data.md#dat-01-storage-conventions-and-base-records).

September 10, 2026: PR #18 merged as
`9f644b0e1fdef1fb09e009bc1576f979c096f643`. The completion branch
`pr/stewardship-phase-1a-completion` targets **all remaining Phase 1A work** in
one PR, as explicitly requested. Its
[execution checkpoints and integration contracts](../../guides/stewardship-phase-1a-completion.md)
cover DAT-02, persistent DOM-02 integration and initial database-backed DOM-05
builders. Phase 1A implementation and local validation are complete; Gate 1
remains after the integrated Phase 1B/1C foundation.
The implementation scope is now complete: DAT-02.01–.05 and DOM-02.03–.05 are
checked with PostgreSQL evidence, and DAT-01/DAT-05/DOM-03/DOM-05 explicitly
separate their completed Phase 1A portions from later consumers. Four independent
full-branch dual-model review/fix rounds are complete. Round 3's High callback
issue was corrected before round 4, which found no High/Critical issues; all
accepted Medium+ findings are resolved. Final validation passes 1,958 baseline
and 634 PostgreSQL tests, plus all 30 rebuilt-image/Compose checks. CI and human
merge approval are tracked on the associated completion PR. After its merge, the
next dependency-ready batch is **Phase 1B**, starting with ARC-03; do not create
another Phase 1A foundation increment.

## Phase 1B: Identity and web foundations

Source scope: [Phase 1B: Identity and web foundations](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

September 10, 2026: PR #19 merged at
`6fd21eb586a635333be9f55fc7db9caa39284e60`. The branch
`pr/stewardship-phase-1b` starts at that tip and carries the complete Phase 1B
assignment as one coherent PR. Its [execution checkpoints](../../guides/stewardship-phase-1b.md)
track implementation and validation; Gate 1 is not released.

Current implementation SHA: `120e10552b1e51d1810f58fec23148eed65bf17a`.
All Phase 1B code scope and its three rounds of review
corrections are implemented. The third review had no High/Critical findings.
Final integrated validation passes. The owner approved the secure-link
audit-retention clarification, closing the three-round review exit. See the
[review ledger](../../guides/stewardship-phase-1b-reviews.md#round-three).
Do not begin Phase 1C until PR CI and human-approved merge are complete.

1. [ARC-03](architecture.md#arc-03-django-web-foundation-and-security-middleware) → [ARC-04](architecture.md#arc-04-google-identity-authorization-sessions-and-denial-paths) with [ADM-01](admin-portal.md#adm-01-login-denial-and-unconfigured-state-routing) integration.
2. [DAT-04](data.md#dat-04-family-campaign-identity-and-credentials) credential/schema foundation → [ARC-05](architecture.md#arc-05-family-code-token-and-family-session-security).
3. [ARC-06](architecture.md#arc-06-enforceable-cryptographic-service-boundary) → [ARC-07](architecture.md#arc-07-application-level-privacy-and-audit-primitives) → initial [ARC-08](architecture.md#arc-08-performance-accessibility-and-compatibility-baseline) and [DOM-04](campaign-domain.md#dom-04-shared-presentation-and-client-contracts).

## Phase 1C: Runtime foundation

Source scope: [Phase 1C: Runtime foundation](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

PR #20 merged at `18a37cb5b6c90bbf2b5f60c5fff37f199cd52201` after passing
PR and merge-queue CI. The owner authorized continuing directly into Phase 1C.
Branch `pr/stewardship-phase-1c` starts at that refreshed `origin/main` tip;
its [execution evidence](../../guides/stewardship-phase-1c.md) tracks the complete
batch. Implementation, review and native Linux CI validation are complete.
Human merge/Gate 1 release remains required, with passing CI on the final PR head.

The operational implementation and its native-volume/production-shaped tests
are present. Round one reviewed this phase; round two reviewed the complete
Phase 1 diff from `509245d`, and round three repeated that cumulative scope.
Both vendors completed all three reviews successfully; round three found no
High/Critical issues. All retained findings have corrections or evidence-backed
rejections. Corrected implementation `e3fed5c`, with the fixture-only CI fixes
through `243782d`, passes 2,710 baseline tests,
991 PostgreSQL tests, 48 rebuilt-container checks and 75 browser checks, with
92.45% line and 84.50% branch coverage. See the
[review ledger](../../guides/stewardship-phase-1c-reviews.md) for the single
Phase 1C [PR #21](https://github.com/epiphany40223/parishkit/pull/21) handoff.
All native Linux CI jobs pass at `243782d`; final-head CI and human approval
remain mandatory before merging or beginning Phase 2.
Do not start DAT-03 until Gate 1 and the phase PR receive human approval.

1. [OPS-02](operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets) → [OPS-03](operations.md#ops-03-production-ingress-tls-and-network-security) → [OPS-04](operations.md#ops-04-bootstrap-migrations-startup-and-upgrades) → baseline [OPS-08](operations.md#ops-08-observability-health-and-operational-runbooks).
2. Complete M1 evidence and G1 before beginning Phase 2.

## Phase 2: Source, setup, and preparation

Source scope: [Phase 2: Source, setup, and preparation](../../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation).

September 11, 2026: PR #21 merged as
`48be3666f0c89cc15586cb67465cd1ba0504203c` after passing merge-queue CI. The owner
authorized automatic continuation upon that merge, releasing Gate 1. Branch
`pr/stewardship-phase-2` starts from the refreshed `origin/main` tip and targets
the complete Phase 2 batch below, not a separate PR per storage component.
Its [execution checkpoints](../../guides/stewardship-phase-2.md) track scope,
tests and the required three-round review/fix cycle. DAT-03 storage and the
background/source pipeline, complete initial setup and Admin campaign preparation
have been implemented. Integrated validation and three full-phase review/fix
rounds pass at corrected implementation `ea2d5cb`; the final round found no
High/Critical issues and all retained findings are resolved. The
[acceptance index](../../guides/stewardship-phase-2-acceptance.md) records all
demonstrations, test counts and coverage. DAT-03, BG-01, BG-05, ADM-02, ADM-03,
ADM-04 and M2 are complete for this phase; DAT-04/DAT-05 retain only their named
later consumers. PR #22's post-consolidation CI passes, and the
[supplemental review gate](../../guides/stewardship-phase-2-consolidation-review.md)
is complete with a second successful dual-source round, no High/Critical
findings and passing correction tests. The authorized merge landed on
`origin/main` as recorded in Phase 3A below, after final-head and merge-group CI
passed. The pre-consolidation SHAs above remain historical evidence, not
acceptance of subsequent corrections.

1. [DAT-03](data.md#dat-03-versioned-parishsoft-source-corpus) → [BG-01](background-processing.md#bg-01-durable-task-scheduler-lease-and-recovery-substrate) → [BG-05](background-processing.md#bg-05-parishsoft-delta-and-full-refresh) → [ADM-02](admin-portal.md#adm-02-bootstrap-command-and-transactional-setup-wizard) → [ADM-03](admin-portal.md#adm-03-navigation-dashboard-indicators-and-configuration) → [ADM-04](admin-portal.md#adm-04-campaign-editor-content-schedules-and-previews).
2. Complete source-promotion integration for [DAT-04](data.md#dat-04-family-campaign-identity-and-credentials) population and [DAT-05](data.md#dat-05-portal-users-and-authorization-policy-records) chair suggestions.
3. Complete M2 evidence and the focused setup/import correction pass.

## Phase 3A: Minimal Family slice

Source scope: [Phase 3A: Minimal Family slice](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

September 13, 2026: PR #22 merged as
`7b2b1dd4478ae4014b167d6c0c203127c9a0ceb9`, verified on refreshed `origin/main`.
Final-head and merge-group CI passed, and the completed supplemental reviews
closed Phase 2's correction gate. Branch `pr/stewardship-family-response` starts
from that tip. The [Phase 3A checkpoint](../../guides/stewardship-phase-3a.md)
tracks the minimal Family response increment, beginning with DAT-06's trusted
baseline/submission contract. Gate 2 remains open until Phase 3B completes its
integrated review; this branch does not include all remaining Family modules.

The minimal flow and DOM-05 executable scenario are implemented, including
restricted-role SQL guards, source reconciliation, real connection races,
mobile browser navigation and final-submit/revisit handling. All three
dual-model review rounds are complete; round three had no High/Critical
findings and both Medium corrections have passing regression tests. See the
[review ledger](../../guides/stewardship-phase-3a-reviews.md#round-3).
Final targeted local validation passes, including the complete 492-test browser
suite. The ledger retains the earlier broad database run's clock-regression
failure and passing recheck. PR #23 then passed clean final-head CI, including
all 2,220 database tests and coverage of 93.93% statements / 85.18% branches,
and merged through the protected queue as
`4051b4a5250cdbfa4a8f41d23d9fab800f252b84` on September 13, 2026.
The owning checklists retain partial scope for packages whose
remaining fields, modules or downstream consumers belong to later increments.

1. [DAT-06](data.md#dat-06-immutable-submissions-and-proposal-overlay) → submission/follow-up slice of [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) → [DAT-08](data.md#dat-08-merge-and-source-reconciliation-services).
2. [FAM-01](parishioner-portal.md#fam-01-availability-code-entry-and-secure-link-exchange) → [FAM-02](parishioner-portal.md#fam-02-in-memory-form-engine-and-navigation) → minimal [FAM-03](parishioner-portal.md#fam-03-family-census-step) and [FAM-06](parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit) response flow.
3. Land the first executable [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) Family scenario before expanding fields.

## Phase 3B: Complete Family flow

Source scope: [Phase 3B: Complete Family flow](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

Branch `pr/stewardship-family-census` starts at the verified PR #23 merge on
refreshed `origin/main`. Its first coherent outcome is the FAM-03 household
census step; see the [increment checkpoints](../../guides/stewardship-family-census.md).
The controlling plan records why remaining Member and stewardship-module
work follows separately. Gate 2 is not released by this subdivision.

The household implementation and its three review/correction rounds are
complete with [recorded evidence](../../guides/stewardship-family-census-reviews.md);
PR #24's final-head and merge-group CI passed and its protected merge is
verified on `origin/main`. FAM-03.01 through .04 are
complete. The remaining FAM-03.05 non-census omission integration accompanies
FAM-05.06 rather than blocking the next Member-census increment.

Branch `pr/stewardship-member-census` starts from that merged tip, `5c478e8`.
Its [bounded scope](../../guides/stewardship-member-census.md) is existing
Members' non-terminal census fields, including end-to-end validation and
revisit. Implementation, local validation and three dual-model review/correction
rounds are complete; final-head and merge-group CI passed, and PR #25 merged
as `eeb3463`, verified on `origin/main`.
Branch `pr/stewardship-member-requests` starts from that exact tip. Its
[increment checkpoints](../../guides/stewardship-member-requests.md) cover
terminal semantics and proposed Members. FAM-04 implementation and local
acceptance are complete, including three dual-model review/fix rounds with
[recorded evidence](../../guides/stewardship-member-requests-reviews.md).
PR #26 passed final-head CI and merged through the protected queue as
`047f0f4`, verified on `origin/main`. Branch `pr/stewardship-ministry-responses`
starts there; its [increment checkpoints](../../guides/stewardship-ministry-responses.md)
cover FAM-05 Ministry choices, roster resolution and non-census omission.
The Ministry implementation, three review/fix rounds and final-head/merge-group
CI passed. PR #27 merged as `6e493657`, verified on `origin/main`; see its
[delivery ledger](../../guides/stewardship-ministry-responses-reviews.md#final-ci-and-protected-merge).
Before financial work, a small [OPS-09 browser-CI increment](../../guides/stewardship-browser-ci.md)
reduces the measured 15–17-minute browser critical path while retaining the
complete suite and protected aggregate check. Branch
`pr/stewardship-browser-ci` starts at the PR #27 merge. Its implementation,
five dual-source review/fix rounds and local validation are complete, including
the container-baseline timeout and oversized-log corrections. PR #28 passed
final-head and protected merge-group CI and merged as
`c0ab9a1259c6a2c459b6568917e2da56278f061b`, verified on `origin/main`.
The [financial-response increment](../../guides/stewardship-financial-responses.md)
starts from that merge. FAM-05.03–.06 implementation and three dual-model
review/fix rounds are complete, with all accepted Medium+ findings corrected.
The linked ledger records local validation and the audited schema-fingerprint
correction. PR #29 passed final-head and complete merge-group CI and merged as
`e406ecfa5af136aa06b67e5461bc1389360b2a8b`, verified on refreshed `origin/main`.
Branch `pr/stewardship-family-acceptance` starts at that tip. Its
[integrated acceptance scope](../../guides/stewardship-family-acceptance.md) closes the remaining
FAM-01/02/06/07 evidence, exercises representative FAM-08, completes the Phase 3
demonstration, and reviews the entire Gate 2 scope before any Phase 4 work.

Local acceptance is complete at `44455b5`: FAM-01–07 and DAT-06/08's Family
service scope pass five dual review rounds and complete baseline/database/browser
validation. The [final evidence](../../guides/stewardship-gate-2-reviews.md#final-correction-review-and-passing-local-gate)
retains all failure/correction history and later-phase boundaries. Protected
delivery, G2.06 and the Phase 4 release remain pending exact-head and complete
merge-group CI plus verified main ancestry.

Completion: [PR #30](../../guides/stewardship-gate-2-reviews.md#protected-delivery)
merged as `6c8cd512e5121095961ffbeb3f80f4dfd844043c` after seven successful
dual-source rounds, final-head CI and every merge-group job passed. The merge
is verified on `origin/main`; the earlier pending delivery checkpoint above is
superseded. M3.05/G2.06 are complete and Phase 4 is dependency-ready.

1. Finish [FAM-03](parishioner-portal.md#fam-03-family-census-step); complete [FAM-04](parishioner-portal.md#fam-04-existing-and-proposed-member-steps), [FAM-05](parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps), [FAM-06](parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit), and [FAM-07](parishioner-portal.md#fam-07-repeat-visits-and-source-change-merge).
2. Exercise representative [FAM-08](parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion) cases continuously.
3. Complete [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) follow-up derivation and required [RPT-02](reports.md#rpt-02-population-and-calculation-library) calculations.
4. Complete M3 evidence and G2 before starting Production mail work.

## Phase 4: Production scheduling and delivery

Source scope: [Phase 4: Production scheduling and delivery](../../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).

Completed increment: `pr/stewardship-delivery-journal` starts at the verified
PR #30 merge. Begin DAT-07's durable outbox and Production-transition journal
services with their state, ownership, scrubbing and concurrency tests. This is
a coherent delivery-state foundation, not a PR per model. Its
[scope and checkpoints](../../guides/stewardship-delivery-journal.md) preserve the
later cleanup/scheduler/dispatch/UI owners and Gate 3 restrictions. No Phase 4
task is yet claimed wholly complete. The journal slice now has three successful
dual-source review/fix rounds and passing local regression/schema evidence in
its [review ledger](../../guides/stewardship-delivery-reviews.md#third-correction-review).
Final-head CI and complete merge-group validation subsequently passed, and
PR #31 merged as `f4e000c5b3f7024e47c7f6d3dbdd30c6cd4976e1`, verified on
refreshed `origin/main`. The next branch, `pr/stewardship-campaign-boundaries`,
starts at that exact tip. Its [scope and checkpoints](../../guides/stewardship-campaign-boundaries.md)
cover BG-02's Phase 4 start/close behavior; BG-03/04 continue afterward in the
order below. Restore/reopen token preparation and Gate 3 remain later work.

Boundary delivery checkpoint: BG-02.01/.02/.04/.05 and BG-02.03's end-date
replacement portion pass local acceptance and three dual-source review/fix
rounds. The [boundary review ledger](../../guides/stewardship-campaign-boundaries.md#review-round-3)
records exact heads, raw severities, dispositions and validation. BG-02.03
remains unchecked for its Phase 6 token worker. All 24 final-head PR CI jobs
and all 24 merge-group jobs passed. PR #32 merged as
`5c85d26ff586cad5539ff2c324c89cd621cbcf9d`, verified on refreshed `origin/main`.
BG-03 on `pr/stewardship-production-cleanup` now passes local acceptance and
three dual-source review/fix rounds. Its
[scope and checkpoints](../../guides/stewardship-production-cleanup.md) retain
the later readiness, mail and activation owners. Final-head PR and protected
merge-group CI subsequently passed, all 24 jobs in each run. PR #33 merged as
`ce1e95d129646bae4d3f6fe2acdb0ad8dfd7767c`, verified on refreshed `origin/main`.
The next branch, `pr/stewardship-schedule-planning`, begins BG-04's ordinary
schedule planning and reconciliation; see its
[scope and checkpoints](../../guides/stewardship-schedule-planning.md).
BG-04.01/.03 now pass local acceptance and three completed dual-source review/fix
rounds; final-head and merge-group CI remain pending. Bounded activation catch-up
and durable digest recovery follow before BG-04 completion. This checkpoint
does not release Gate 3 or enable mail dispatch.

Delivery update: PR #34 merged as `db8aee09` after all 24 final-head and all
24 merge-group jobs passed. The merge is verified on refreshed `origin/main`.
Branch `pr/stewardship-activation-catchup` now continues the dependency-ready
[bounded recovery increment](../../guides/stewardship-activation-catchup.md).
Earlier pending-CI notes are superseded; the remaining Phase 4 owners and Gate 3
stay in force.

PR #35 subsequently merged as `e1e575dd` after all 24 final-head and all
24 merge-group jobs passed; its merge is verified on refreshed `origin/main`.
Branch `pr/stewardship-export-foundation` now implements the dependency-ready
[BG-08 chart/export substrate](../../guides/stewardship-export-foundation.md).
Full report workflows, calculation materialization, remaining Phase 4 owners
and Gate 3 remain open.

PR #36 subsequently merged as `7d9a3b0` after all 24 final-head and all
24 protected merge-group jobs passed, verified on refreshed `origin/main`.
Branch `pr/stewardship-family-deliverability` continues BG-06's
[recipient and recovery preparation](../../guides/stewardship-family-deliverability.md).
The earlier export pending-delivery checkpoint is superseded; the remaining
Phase 4 owners, later full report workflows and Gate 3 remain open.
Recipient/recovery preparation has now completed its three dual-source review/
fix rounds and local validation; protected PR CI/merge remains pending. Continue
with the guide's deferred BG-06 rendering/dispatch owners only after its merge
is verified on refreshed `origin/main`.

PR #37 has now merged as `0a4313e5`, with all 24 exact-head and all 24 protected
merge-group jobs passing, verified on freshly fetched `origin/main`.
`pr/stewardship-family-mail-preparation` implements the next coherent
[personalized outbox/credential slice](../../guides/stewardship-family-mail-preparation.md).
No Production activation or Gate 3 release is inferred from preparation.

The preparation slice has completed three dual-source review/fix rounds and
full local validation, recorded in its
[handoff evidence](../../guides/stewardship-family-mail-preparation.md#final-local-validation-and-handoff).
Deliver it through exact-head CI and the protected queue, verify its merge on
fresh `origin/main`, then continue the guide's provider-dispatch increment
without a routine human-approval stop.

PR #39's provider-dispatch increment has now merged as `9e5b6e99` after all
24 exact-head and 24 protected merge-group jobs passed, verified on refreshed
`origin/main`. Continue the dependency-ready
[Admin-resolution increment](../../guides/stewardship-family-mail-resolution.md)
before BG-07; BG-06 and Gate 3 remain open.

PR #40's Admin-resolution increment merged as `737be049` after all 24
exact-head jobs (run `35129776022`) and all 24 protected merge-group jobs
(run `35131554150`) passed. Its merge is verified on freshly fetched
`origin/main`. Branch `pr/stewardship-report-facts` starts at that merge and
implements the dependency-ready RPT-02/RPT-03 calculation/materialization
prerequisite below. BG-07, the full report UI, and Gate 3 remain open.

The [ordinary report-fact increment](../../guides/stewardship-report-facts.md)
landed in PR #41 as `2eade2a5` after three dual-source review/fix rounds,
all 24 exact-head jobs plus DCO (run `35141293392`), and all 24 protected
merge-group jobs (run `35143121665`). Fresh `origin/main` was verified.
The [consumer increment](../../guides/stewardship-report-selection.md) landed
in PR #42 as `c72b8bdb` after three dual-source review/fix rounds, all 24
exact-head jobs plus DCO (run `35149391133`) and all 24 protected merge-group
jobs (run `35151238864`). Fresh `origin/main` was verified. Branch
`pr/stewardship-exact-report-facts` starts there and implements the next
[queued exact-export increment](../../guides/stewardship-exact-exports.md).
Scheduled verification and remaining RPT-02 statistics still precede BG-07.
BG-07 and Gate 3 remain open; no complete RPT-03 task is claimed by these
partial increments.

PR #43 merged as `053eed78` after all 24 exact-head jobs plus DCO
(run `35159793642`) and all 24 protected merge-group jobs (run `35161289648`)
passed. The merge was verified on freshly fetched `origin/main`.
Branch `pr/stewardship-fact-verification` starts at that merge and implements
the [scheduled verification increment](../../guides/stewardship-fact-verification.md).
Implementation, three successful dual-source review/fix rounds and local
validation are complete; exact-head CI/DCO and protected queue delivery follow.
The remaining Phase 4 prerequisites and Gate 3 remain open.

PR #44 subsequently merged as `80754a49` after all 24 exact-head jobs plus DCO
(run `35173275295`) and all 24 protected merge-group jobs (run `35174469269`)
passed. The merge is verified on freshly fetched `origin/main`. Branch
`pr/stewardship-campaign-statistics` now implements the
[statistics calculation increment](../../guides/stewardship-campaign-statistics.md)
before BG-07. Implementation, local validation and three successful dual-source
review/fix rounds are complete; final-head CI/DCO and protected delivery remain
pending. The later report UI and Gate 3 are not released.

PR #45 subsequently merged as `3bfec17a` after all 24 exact-head jobs plus DCO
and all 24 protected merge-group jobs passed. Its guide's
[delivery receipt](../../guides/stewardship-campaign-statistics.md#protected-delivery)
supersedes that pending note. From verified fresh `origin/main`,
`pr/stewardship-submission-receipts` now implements BG-07.01's coherent
[submission-confirmation increment](../../guides/stewardship-submission-receipts.md).
Implementation, local validation and three successful dual-source review/fix
rounds are complete; exact-head CI/DCO and protected delivery remain pending.
Daily/weekly digest ownership and the remaining Phase 4/Gate 3 work stay open.

PR #46 subsequently merged as `849cc71f` after all exact-head and protected
merge-group checks passed. The
[delivery receipt](../../guides/stewardship-submission-receipts.md#protected-delivery)
supersedes its pending note. `pr/stewardship-daily-digests` starts from that
verified fresh tip and now implements the
[daily digest increment](../../guides/stewardship-daily-digests.md).
Its implementation, five successful dual-source review/fix rounds and
[final local acceptance](../../guides/stewardship-daily-digests.md#final-local-acceptance)
are complete; exact-head CI/DCO and protected delivery remain pending. BG-07.02
is checked for its admitted scope, not the whole BG-07 package. After delivery,
BG-07.03's weekly information/correction digest is the next coherent increment;
the remaining Phase 4 owners and Gate 3 stay open.

PR #47 subsequently merged as `e548809c` after all exact-head CI/DCO and
protected merge-group checks passed. Its
[delivery receipt](../../guides/stewardship-daily-digests.md#protected-delivery)
supersedes that pending note. From verified fresh `origin/main`,
`pr/stewardship-weekly-digests` now begins the coherent
[weekly information/correction digest increment](../../guides/stewardship-weekly-digests.md).

Its rendering, capture, per-Admin allocation/replacement and maintained
preparation/interval-completion checkpoints are implemented and tested.
The [execution evidence](../../guides/stewardship-weekly-digests.md#execution-evidence)
tracks the remaining delivery/UI/cleanup integration and review acceptance;
the weekly task and Phase 4 remain in progress.

The weekly implementation and four successful dual-source review/fix rounds
are now complete; the final round found no High issues. The
[review ledger](../../guides/stewardship-weekly-digests.md#review-round-four-and-handoff)
records all accepted Medium-or-higher dispositions and focused correction
tests. BG-07.03 is complete for its weekly scope; BG-07.04/.05 and Gate 3 remain
open. Final-head CI/DCO and protected delivery on
[PR #48](https://github.com/epiphany40223/parishkit/pull/48) are still required.
Do not infer merge, deployment or release from this implementation checkpoint.

PR #48 subsequently merged as `8998542b`, verified on refreshed `origin/main`.
Its [delivery receipt](../../guides/stewardship-weekly-digests.md#protected-delivery)
supersedes the pending note above. The human disabled the merge queue; future
PRs use the updated protected auto-merge cycle without duplicate queue CI.
Before BG-10, a bounded test-maintenance increment addresses the requested
[compatible fixture reuse](../../guides/stewardship-test-efficiency.md#compatible-database-fixture-reuse).
It does not advance feature acceptance or Gate 3.

That maintenance increment subsequently auto-merged in
[PR #49](https://github.com/epiphany40223/parishkit/pull/49) as `70797cb2` after
three successful dual-source review/fix rounds and all 25 exact-head CI/DCO
checks passed. Its [delivery evidence](../../guides/stewardship-test-efficiency.md#round-three-and-protected-delivery)
records unchanged coverage and the measured log-copy saving without claiming
the remaining database-test bottleneck is solved. From the verified fresh tip,
`pr/stewardship-operational-alerts` is the next BG-10 working branch. BG-10 is
not yet complete, and Gate 3 has not passed.

PR #50 subsequently auto-merged as `1895949`, verified on refreshed
`origin/main`, after three successful dual-source rounds and all 25 exact-head
CI/DCO checks passed. Its [protected delivery receipt](../../guides/stewardship-operational-alerts.md#protected-delivery)
supersedes pending notes. The [remaining BG-10 increment](../../guides/stewardship-operational-health.md)
starts from that tip on `pr/stewardship-operational-health`; Slack ownership,
remaining health producers and shutdown acceptance precede ADM-05.
For reviewability, PR #51 delivers independent Slack, notification-shutdown
evidence and adjacent hold corrections, alongside its separate test-efficiency
checkpoint. Current-phase health sampling/recovery follows from verified main
in the next coherent increment; BG-10 remains unchecked until that completion.

PR #51 has now auto-merged as `fb945fb9`, verified on refreshed `origin/main`,
after all 24 final-head CI jobs plus DCO and the required reviews passed.
Its [protected receipt](../../guides/stewardship-operational-health.md#protected-delivery)
supersedes pending delivery notes. The
[health-observation increment](../../guides/stewardship-health-observation.md)
starts on `pr/stewardship-health-observation` from that exact tip and owns the
remaining current-phase BG-10 observations/recovery before ADM-05.

PR #52 has now merged as `08367cb7`, verified on refreshed `origin/main`, after
three successful dual-source rounds and all 24 exact-head CI jobs plus DCO.
Its [protected receipt](../../guides/stewardship-health-observation.md#protected-delivery)
supersedes pending delivery notes. The [periodic-health increment](../../guides/stewardship-periodic-health.md)
starts from that tip and closes the quiet-traffic authentication recovery gap,
with separate measured CI improvements. Broader source/provider/due-work health
observations still precede BG-10 completion and ADM-05.

PR #53 has merged as `113fcd0f`, verified on refreshed `origin/main`, with all
28 exact-head CI jobs, DCO and three successful review/fix rounds. Its
[protected receipt](../../guides/stewardship-periodic-health.md#protected-delivery)
records actual timing and coverage. The bounded
[CI bootstrap-reuse increment](../../guides/stewardship-ci-bootstrap-reuse.md)
now removes repeated setup and verified duplicate assertions while preserving
all scenarios, before resuming broader BG-10 health producers.

PR #54 subsequently merged as `17d5d4d9`, verified on refreshed `origin/main`.
Its [protected receipt](../../guides/stewardship-ci-bootstrap-reuse.md#protected-delivery)
records the completed reviews, exact-head CI and human-authorized check-name
update. The [source-health increment](../../guides/stewardship-source-health.md)
now resumes BG-10 feature work; provider/due-work health remains separately open.

PR #55 subsequently merged as `7b5c43b6`, verified on refreshed `origin/main`.
Its [protected delivery receipt](../../guides/stewardship-source-health.md#protected-delivery)
supersedes pending review/CI notes. The [mail-health increment](../../guides/stewardship-mail-health.md)
now owns provider alerts and actual-success recovery; due-work health and final
BG-10 acceptance remain before ADM-05.

PR #56 merged as `96a80fc2`, verified on refreshed `origin/main`; its
[protected receipt](../../guides/stewardship-mail-health.md#protected-delivery)
records all three review/fix rounds, final-head CI/DCO and consolidated tree.
The [due-work increment](../../guides/stewardship-due-work-health.md) now owns
remaining current-phase service observations before BG-10 acceptance and ADM-05.

Current-phase BG-10 implementation and three due-work review/fix rounds are
complete; its [acceptance map](../../guides/stewardship-due-work-health.md#current-phase-bg-10-acceptance-map)
retains later producer/operations owners. After final-head CI/DCO and protected
PR #57 delivery, begin ADM-05 from verified fresh main. This does not release
Gate 3 or enable real provider delivery.

PR #57 subsequently merged as `17f5f2fc`, verified on refreshed `origin/main`.
Its [protected receipt](../../guides/stewardship-due-work-health.md#protected-delivery)
supersedes pending delivery. The [ADM-05 readiness/cleanup increment](../../guides/stewardship-go-live-readiness.md)
starts from that tip; activation/withdrawal and the final Phase 4 handoff follow
before Gate 3. No whole ADM-05 task is claimed by this initial checkpoint.

ADM-05.01/.02 now pass implementation and three dual-source review/fix rounds;
the [acceptance map](../../guides/stewardship-go-live-readiness.md#acceptance-and-delivery-boundary)
records the readiness and guarded cleanup evidence. Final-head CI/DCO and
protected PR #58 delivery precede a fresh-main activation/withdrawal increment.
ADM-05.03/.04/.05, delivery-pause and integrated Gate 3 acceptance remain open.

PR #58 subsequently passed corrected-head CI/DCO and merged as `47ec3599`,
verified on refreshed `origin/main`. Its [protected delivery receipt](../../guides/stewardship-go-live-readiness.md#protected-delivery)
supersedes pending delivery. The [activation/withdrawal increment](../../guides/stewardship-production-activation.md)
now starts from that tip; ADM-05.03/.04/.05 remain in progress, not complete.
PR #59 first delivers the [inactive-link Admin workflow](../../guides/stewardship-production-activation.md#delivery-boundary)
as a smaller reviewable slice. After its reviewed protected merge, resume the
remaining final-readiness/activation/withdrawal checkpoints from fresh main;
do not advance to delivery pause or claim the ADM-05 package complete yet.

PR #59 passed all three dual-source rounds and final-head CI/DCO, then merged
as `880507fc`; its [protected receipt](../../guides/stewardship-production-activation.md#protected-delivery)
records verification on `origin/main`. Continue the
[final-confirmation increment](../../guides/stewardship-production-confirmation.md)
from that tip on `pr/stewardship-production-confirmation`. Withdrawal remains the
following coherent increment, with all original load/race and gate criteria.

PR #60 completes ADM-05.03 implementation and three dual-source review/fix rounds;
see its [delivery boundary](../../guides/stewardship-production-confirmation.md#delivery-boundary).
Wait for corrected-head CI/DCO and protected merge, verify refreshed main, then
begin ADM-05.04 pre-start withdrawal and the remaining ADM-05.05 acceptance.
Do not infer complete ADM-05 or Gate 3 from final confirmation alone.

PR #60 has merged as `6bc3238`, verified on refreshed `origin/main` after all
25 final-head CI/DCO checks passed. The [pre-start withdrawal increment](../../guides/stewardship-production-withdrawal.md)
starts from that tip and retains the outstanding ADM-05.04/.05 acceptance.

PR #61 completes ADM-05.04/.05 implementation and its three-round dual-source
review/fix exit; see [withdrawal acceptance](../../guides/stewardship-production-withdrawal.md#review-round-3-and-acceptance).
After final-head CI/DCO and protected merge, verify refreshed `origin/main`,
then begin the ADM-06 delivery-pause slice. Phase 4's remaining service/queue
handoff and M4 evidence still follow; this does not release integrated Gate 3.

PR #61 merged as `4be1ce09`, verified on refreshed `origin/main` after all
25 final-head CI/DCO checks passed. Continue the
[delivery-pause increment](../../guides/stewardship-delivery-pause.md) from that
tip on `pr/stewardship-delivery-pause`; retain the full Phase 4 and gate boundaries.

PR #62 completes the delivery-pause implementation and its three dual-source
review/fix rounds. The [acceptance and M4 service map](../../guides/stewardship-delivery-pause.md#acceptance-and-delivery-boundary)
records current-phase scope, exact regression evidence and remaining later
owners. After final-head CI/DCO and protected merge, verify refreshed main,
record the M4 release, and begin the Phase 5 shared reporting/participation UI
on a new topic branch. Do not infer integrated Gate 3 or production approval.

1. Finish [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) job/outbox records; implement [BG-02](background-processing.md#bg-02-campaign-boundary-occurrences) → [BG-03](background-processing.md#bg-03-production-transition-cleanup-worker) → [BG-04](background-processing.md#bg-04-schedule-revision-fulfillment-and-mode-routing).
2. Begin [BG-08](background-processing.md#bg-08-export-and-graph-workers) chart/export substrate, then implement [BG-06](background-processing.md#bg-06-family-invitations-and-reminders).
3. Complete required [RPT-02](reports.md#rpt-02-population-and-calculation-library) calculations and the [RPT-03](reports.md#rpt-03-participation-graph-and-campaign-statistics) fact-materialization service before [BG-07](background-processing.md#bg-07-submission-confirmations-and-admin-digests); report UI remains in Phase 5.
4. [BG-07](background-processing.md#bg-07-submission-confirmations-and-admin-digests) → [BG-10](background-processing.md#bg-10-critical-notification-and-service-shutdown) → [ADM-05](admin-portal.md#adm-05-production-transition-and-pre-start-withdrawal) → delivery-pause slice of [ADM-06](admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive).
5. Recheck [ARC-06](architecture.md#arc-06-enforceable-cryptographic-service-boundary), [OPS-01](operations.md#ops-01-development-and-production-compose-topology), and [OPS-02](operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets) against actual service/queue needs; record M4 evidence.
6. Keep the master plan's fake/disposable-environment restrictions until G3.

Before enabling ADM-05 direct activation, verify the DAT-02 demand, BG-04
bounded catch-up, and BG-06/BG-07 preparation-hold integration and load/recovery
evidence required by the master plan's Phase 4 handoff.

## Phase 5: Reports and staff workflows

Source scope: [Phase 5: Reports and staff workflows](../../plans/stewardship/overall.md#phase-5-reports-exports-users-and-follow-up).

Execution checkpoint (September 19, 2026 UTC): PR #62 merged as `2c3151e6`
after all 25 final-head CI/DCO checks passed; its
[protected receipt](../../guides/stewardship-delivery-pause.md#protected-delivery)
releases M4. Branch `pr/stewardship-report-workspace` starts at that verified
`origin/main` tip. Begin the [shared report workspace increment](../../guides/stewardship-report-workspace.md)
with Admin/Staff participation/statistics and native export controls, reusing
the delivered calculations and workers. Full report-catalog, Ministry scoping,
follow-up and integrated Gate 3 acceptance remain open.

The workspace now completes RPT-03.01/.03/.04/.05 with three completed
dual-source review/fix rounds and focused passing validation; its
[review ledger](../../guides/stewardship-report-workspace-reviews.md#round-3)
records the final admission corrections. PR #63 remains pending final-head
delivery. The human-prioritized CI feedback increment is isolated in PR #64;
verify that merge, rebase the preserved reporting branch, and complete its
protected CI/merge before starting native queued-exact waiting/regeneration
controls on the next fresh-main branch. No integrated gate is released here.

That pending-delivery checkpoint is superseded: PR #64 merged as `07f80e5b`
and PR #63 as `351cf375`, both with full final-head CI/DCO. The
[workspace protected receipt](../../guides/stewardship-report-workspace.md#protected-delivery)
records the exact candidates and runs. Branch `pr/stewardship-exact-export-ui`
starts at verified `351cf375` and implements the
[native exact-export and regeneration increment](../../guides/stewardship-exact-export-ui.md).
Focused PostgreSQL, browser and unit validation and
[three dual-source review rounds](../../guides/stewardship-exact-export-ui-reviews.md#round-3)
pass; PR #65 still requires protected final-head delivery. The report catalog
and integrated Gate 3 remain open.

PR #65 has now merged as `b60e39f4`, with full exact-head CI and DCO; its
[protected receipt](../../guides/stewardship-exact-export-ui.md#protected-delivery)
supersedes the pending checkpoint. Branch `pr/stewardship-additional-followup`
starts at that verified fresh-main tip. The
[Staff queue/history/editing increment](../../guides/stewardship-additional-followup.md)
completes RPT-04.01/.02/.03/.05 and three dual-source review/fix rounds;
protected delivery remains pending in PR #66. RPT-04.04 async exports follow
separately. No complete
catalog, M5 or Gate 3 acceptance is claimed.

PR #66 subsequently merged as `6a636680`, verified on refreshed `origin/main`,
after all 24 corrected-head CI jobs plus DCO passed. Its
[protected receipt](../../guides/stewardship-additional-followup.md#protected-delivery)
supersedes that pending note. Branch `pr/stewardship-information-exports` starts
there and implements [RPT-04.04 complete-text/history exports](../../guides/stewardship-information-exports.md)
through the shared asynchronous report pipeline.

This increment now completes RPT-04.04 with focused acceptance and
[three dual-source review/fix rounds](../../guides/stewardship-information-export-reviews.md).
PR #67 is awaiting full corrected-head CI/DCO and protected delivery. Verify
its merge on refreshed main before the next coherent RPT-05 Family-code and
postal-outreach increment. M5 and Gate 3 remain open.

PR #67 subsequently merged as `75a20c0a` after full exact-head CI/DCO passed;
its [protected receipt](../../guides/stewardship-information-exports.md#protected-delivery)
supersedes the pending checkpoint. Branch `pr/stewardship-family-directories`
starts from that verified main tip for the [RPT-05 interactive directories](../../guides/stewardship-family-directories.md).
PR #68 implements .01/.02/.04 and interactive .05 with three completed
[review/fix rounds](../../guides/stewardship-family-directory-reviews.md).
Full exact-head CI/DCO and protected merge remain pending. After verifying
that merge on refreshed main, continue with RPT-05.03 complete-result directory
exports and remaining .05 export tests; RPT-05/M5/Gate 3 are not yet complete.
PR #68 subsequently merged as `8dc00e9c` after all 24 full exact-head CI jobs
and DCO passed; see its [protected receipt](../../guides/stewardship-family-directories.md#protected-delivery).
Fresh branch `pr/stewardship-directory-exports` starts from that verified main
tip for [complete-result directory exports](../../guides/stewardship-directory-exports.md).
Complete-result exports follow separately; neither this branch nor its
predecessor releases the incomplete report catalog or Gate 3.

PR #69 now completes remaining RPT-05.03/.05 implementation and focused
acceptance, with [three completed dual-source review/fix rounds](../../guides/stewardship-directory-export-reviews.md).
After full exact-head CI/DCO and protected delivery, verify refreshed main and
begin RPT-06 scoped Ministry reporting. M5 and Gate 3 remain open.

PR #69 subsequently passed all 24 full exact-head jobs plus DCO and merged as
`c64a9662`; its [protected receipt](../../guides/stewardship-directory-exports.md#protected-delivery)
records the correction review and final candidate. The
[scoped interactive Ministry increment](../../guides/stewardship-ministry-reports.md)
now starts from that verified main tip. Complete Ministry exports, packet and
follow-up owners still follow; no integrated gate is released.

PR #70 completes RPT-06.01/.02/.04 and interactive .05 acceptance, with three
[completed independent review/fix rounds](../../guides/stewardship-ministry-report-reviews.md).
PR #70 passed all 24 exact-head CI jobs and DCO, then merged as `5b0d3051`,
verified on refreshed main. The [complete-result Ministry export increment](../../guides/stewardship-ministry-exports.md)
is now implementing RPT-06.03 on a fresh main branch. Packet/follow-up/report
owners follow after its protected delivery. M5 and Gate 3 remain open.

PR #71 now completes RPT-06.03 implementation and focused acceptance, with
[three completed review/fix rounds](../../guides/stewardship-ministry-export-reviews.md#round-3)
and no unresolved accepted Medium-or-higher issues. Full exact-head CI/DCO and
protected merge remain pending. The packet slice must account for ADM-08's
contact-attempt/notes dependency before claiming complete RPT-07 acceptance.

PR #71 passed all 24 full exact-head jobs plus DCO and merged as `c08fd51d`;
its [protected receipt](../../guides/stewardship-ministry-exports.md#protected-delivery)
records the single rerun of an unmodified, runner-sensitive load case. The
dependency-ready ADM-08.03 Ministry follow-up increment now starts from that
verified main tip, so RPT-07 packets can project real contact dates, notes and
outcomes. M5 and Gate 3 remain open.

That [Ministry follow-up increment](../../guides/stewardship-ministry-followup.md)
now implements ADM-08.03 with focused acceptance, an independent fresh-schema
audit and three-engine browser checks. Three
[review/fix rounds](../../guides/stewardship-ministry-followup-reviews.md#round-3)
are complete; full exact-head CI/DCO and protected delivery remain pending. RPT-07 follows on a fresh main
branch once it lands. M5 and Gate 3 remain open.

Two unrelated CI defects found during that work were delivered separately so
they could not block it. PR #73 made a delivery-closed weekly case
[calendar-independent](../../guides/stewardship-weekly-skip-test.md#protected-delivery)
after it failed main by weekday, and PR #74 raised the database shard's
[stack-dump threshold](../../guides/stewardship-shard-watchdog.md#protected-delivery)
above a legitimately slow load case whose dump had crashed passing shards.
Both passed all 24 exact-head jobs plus DCO and merged as `1faa4a88` and
`321ba382`. Their rounds were single-source under the
[September 20, 2026 exemption](../../plans/stewardship/overall.md#automated-phase-delivery-cycle).

PR #72 passed all 24 full exact-head jobs plus DCO on its first attempt and
merged as `bd5522f6`; its
[protected receipt](../../guides/stewardship-ministry-followup.md#protected-delivery)
records the identical retained review tree. The RPT-07 multi-Ministry packet
increment now starts from that verified main tip and reads the recorded contact
dates, notes and outcomes. M5 and Gate 3 remain open.

That [packet increment](../../guides/stewardship-ministry-packets.md) now
implements RPT-07 with focused acceptance, an independent fresh-schema audit and
three-engine browser checks. Three dual-source
[review/fix rounds](../../guides/stewardship-ministry-packet-reviews.md#round-3)
are complete; full exact-head CI/DCO and protected delivery remain pending. M5 and Gate 3 remain open.

PR #75 passed all 24 full exact-head jobs plus DCO on its first attempt and
merged as `1070fd28`; its
[protected receipt](../../guides/stewardship-ministry-packets.md#protected-delivery)
records the identical retained review tree and the withdrawn earlier candidate.
The financial stewardship detail increment of RPT-08 now starts from that
verified main tip. M5 and Gate 3 remain open.

That [financial detail increment](../../guides/stewardship-financial-report.md)
now implements the interactive Admin/Staff report with focused acceptance, an
independent fresh-schema audit and three-engine browser checks. Its CSV, XLSX
and PDF exports followed as the separate increment below, which is what closed
RPT-08.04. Its review rounds, CI and protected delivery are recorded in its
receipt. M5 and Gate 3 remain open.

PR #76 passed all 24 full exact-head jobs plus DCO on its second attempt, after
one shard exposed a SQL check constraint the new operational event had not been
added to, and merged as `f3bdac13`; its
[protected receipt](../../guides/stewardship-financial-report.md#protected-delivery)
records the identical retained review tree and the corrected candidate. The
ADM-07 portal users increment and the ADM-08.04 system logs increment were
reviewed in parallel from the same base and now deliver, in that order, from
that verified main tip. M5 and Gate 3 remain open.

PR #77 passed all 24 full exact-head jobs plus DCO on its second attempt, after
five shards exposed a test factory the setup suites could no longer patch, and
merged as `151966fb`; its
[protected receipt](../../guides/stewardship-portal-users.md#protected-delivery)
records the identical retained review tree and the corrected candidate. The
ADM-08.04 system logs increment now delivers from that verified main tip, and
the financial export increment of RPT-08 is under review beside it. M5 and
Gate 3 remain open.

The [financial export increment](../../guides/stewardship-financial-exports.md)
now completes RPT-08.04: complete CSV, XLSX and PDF captures of the financial
projection, taken by SQL with the page's own giving proof, on the shared export
lifecycle with focused acceptance, an independent fresh-schema audit and
three-engine browser checks. Three review/fix rounds are complete; full
exact-head CI/DCO and protected delivery remain pending. M5 and Gate 3 remain
open.

PR #78 passed all 24 full exact-head jobs plus DCO on its first attempt and
merged as `30cb4635`; its
[protected receipt](../../guides/stewardship-admin-logs.md#protected-delivery)
records the identical retained review tree. The financial export increment now
delivers from that verified main tip. M5 and Gate 3 remain open.

PR #79 passed all 24 full exact-head jobs plus DCO on its second attempt, after
one shard exposed the immutable-guard inventory the new capture table was not
listed in, and merged as `e01b52ba`; its
[protected receipt](../../guides/stewardship-financial-exports.md#protected-delivery)
records the identical retained review tree and the test-only correction.
RPT-08.04 is delivered. The ADM-07 role-editing increment now starts from that
verified main tip. M5 and Gate 3 remain open.

That [login rule edit increment](../../guides/stewardship-user-rule-edits.md)
now lets an Administrator set roles, create and remove login rules from the
Portal users page through previewed configuration requests, with focused
acceptance and three-engine browser checks. Three review/fix rounds and nine
correction checks of its activation guard are complete, the last finding
nothing to fix. PR #80 passed all 25 exact-head checks plus DCO on its first
ready candidate and merged as `4f0465fa`; its
[protected receipt](../../guides/stewardship-user-rule-edits.md#protected-delivery)
records the identical retained review tree. The ADM-07 security event
increment now starts from that verified main tip. M5 and Gate 3 remain open.

That [security event acknowledgement increment](../../guides/stewardship-policy-security-events.md)
now keeps every high-impact login-policy expansion on each Administrator's
dashboard until an Administrator acknowledges it, recorded once and audited,
with focused acceptance, an independent fresh-schema audit and three-engine
browser checks. Three review/fix rounds and a closing correction check are
complete, the check finding nothing to fix. PR #81 passed all 25 exact-head
checks plus DCO on its second ready candidate, after one shard exposed the
Admin page's query budget and two standalone corrections, and merged as
`f9ce5278`; its
[protected receipt](../../guides/stewardship-policy-security-events.md#protected-delivery)
records the identical retained review tree and the corrections. The ADM-07
security event email increment now starts from that verified main tip. M5
and Gate 3 remain open.

That [security event email increment](../../guides/stewardship-security-event-mail.md)
now sends each high-impact login-policy expansion to every Administrator who
existed before it, through the durable outbox as a new delivery purpose
prepared, sent and settled by the same engine as the operational alerts,
with focused acceptance under the real service roles and an independent
fresh-schema audit. Three review/fix rounds were single-source under the
second Codex exemption, the third validating nothing; the first ready
candidate failed the fast build contract, since the container context did not
re-include the new schema files, and a standalone correction with a correction
check that found nothing followed. PR #83 merged through protected auto-merge
as `b1f80661`; its
[protected receipt](../../guides/stewardship-security-event-mail.md#protected-delivery)
records the identical landed tree, the correction and the refreshed-main
verification. The ADM-07 Chairperson suggestion increment now starts from
that verified main tip. M5 and Gate 3 remain open.

That [Chairperson suggestions increment](../../guides/stewardship-chair-suggestions.md)
shows the Administrator, on the Portal users page, each current Chairperson
of an active Ministry in the promoted source with the Member's name, the
Ministry, the contact's publication flag, the address's current rule and
assignment and any ambiguity, through a schema-owned view over the one
Chairperson projection the reconciliation owners use. Three review/fix
rounds and a correction check were single-source under the second Codex
exemption, with every accepted finding fixed. PR #84 merged through
protected auto-merge as `3fd51b5a`; its
[protected receipt](../../guides/stewardship-chair-suggestions.md#protected-delivery)
records the identical landed tree and the refreshed-main verification. The
ADM-07 Chairperson confirmation increment now starts from that verified main
tip. M5 and Gate 3 remain open.

That [Chairperson confirmation increment](../../guides/stewardship-chair-confirmation.md)
lets the Administrator confirm selected suggestions, naming the Member where
an address is shared, as one reviewed configuration request under its own
schema that alone creates Chairperson-seeded rules, grants and assignments,
with the selected Member carried beside the request and recorded as retained
identity evidence inside the activation. Three review/fix rounds and a
correction check were single-source under the second Codex exemption, with
every accepted finding fixed. PR #85 merged through protected auto-merge as
`f26050d2`; its
[protected receipt](../../guides/stewardship-chair-confirmation.md#protected-delivery)
records the identical landed tree and the refreshed-main verification. The
ADM-07 Chairperson review increment now starts from that verified main tip.
M5 and Gate 3 remain open.

That [Chairperson seed review increment](../../guides/stewardship-chair-review.md)
lists each suspended seeded assignment on the Portal users page with its
Member, reason and source state, and lets the Administrator restore it as a
manual assignment, remove it, or keep a seeded Ministry leader role
independently, each an ordinary policy request whose entered reason is
recorded in the audit. Three review/fix rounds were single-source under the
second Codex exemption, the third validating nothing. PR #86 merged through
protected auto-merge as `a69cb098`; its
[protected receipt](../../guides/stewardship-chair-review.md#protected-delivery)
records the identical landed tree, the infrastructure re-run of one shard's
evidence upload and the refreshed-main verification. The ADM-07 manual
assignment editor increment now starts from that verified main tip. M5 and
Gate 3 remain open.

That [manual assignment editor increment](../../guides/stewardship-assignment-editor.md)
lets the Administrator assign an address to an active Ministry of the
promoted catalog or remove an Administrator entry assignment from the Portal
users page, each an ordinary policy request whose preview states the rule
the assignment depends on, and names each assignment's Ministry beside its
DUID. Four review/fix rounds were single-source under the second Codex
exemption, the fourth, a correction check, validating nothing. PR #87 merged
through protected auto-merge as `af474230`; its
[protected receipt](../../guides/stewardship-assignment-editor.md#protected-delivery)
records the identical landed tree and the refreshed-main verification. The
ADM-07 autosave queue increment now starts from that verified main tip. M5
and Gate 3 remain open.

That [login-rule autosave queue increment](../../guides/stewardship-rule-autosave.md)
autosaves each role checkbox change on the Portal users page as one logical
intent through a client-keyed configuration request, with one queue per
page, Applied shown only from an activation receipt, the applied digest
adopted for the next intent, a pause on any failure and an open conflict
view for a stale digest; the native review forms remain without scripting.
Review/fix rounds, full exact-head CI/DCO and protected delivery remain
pending. M5 and Gate 3 remain open.

1. Finish [BG-08](background-processing.md#bg-08-export-and-graph-workers); implement [RPT-01](reports.md#rpt-01-shared-report-framework-and-campaign-selection) and finish [RPT-02](reports.md#rpt-02-population-and-calculation-library).
2. Complete [RPT-03](reports.md#rpt-03-participation-graph-and-campaign-statistics), [RPT-04](reports.md#rpt-04-additional-information-workflow-report), [RPT-05](reports.md#rpt-05-family-code-and-postal-outreach-reports), [RPT-06](reports.md#rpt-06-ministry-summary-and-detail), [RPT-07](reports.md#rpt-07-multi-ministry-follow-up-packet), and the reporting slice of [RPT-08](reports.md#rpt-08-census-and-financial-reports).
3. [ADM-07](admin-portal.md#adm-07-user-rules-and-ministry-assignments) → [ADM-08](admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs) → [RPT-09](reports.md#rpt-09-logs-and-daily-email-parity); connect [RPT-04](reports.md#rpt-04-additional-information-workflow-report)/[RPT-06](reports.md#rpt-06-ministry-summary-and-detail) follow-up and verify [BG-07](background-processing.md#bg-07-submission-confirmations-and-admin-digests) parity.
4. Complete M5 evidence and G3 before Phase 6.

## Phase 6A: Publication

Source scope: [Phase 6A: Publication](../../plans/stewardship/overall.md#phase-6-reconciliation-and-post-campaign-operations).

1. [DAT-09](data.md#dat-09-publication-retention-and-purge-schema-behavior) publication schema → [ADM-09](admin-portal.md#adm-09-census-review-and-parishsoft-publication-ui) → [BG-09](background-processing.md#bg-09-parishsoft-publication-worker).
2. Complete [RPT-08](reports.md#rpt-08-census-and-financial-reports) publication/action integration.
3. Keep real source writes disabled outside explicitly authorized environments until the focused review passes.

## Phase 6B: Recovery and campaign completion

Source scope: [Phase 6B: Recovery and campaign completion](../../plans/stewardship/overall.md#phase-6-reconciliation-and-post-campaign-operations).

1. [OPS-05](operations.md#ops-05-backup-service-and-purge-triggered-backup) → [OPS-06](operations.md#ops-06-restore-and-state-aware-release) → remaining [ADM-06](admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive) restore/reopen/archive/Return work.
   Complete [BG-02](background-processing.md#bg-02-campaign-boundary-occurrences)'s shared token-preparation worker before restore release or final reopen.
2. Complete post-close obligation resolution with [BG-07](background-processing.md#bg-07-submission-confirmations-and-admin-digests); implement [OPS-07](operations.md#ops-07-housekeeping-and-retention-jobs) retention.
3. Review source-compaction integration against [DAT-03](data.md#dat-03-versioned-parishsoft-source-corpus) and [DAT-09](data.md#dat-09-publication-retention-and-purge-schema-behavior).

## Phase 6C: Exceptional purge

Source scope: [Phase 6C: Exceptional purge](../../plans/stewardship/overall.md#phase-6-reconciliation-and-post-campaign-operations).

1. Complete [DAT-09](data.md#dat-09-publication-retention-and-purge-schema-behavior) purge state/gate/checkpoints → [ADM-10](admin-portal.md#adm-10-exceptional-campaign-purge-web-workflow) → [BG-11](background-processing.md#bg-11-exceptional-purge-worker).
2. Exercise only disposable/restored test data within authorized scope.
3. Complete M6 evidence and G4 before Phase 7.

## Phase 7: Release completion

Source scope: [Phase 7: Release completion](../../plans/stewardship/overall.md#phase-7-production-hardening-and-release-readiness).

1. Finish [ARC-08](architecture.md#arc-08-performance-accessibility-and-compatibility-baseline) → [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) → [FAM-08](parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion) and every report scale/accessibility case.
2. Finish [OPS-08](operations.md#ops-08-observability-health-and-operational-runbooks) and [OPS-09](operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline), including all acceptance and release checks.
3. Complete M7 evidence and G5, then perform only the separately authorized PR/release actions.

## Packages that span phases

| Package or task group | Initial delivery | Required later completion |
| --- | --- | --- |
| [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) | Phase 0 clocks/traceability; Phase 1 factories | Grow with each vertical slice; complete acceptance in Phase 7 |
| [DAT-04](data.md#dat-04-family-campaign-identity-and-credentials) and [DAT-05](data.md#dat-05-portal-users-and-authorization-policy-records) | Phase 1 identity/policy schema | Phase 2 source promotion, population, and chair integration |
| [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) | Phase 3 submission/follow-up records | Phase 4 complete jobs/outbox and link later workflow records |
| [ARC-08](architecture.md#arc-08-performance-accessibility-and-compatibility-baseline) and [FAM-08](parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion) | Early performance/accessibility baseline | Full browser and scale evidence in Phase 7 |
| [RPT-02](reports.md#rpt-02-population-and-calculation-library) and [RPT-03](reports.md#rpt-03-participation-graph-and-campaign-statistics) | Phase 3 calculation subset; Phase 4 digest calculation/materialization | Complete report framework, UI, and matrix in Phase 5 |
| [BG-08](background-processing.md#bg-08-export-and-graph-workers) | Phase 4 digest rendering/export substrate | Phase 5 complete export workflow |
| [BG-02](background-processing.md#bg-02-campaign-boundary-occurrences) | Phase 4 start/close boundaries | Phase 6 background restore/reopen token preparation |
| [ADM-06](admin-portal.md#adm-06-restore-release-delivery-pause-reopen-and-archive) and [BG-07](background-processing.md#bg-07-submission-confirmations-and-admin-digests) | Phase 4 delivery pause and scheduled digests | Phase 6 restore, archive, and explicit post-close resolution |
| [RPT-08](reports.md#rpt-08-census-and-financial-reports) | Phase 5 reporting/financial scope | Phase 6 publication/action integration |
| [OPS-08](operations.md#ops-08-observability-health-and-operational-runbooks) and [OPS-09](operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline) | Phase 0/1 CI and operational baseline | Complete runbooks, suites, and release readiness in Phase 7 |

## Completion

Completion requires all 371 implementation tasks, all linked package definitions
of done, M0 through M7 demonstrations, and G1 through G5 review evidence. A
passing unit suite or a completed portal alone does not close the project.
Keep the task index, this navigation map, and the controlling plan synchronized
when scope or ordering changes.
