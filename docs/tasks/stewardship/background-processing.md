# Background processing tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/background-processing.md) ·
[Normative specification](../../specs/stewardship/background-processing/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## BG-01: Durable task, scheduler, lease, and recovery substrate

Scope and dependencies: [BG-01 work package](../../plans/stewardship/background-processing.md#bg-01-durable-task-scheduler-lease-and-recovery-substrate).

- [ ] BG-01.01 — Implement durable task and occurrence claims.
- [ ] BG-01.02 — Configure singleton scheduling and isolated queues.
- [ ] BG-01.03 — Implement transactional campaign-work admission.
- [ ] BG-01.04 — Expose authorized task progress and status.
- [ ] BG-01.05 — Test duplicates, crashes, leases, and shutdown.

Evidence: Not started.

## BG-02: Campaign boundary occurrences

Scope and dependencies: [BG-02 work package](../../plans/stewardship/background-processing.md#bg-02-campaign-boundary-occurrences).

- [ ] BG-02.01 — Materialize unique campaign boundary occurrences.
- [ ] BG-02.02 — Implement locked start and close transitions.
- [ ] BG-02.03 — Replace future close work safely after date edits.
- [ ] BG-02.04 — Recover overdue boundaries while enforcing exact access gates.
- [ ] BG-02.05 — Test DST, restart, duplicate, and boundary races.

Evidence: Not started.

## BG-03: Production-transition cleanup worker

Scope and dependencies: [BG-03 work package](../../plans/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker).

- [ ] BG-03.01 — Enforce the go-live mutation gate.
- [ ] BG-03.02 — Delete inventoried Testing detail in resumable batches.
- [ ] BG-03.03 — Verify cleanup completeness before readiness.
- [ ] BG-03.04 — Implement safe retry and cancellation semantics.
- [ ] BG-03.05 — Test interrupted cleanup and concurrent Testing work.

Evidence: Not started.

## BG-04: Schedule revision, fulfillment, and mode routing

Scope and dependencies: [BG-04 work package](../../plans/stewardship/background-processing.md#bg-04-schedule-revision-fulfillment-and-mode-routing).

- [ ] BG-04.01 — Evaluate schedules using campaign-local intervals.
- [ ] BG-04.02 — Implement revision and semantic-fulfillment identity.
- [ ] BG-04.03 — Implement locked replacement, removal, and cancellation.
- [ ] BG-04.04 — Enforce immutable Testing/Production/operational routing.
- [ ] BG-04.05 — Implement missed-work recovery and coalescing.
- [ ] BG-04.06 — Test schedule, mode, revision, and restart combinations.

Evidence: Not started.

## BG-05: ParishSoft delta and full refresh

Scope and dependencies: [BG-05 work package](../../plans/stewardship/background-processing.md#bg-05-parishsoft-delta-and-full-refresh).

- [ ] BG-05.01 — Implement supported change-feed and watermark adapters.
- [ ] BG-05.02 — Implement delta indications and affected-Family reloads.
- [ ] BG-05.03 — Implement scheduled and manual complete source refreshes.
- [ ] BG-05.04 — Fence source mutations and validate promotion inputs.
- [ ] BG-05.05 — Coalesce manual refresh and exclude concurrent publication.
- [ ] BG-05.06 — Test invalid corpora, retries, takeover, and reconciliation.

Evidence: Not started.

## BG-06: Family invitations and reminders

Scope and dependencies: [BG-06 work package](../../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders).

- [ ] BG-06.01 — Materialize eligible Family mail slots.
- [ ] BG-06.02 — Implement deliverability-recovery invitations.
- [ ] BG-06.03 — Render personalized versioned Family templates.
- [ ] BG-06.04 — Seal substitutions for isolated mail dispatch.
- [ ] BG-06.05 — Implement provider outcomes, reconciliation, and scrubbing.
- [ ] BG-06.06 — Implement pause holds, close cancellation, and resume.
- [ ] BG-06.07 — Test recipients, suppression, routing, races, and failures.

Evidence: Not started.

## BG-07: Submission confirmations and Admin digests

Scope and dependencies: [BG-07 work package](../../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).

- [ ] BG-07.01 — Create idempotent submission confirmations.
- [ ] BG-07.02 — Build daily digests against exact immutable facts.
- [ ] BG-07.03 — Build weekly information and correction digests.
- [ ] BG-07.04 — Integrate post-close obligation inventory and explicit resolutions.
- [ ] BG-07.05 — Test digest coverage, parity, recovery, and repeat safety.

Evidence: Not started.

## BG-08: Export and graph workers

Scope and dependencies: [BG-08 work package](../../plans/stewardship/background-processing.md#bg-08-export-and-graph-workers).

- [ ] BG-08.01 — Implement requester-scoped export jobs and pinned inputs.
- [ ] BG-08.02 — Recheck authorization throughout export and download.
- [ ] BG-08.03 — Write owner-only atomic exports with expiration.
- [ ] BG-08.04 — Implement shared deterministic chart rendering.
- [ ] BG-08.05 — Test large exports, revocation, cancellation, and purge races.

Evidence: Not started.

## BG-09: ParishSoft publication worker

Scope and dependencies: [BG-09 work package](../../plans/stewardship/background-processing.md#bg-09-parishsoft-publication-worker).

- [ ] BG-09.01 — Claim source lease for confirmed publication plans.
- [ ] BG-09.02 — Recheck source payload, conflicts, and fencing before PUT.
- [ ] BG-09.03 — Implement grouped writes, bounded retries, and verification.
- [ ] BG-09.04 — Record partial outcomes and request final source refresh.
- [ ] BG-09.05 — Test external races, ambiguous writes, and partial recovery.

Evidence: Not started.

## BG-10: Critical notification and service shutdown

Scope and dependencies: [BG-10 work package](../../plans/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown).

- [ ] BG-10.01 — Create durable deduplicated critical events.
- [ ] BG-10.02 — Dispatch operational Admin and optional Slack alerts.
- [ ] BG-10.03 — Implement escalation, suppression, and recovery notices.
- [ ] BG-10.04 — Implement graceful worker and scheduler shutdown.
- [ ] BG-10.05 — Test notification failures and interrupted shutdown.

Evidence: Not started.

## BG-11: Exceptional purge worker

Scope and dependencies: [BG-11 work package](../../plans/stewardship/background-processing.md#bg-11-exceptional-purge-worker).

- [ ] BG-11.01 — Recheck purge ownership and prerequisites at claim.
- [ ] BG-11.02 — Delete campaign data in stable checkpointed batches.
- [ ] BG-11.03 — Enforce rollback limits and resume after deletion starts.
- [ ] BG-11.04 — Complete file cleanup, tombstone, and credential invalidation.
- [ ] BG-11.05 — Escalate purge inconsistency and cleanup failures.

Evidence: Not started.
