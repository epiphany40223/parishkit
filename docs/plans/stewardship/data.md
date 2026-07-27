# Data and reconciliation implementation plan

This plan implements the
[data and reconciliation specification](../../specs/stewardship/data/spec.md).
Migrations and transactional invariants are first-class deliverables; view-only
validation is never sufficient.

## Migration policy

- Add models in dependency-sized migrations, with explicit constraints and
  indexes in the same work package that depends on them.
- Prove forward and reverse behavior where Django supports it; document any
  intentionally irreversible data migration.
- Run migration drift checks and PostgreSQL integration tests for every package.
- Use factories/builders instead of shared mutable fixture dumps.

## Work packages

### DAT-01: Storage conventions and base records

1. Add UUID/time/version/audit mixins, UTC enforcement, immutable-row helpers,
   soft historical references, and parish ownership conventions.
2. Implement Parish, branding references, SystemConfiguration, integration
   metadata/fingerprints, and configuration version records.
3. Enforce the singleton parish/configuration rules and Testing default.
4. Add PostgreSQL-backed session configuration and base audit correlation
   fields.
5. Test constraints, UTC round trips, optimistic versions, and secret-value
   exclusion.

### DAT-02: Campaign lifecycle and schedule schema

1. Implement Campaign, enabled modules, financial/comparison periods, fund and
   Ministry selections, share-option versions, content references, delivery-
   pause metadata, and structural-lock state.
2. Add transactional guards for one current campaign across draft through
   closed, Testing-only draft creation, interval validity, at-least-one module,
   exact-year financial period, and current-campaign pointer consistency.
3. Implement CampaignBoundaryOccurrence and lifecycle/mode transition history.
4. Implement ScheduleDefinition, immutable revisions, occurrence records,
   semantic fulfillment, replacement/removal markers, and restore delivery
   holds with uniqueness constraints.
5. Add race tests for creation, activation, close, end-date edits, withdrawal,
   reopen, archive, unarchive, and return to Testing.

### DAT-03: Versioned ParishSoft source corpus

1. Implement SourceSnapshot metadata and normalized versioned Family, Member,
   contact/address, Ministry, roster, fund, pledge, and contribution tables.
2. Choose and document deduplication/version-reference strategy while ensuring
   any snapshot reconstructs one coherent corpus.
3. Add promoted-snapshot/current-index pointers and a transaction that promotes
   all staged data atomically.
4. Implement SourceMutationLease with fencing token, heartbeat, expiry, phase,
   owner task, and safe-takeover metadata.
5. Add source watermark/cursor storage, count/digest validation records, and
   integration tests for failed staging, stale-owner denial, and atomic
   promotion.

### DAT-04: Family campaign identity and credentials

1. Implement FamilyCampaign eligibility/deliverability/status history, campaign
   code ciphertext, MAC fingerprint rows, access-token ciphertext/digest, mail
   state, response pointers, and activity metadata.
2. Add campaign/key-scoped uniqueness and cross-key collision constraints for
   codes and campaign-scoped token-digest uniqueness.
3. Implement population/reconciliation services for new, inactive, and
   reactivated Families without changing an existing campaign code.
4. Add token destruction/reissuance and close/reopen metadata without retaining
   secret material in audit rows.
5. Test concurrent generation, migration, inactive/reactivated behavior, and
   transactional snapshot promotion effects.

### DAT-05: Portal users and authorization policy records

1. Implement PortalUser, DomainRule, AddressRule, MinistryAssignment,
   chairperson suggestions, suspension/review tasks, and login/audit history.
2. Enforce no domain Administrator, no `gmail.com` domain, explicit-address
   precedence, last-Administrator protection, and hosted-domain evidence.
3. Implement Admin-confirmed chair-seed creation and snapshot-driven suspension/
   reactivation while preserving manual assignments and unrelated roles.
4. Add indexes for normalized email/domain and Ministry row-scope queries.
5. Test every role-source transition and concurrent autosave version conflict.

### DAT-06: Immutable submissions and proposal overlay

1. Implement immutable versioned Submission aggregates for Family, existing and
   proposed Members, Ministry choices, financial answers/share options, and
   additional information.
2. Store baseline snapshot/prior response, schema/content versions, mode,
   parish-local submission date, and monotonic Family version.
3. Implement atomic final-submit service that validates the complete payload,
   rejects stale versions, creates derived proposals/workflows, and advances
   only the correct test/live effective pointer.
4. Implement ProposedChange decision/execution dimensions, writability registry,
   provenance, supersession, and separate death-date/deceased-semantic records.
5. Test no-change and all-field submissions, immutable history, test/live
   isolation, stale races, and retry after a failed transaction.

### DAT-07: Follow-up, content, templates, jobs, and audit

1. Implement AdditionalInformationItem dispositions/history, MinistryRequest,
   contact attempts, Staff notes, manual-census resolution metadata, named
   content versions, and email template versions.
2. Implement TaskRun, OutboxMessage, sealed substitution metadata, AuditEvent,
   OperationalLog, and appropriate ownership/correlation indexes.
3. Implement ProductionTransitionRequest, delivery-pause holds, export records,
   publication plan/attempt records, and stable idempotency keys.
4. Enforce immutable/append-only behavior and terminal-state credential
   scrubbing in model services and database constraints where feasible.
5. Test correction/supersession behavior, terminal transitions, concurrency,
   and privacy-safe audit payloads.

### DAT-08: Merge and source reconciliation services

1. Implement the deterministic effective-value merge for current source,
   immutable Family-submitted values, prior effective response, and Admin-edited
   publication proposals.
2. Classify unchanged, upstream-caught-up, Family-changed, source-changed, and
   true three-way-conflict cases per atomic field/semantic request.
3. Preserve Family-facing provenance: never display an Admin edit as if the
   Family submitted it.
4. Reconcile snapshot promotions into Family eligibility, proposed-change
   resolution/conflict, chair assignments, and workflow supersession in the
   same coherent post-promotion process.
5. Add table-driven and property-style tests for every merge branch and
   repeated/reordered refresh.

### DAT-09: Publication, retention, and purge schema behavior

1. Implement review-plan versioning, source payload digests, entity grouping,
   execution checkpoints, conflicts, read-after-write verification, and
   immutable outcome records.
2. Implement PurgeRequest state machine, campaign-wide gate ownership,
   inventory/backup expirations, batch checkpoints, tombstone, and allowable
   rollback boundaries.
3. Implement retention services for test cleanup, terminal outbox substitution
   scrubbing, temporary artifacts, indefinite live history, and Admin-approved
   campaign purge without unsafe cascades.
4. Add PostgreSQL integration tests for gate races, fencing, deletion batches,
   pre-delete rollback, post-delete retry, cleanup failure, and retained parish-
   owned audit.

## Review handoffs

- Review Gate 1: DAT-01 through DAT-05 schema/constraint and migration review.
- Review Gate 2: DAT-06 and DAT-08 submission/merge review using the Family
  vertical slice.
- Review Gate 3: DAT-07 outbox/job/idempotency review.
- Review Gate 4: DAT-09 destructive-state and ParishSoft publication review.

## Completion criteria

- Every durable invariant named in the specification is enforced transactionally
  and has a PostgreSQL race/constraint test.
- Snapshot, submission, audit, publication, and purge history can be replayed
  without relying on mutable display tables.
- Migration drift is empty and retention cannot cascade into shared or another
  campaign's data.
