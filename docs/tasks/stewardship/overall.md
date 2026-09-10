# Stewardship top-level task execution plan

Start here to coordinate all eight [subsystem task lists](README.md). This is
the task-level navigation companion to the
[controlling implementation plan](../../plans/stewardship/overall.md), which
owns dependency ordering, allowed phase scope, and gate exit requirements.
Use [milestones](milestones.md) for demonstration and review status.

## How to select the next work

1. Find the earliest incomplete phase whose preceding review gate has passed and
   whose preceding phase PR has merged. Follow the controlling plan's
   [automated phase delivery cycle](../../plans/stewardship/overall.md#automated-phase-delivery-cycle)
   for branch creation, delegated decisions, review rounds, CI, and merge approval.
2. Follow the ordered package links below; within a package, inspect its full
   plan item, task evidence, and dependencies before selecting work.
3. Deliver a coherent phase/subphase batch, verify it, and update its owning task lists.
   Record partial scope when a package spans phases; do not mark a whole package
   done because its first consumer works. Continue through dependency-ready tasks
   within the batch without routine approval stops. Individual storage records,
   migrations and helpers are internal checkpoints, not default PR boundaries.
4. Complete the phase demonstration and required review/fix rounds, then create
   or update its PR and correct CI failures. At a formal gate, also validate and
   review the complete integrated gate scope. Stop for human merge approval with
   the applicable gate evidence before proceeding to the next phase.
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

## Phase 1B: Identity and web foundations

Source scope: [Phase 1B: Identity and web foundations](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

1. [ARC-03](architecture.md#arc-03-django-web-foundation-and-security-middleware) → [ARC-04](architecture.md#arc-04-google-identity-authorization-sessions-and-denial-paths) with [ADM-01](admin-portal.md#adm-01-login-denial-and-unconfigured-state-routing) integration.
2. [DAT-04](data.md#dat-04-family-campaign-identity-and-credentials) credential/schema foundation → [ARC-05](architecture.md#arc-05-family-code-token-and-family-session-security).
3. [ARC-06](architecture.md#arc-06-enforceable-cryptographic-service-boundary) → [ARC-07](architecture.md#arc-07-application-level-privacy-and-audit-primitives) → initial [ARC-08](architecture.md#arc-08-performance-accessibility-and-compatibility-baseline) and [DOM-04](campaign-domain.md#dom-04-shared-presentation-and-client-contracts).

## Phase 1C: Runtime foundation

Source scope: [Phase 1C: Runtime foundation](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

1. [OPS-02](operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets) → [OPS-03](operations.md#ops-03-production-ingress-tls-and-network-security) → [OPS-04](operations.md#ops-04-bootstrap-migrations-startup-and-upgrades) → baseline [OPS-08](operations.md#ops-08-observability-health-and-operational-runbooks).
2. Complete M1 evidence and G1 before beginning Phase 2.

## Phase 2: Source, setup, and preparation

Source scope: [Phase 2: Source, setup, and preparation](../../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation).

1. [DAT-03](data.md#dat-03-versioned-parishsoft-source-corpus) → [BG-01](background-processing.md#bg-01-durable-task-scheduler-lease-and-recovery-substrate) → [BG-05](background-processing.md#bg-05-parishsoft-delta-and-full-refresh) → [ADM-02](admin-portal.md#adm-02-bootstrap-command-and-transactional-setup-wizard) → [ADM-03](admin-portal.md#adm-03-navigation-dashboard-indicators-and-configuration) → [ADM-04](admin-portal.md#adm-04-campaign-editor-content-schedules-and-previews).
2. Complete source-promotion integration for [DAT-04](data.md#dat-04-family-campaign-identity-and-credentials) population and [DAT-05](data.md#dat-05-portal-users-and-authorization-policy-records) chair suggestions.
3. Complete M2 evidence and the focused setup/import correction pass.

## Phase 3A: Minimal Family slice

Source scope: [Phase 3A: Minimal Family slice](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

1. [DAT-06](data.md#dat-06-immutable-submissions-and-proposal-overlay) → submission/follow-up slice of [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) → [DAT-08](data.md#dat-08-merge-and-source-reconciliation-services).
2. [FAM-01](parishioner-portal.md#fam-01-availability-code-entry-and-secure-link-exchange) → [FAM-02](parishioner-portal.md#fam-02-in-memory-form-engine-and-navigation) → minimal [FAM-03](parishioner-portal.md#fam-03-family-census-step) and [FAM-06](parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit) response flow.
3. Land the first executable [DOM-05](campaign-domain.md#dom-05-cross-domain-acceptance-harness) Family scenario before expanding fields.

## Phase 3B: Complete Family flow

Source scope: [Phase 3B: Complete Family flow](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

1. Finish [FAM-03](parishioner-portal.md#fam-03-family-census-step); complete [FAM-04](parishioner-portal.md#fam-04-existing-and-proposed-member-steps), [FAM-05](parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps), [FAM-06](parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit), and [FAM-07](parishioner-portal.md#fam-07-repeat-visits-and-source-change-merge).
2. Exercise representative [FAM-08](parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion) cases continuously.
3. Complete [DAT-07](data.md#dat-07-follow-up-content-templates-jobs-and-audit) follow-up derivation and required [RPT-02](reports.md#rpt-02-population-and-calculation-library) calculations.
4. Complete M3 evidence and G2 before starting Production mail work.

## Phase 4: Production scheduling and delivery

Source scope: [Phase 4: Production scheduling and delivery](../../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).

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
