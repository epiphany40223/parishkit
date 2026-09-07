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
2. Implement AppliedConfigurationVersion, ConfigurationChangeRequest,
   SecretReplacementRequest, normalized YAML materializations, Parish/branding,
   SystemConfiguration, and integration metadata/fingerprints.
3. Enforce one active applied version matching the YAML digest, immutable
   configuration projections, target-specific request claims, singleton parish/
   runtime-configuration rules, and Testing default.
4. Add PostgreSQL-backed session configuration and base audit correlation
   fields.
5. Implement the base TaskRun record/claim metadata required by BG-01; later job
   packages extend it with outbox and workflow records.
6. Test constraints, YAML/request state machines and mismatch recovery, UTC
   round trips, optimistic versions, TaskRun claims, and secret-value exclusion.

### DAT-02: Campaign lifecycle and schedule schema

1. Implement Campaign, immutable-after-readiness IANA timezone snapshot, enabled
   modules, financial/comparison periods, fund and Ministry selections, share-
   option versions, content references, delivery-pause metadata, and structural-
   lock state.
2. Add transactional guards for one current campaign across draft through
   closed, Testing-only draft creation, interval validity, at-least-one module,
   exact-year financial period, and current-campaign pointer consistency.
3. Implement CampaignBoundaryOccurrence and lifecycle/mode transition history.
4. Implement ScheduleDefinition, immutable revisions, occurrence records,
   semantic fulfillment, replacement/removal markers, restore delivery holds,
   and PostCloseMailResolution coverage/actor/reason records with explicit
   semantic-resolution and occurrence/outbox/task idempotency uniqueness
   constraints. Wire later submission/item references with DAT-06/DAT-07.
5. Add race tests for creation, activation, close, draft timezone/end-date
   edits, Parish-default timezone changes, withdrawal, reopen, archive,
   unarchive, and return to Testing.

### DAT-03: Versioned ParishSoft source corpus

1. Implement SourceSnapshot metadata and normalized versioned Family, Member,
   contact/address, Ministry, roster, fund, pledge, and contribution tables.
2. Implement mandatory canonical content digests, immutable payload-version
   reuse, and snapshot membership maps so unchanged entities are never copied
   by refresh and every uncompacted snapshot reconstructs one coherent corpus.
3. Add promoted-snapshot/current-index pointers and a transaction that promotes
   all staged data atomically.
4. Implement SourceMutationLease with fencing token, heartbeat, expiry, phase,
   owner task, and safe-takeover metadata.
5. Add permanent manifest/compaction metadata, protected-reference discovery,
   and deterministic 90-day/all, one-year/daily, and later/monthly anchors.
6. Add immutable CampaignDailyFactSet/DailyFact generation, completeness,
   publication-pointer, and pinned-reference constraints, plus the unique
   per-campaign/scope CampaignFactRebuildDemand row and claim/pending revisions.
7. Add source watermark/cursor storage, count/digest validation records, and
   integration tests for failed staging, stale-owner denial, atomic promotion,
   deduplication, fact publication, protected references, cutoff boundaries,
   and compaction races.

### DAT-04: Family campaign identity and credentials

1. Implement FamilyCampaign eligibility/deliverability/status history,
   immutable first-eligibility timestamp/source-generation provenance, campaign
   code ciphertext, MAC fingerprint rows, access-token ciphertext/digest, mail
   state, response pointers, and activity metadata.
2. Add campaign/key-scoped uniqueness and cross-key collision constraints for
   codes and campaign-scoped token-digest uniqueness.
3. Implement atomic `READ COMMITTED` population/reconciliation services for new,
   inactive, and reactivated Families, using a campaign generation lock,
   set-based cross-key collision filtering, and bounded batch retries without
   changing an existing campaign code.
4. Add token destruction/reissuance and close/reopen metadata without retaining
   secret material in audit rows.
5. Test concurrent generation, migration, inactive/reactivated behavior, and
   transactional snapshot promotion effects.

### DAT-05: Portal users and authorization policy records

1. Implement PortalUser, DomainRule, AddressRule, MinistryAssignment,
   chairperson suggestions, suspension/review tasks, and login/audit history.
2. Materialize configured rules/manual assignments from exact YAML versions and
   keep source suspension/reactivation as a fail-closed runtime overlay.
3. Enforce no domain Administrator, no `gmail.com` domain, explicit-address
   precedence, last-Administrator protection, and hosted-domain evidence.
4. Implement Admin-confirmed chair-seed configuration requests and snapshot-
   driven suspension/reactivation while preserving manual assignments and
   unrelated roles.
5. Add indexes for normalized email/domain and Ministry row-scope queries.
6. Test every role-source transition, activation race, and concurrent autosave
   digest conflict.

### DAT-06: Immutable submissions and proposal overlay

1. Implement immutable versioned Submission aggregates for Family, existing and
   proposed Members, Ministry choices, financial answers/share options, and
   additional information.
2. Store baseline snapshot/prior response, schema/content versions, mode,
   campaign-local submission date, and monotonic Family version.
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
2. Extend the DAT-01 TaskRun substrate and implement OutboxMessage, sealed
   substitution metadata, AuditEvent, OperationalLog, and appropriate ownership/
   correlation indexes plus task/outbox semantic uniqueness constraints.
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
   rollback boundaries; require Testing, a null current pointer, and no
   successor campaign at request creation.
3. Implement retention services for source-snapshot compaction, test cleanup,
   terminal outbox substitution scrubbing, temporary artifacts, protected live
   history, and Admin-approved campaign purge without unsafe cascades.
4. Add PostgreSQL integration tests for gate/pointer/successor races, fencing,
   deletion batches, pre-delete rollback, post-delete retry, cleanup failure,
   and retained parish-owned audit.

## Review handoffs

- Review Gate 1: DAT-01, DAT-02, and the Phase 1 portions of DAT-04/DAT-05.
- Review Gate 2: DAT-03, the remaining DAT-04/DAT-05 source-driven behavior,
  and DAT-06/DAT-08 submission/merge using the Family vertical slice.
- Review Gate 3: DAT-07 outbox/job/idempotency review.
- Review Gate 4: DAT-09 destructive-state and ParishSoft publication review.

## Completion criteria

- Every durable invariant named in the specification is enforced transactionally
  and has a PostgreSQL race/constraint test.
- Snapshot, submission, audit, publication, and purge history can be replayed
  without relying on mutable display tables.
- Migration drift is empty and retention cannot cascade into shared or another
  campaign's data.
