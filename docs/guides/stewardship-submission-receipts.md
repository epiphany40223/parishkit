# Submission confirmation delivery

## Scope and dependencies

This Phase 4 increment begins on `pr/stewardship-submission-receipts` from
PR #45's verified merge `3bfec17a`. It delivers
[BG-07.01](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
as a coherent submission-to-delivery flow, including its receipt-specific
Testing, pause, retry, resolution and cleanup integration. Follow the
[confirmation contract](../specs/stewardship/background-processing/spec.md#submission-confirmation),
[final submission contract](../specs/stewardship/parishioner-portal/spec.md#review-and-submission),
and [content contract](../specs/stewardship/data/spec.md#content-and-email-templates).
Daily/weekly digest ownership, complete post-close inventory/resolution UI and
Gate 3 remain with their existing later owners; this increment does not claim
the full BG-07 package complete.

## Internal acceptance checkpoints

1. Deliver credential-free content, deterministic confirmation-template
   selection and atomic submission/outbox creation. No provider or broker I/O
   belongs inside final Submit. No deliverable head address means a non-error
   audit, not a blocked response or empty-recipient message.
2. Connect receipt dispatch to existing fenced task/provider journal ownership.
   Keep direct receipt identity independent of invitation/reminder schedules;
   preserve Testing routing, current-source checks, pause holds and truthful
   provider uncertainty without copying the SMTP attempt state machine.
3. Integrate current Admin metadata, evidence, explicit retry and duplicate-risk
   resolution. Preserve exact submission/epoch/root bindings, immutable attempt
   history and post-close admission without inventing successful fulfillment.
4. Verify fresh-install schema/model/grant equivalence, actual runtime-role
   adversarial tests, cleanup, submission rollback and race coverage. Retained
   development databases are neither upgraded nor deleted.
5. Complete full local validation, three successful dual-source review/fix
   rounds, exact-head CI/DCO, protected merge-group checks and fresh-main
   verification before advancing to the digest increment.

## Execution evidence

Implementation is in progress. The checkpoints above are acceptance targets,
not claims of completion. Task status remains unchecked until implementation
and its applicable verification pass.

The initial content checkpoint implements credential-free rendering with fixed
required facts and campaign-zone timestamps, a separately authored confirmation
block, singleton direct-mail template selection, and shared Testing routing.
It passes 231 focused pure tests, 50 PostgreSQL content/actual-role editing tests
in 55.56 seconds, and the full baseline: 6,141 passed, 4,233 explicit profile
skips and two existing warnings in 58.20 seconds. Ruff and changed Markdown pass.
Final Submit/outbox and dispatch integration remain in progress; this checkpoint
alone cannot send receipts and does not complete BG-07.01.
