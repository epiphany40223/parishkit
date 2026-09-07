# Stewardship milestones and review tasks

[Task index](README.md) · [Task execution plan](overall.md) ·
[Controlling plan](../../plans/stewardship/overall.md)

Implementation completion lives in the eight subsystem task lists. This file
tracks integration evidence and gate completion, not a second copy of package
status. Execute each phase's exact package order and partial-package scope from
the controlling plan. A review gate remains open until its whole protocol and
exit criteria pass, even if all implementation checkboxes are checked.

## Phase 0: Skeleton

Scope: [Phase 0](../../plans/stewardship/overall.md#phase-0-reproducible-project-skeleton).

- [ ] M0.01 — Demonstrate local bind-mount reload, routes, and internal health.
- [ ] M0.02 — Run the same baseline tests on the host and in the image.
- [ ] M0.03 — Demonstrate safe startup rejection for incomplete production settings.
- [ ] M0.04 — Review and correct the scaffold before foundation work.

Evidence: Not started.

## Phase 1: Secure foundation

Scope: [Phase 1](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

- [ ] M1.01 — Demonstrate fake-provider Google login, denial, and session expiry.
- [ ] M1.02 — Demonstrate Family access and exact campaign-boundary denial.
- [ ] M1.03 — Prove single-current-campaign constraints under concurrent requests.
- [ ] M1.04 — Verify service mounts, configuration activation, and restart durability.
- [ ] M1.05 — Complete Gate 1 before beginning Phase 2.

Evidence: Not started.

## Gate 1: Foundation and security

Scope: [Gate 1](../../plans/stewardship/overall.md#review-gate-1-foundation-and-security).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G1.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G1.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G1.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G1.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G1.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G1.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 2: Setup and source truth

Scope: [Phase 2](../../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation).

- [ ] M2.01 — Demonstrate empty deployment through a configured Testing campaign.
- [ ] M2.02 — Prove YAML/database agreement and safe installer/wizard interruption.
- [ ] M2.03 — Demonstrate atomic full/delta refresh and stable campaign identities.
- [ ] M2.04 — Preview each Family page/email without creating live fulfillment.
- [ ] M2.05 — Complete the focused import/setup correction pass before the Family slice.

Evidence: Not started.

## Phase 3: Family response

Scope: [Phase 3](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

- [ ] M3.01 — Demonstrate the minimal login-to-submit-to-repeat-visit vertical slice.
- [ ] M3.02 — Exercise every enabled-module combination and all Family response fields.
- [ ] M3.03 — Prove in-memory drafts, atomic submission, and stale/duplicate protection.
- [ ] M3.04 — Demonstrate upstream merge, proposal provenance, and follow-up supersession.
- [ ] M3.05 — Complete representative mobile/accessibility/privacy evidence and Gate 2.

Evidence: Not started.

## Gate 2: Data, privacy, and Family UX

Scope: [Phase 3 and Gate 2](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G2.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G2.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G2.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G2.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G2.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G2.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 4: Production scheduling and mail

Scope: [Phase 4](../../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).

- [ ] M4.01 — Demonstrate Testing routing and operational-alert classification.
- [ ] M4.02 — Prove resumable Testing cleanup and atomic Production activation.
- [ ] M4.03 — Exercise scheduled mail, receipts, and digests through outages and retries.
- [ ] M4.04 — Verify unknown delivery, pause/resume, and close-during-pause behavior.
- [ ] M4.05 — Recheck real service queue/mount needs using fake-backed integrations.

Keep live dispatch and final Production activation within the environments
permitted by the master plan until Gate 3 is complete.

Evidence: Not started.

## Phase 5: Reporting and staff workflows

Scope: [Phase 5](../../plans/stewardship/overall.md#phase-5-reports-exports-users-and-follow-up).

- [ ] M5.01 — Demonstrate all report formats and historical campaign selection.
- [ ] M5.02 — Verify Family-code, financial, and assigned-Ministry access boundaries.
- [ ] M5.03 — Prove shared values across web, charts, exports, and pinned digests.
- [ ] M5.04 — Deny queued/download access after role or assignment revocation.
- [ ] M5.05 — Complete Gate 3 before beginning publication and destructive workflows.

Evidence: Not started.

## Gate 3: Async processing, RBAC, and reports

Scope: [Gate 3](../../plans/stewardship/overall.md#review-gate-3-async-processing-rbac-and-reporting).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G3.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G3.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G3.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G3.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G3.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G3.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 6: Reconciliation and campaign completion

Scope: [Phase 6](../../plans/stewardship/overall.md#phase-6-reconciliation-and-post-campaign-operations).

- [ ] M6.01 — Demonstrate proposal review, conflicts, manual outcomes, and partial publication.
- [ ] M6.02 — Prove source fencing, verification, and safe retry of unresolved entities.
- [ ] M6.03 — Restore an isolated backup and exercise every release/pointer state.
- [ ] M6.04 — Demonstrate reopen, post-close mail resolution, archive, and Return to Testing.
- [ ] M6.05 — Exercise purge gates, rollback limits, resume, cleanup, and tombstones on disposable data.
- [ ] M6.06 — Complete Gate 4 before release hardening can declare these workflows ready.

Evidence: Not started.

## Gate 4: External writes and destructive workflows

Scope: [Gate 4](../../plans/stewardship/overall.md#review-gate-4-external-writes-and-destructive-workflows).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G4.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G4.02 — Obtain independent transaction/migration and security/authorization reviews.
- [ ] G4.03 — Run authorized isolated restore/purge exercises and boundary failure injection.
- [ ] G4.04 — Triage findings, implement corrections, and add regression coverage.
- [ ] G4.05 — Repeat validation, demonstrations, and independent review after corrections.
- [ ] G4.06 — Resolve every validated Critical/High/Medium finding; document Low deferrals.
- [ ] G4.07 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 7: Release readiness

Scope: [Phase 7](../../plans/stewardship/overall.md#phase-7-production-hardening-and-release-readiness).

- [ ] M7.01 — Close every subsystem task and acceptance traceability entry.
- [ ] M7.02 — Complete supported-browser, scale, coverage, and accessibility verification.
- [ ] M7.03 — Validate production images and exercise the documented operational runbooks.
- [ ] M7.04 — Collect authorized human-run integration smoke-test evidence.
- [ ] M7.05 — Complete retention/privacy review and operator/developer handoff documents.
- [ ] M7.06 — Demonstrate the full disposable campaign lifecycle from production images.
- [ ] M7.07 — Complete Gate 5 before release artifact publication.

Evidence: Not started.

## Gate 5: Release readiness

Scope: [Gate 5](../../plans/stewardship/overall.md#review-gate-5-release-readiness).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G5.01 — Prepare the complete review diff and pass all required release validation.
- [ ] G5.02 — Obtain two independent reviews of the complete implementation.
- [ ] G5.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G5.04 — Repeat full validation and independent review of the corrected diff.
- [ ] G5.05 — Resolve release blockers and record all permitted Low deferrals.
- [ ] G5.06 — Record reviewed SHA and human product/security/operations approval.
- [ ] G5.07 — Complete the normal PR handoff and separately authorized release procedure.

Evidence: Not started.
