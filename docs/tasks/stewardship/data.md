# Data and reconciliation tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/data.md) ·
[Normative specification](../../specs/stewardship/data/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## DAT-01: Storage conventions and base records

Scope and dependencies: [DAT-01 work package](../../plans/stewardship/data.md#dat-01-storage-conventions-and-base-records).

- [x] DAT-01.01 — Implement durable record and ownership conventions.
- [ ] DAT-01.02 — Implement configuration, parish, and secret-request records.
- [ ] DAT-01.03 — Enforce YAML-version, singleton, and installer constraints.
- [x] DAT-01.04 — Configure durable sessions and audit correlation.
- [x] DAT-01.05 — Implement TaskRun claims, retry-chain constraints, and attempt history.
- [ ] DAT-01.06 — Test base constraints, recovery, and privacy.

Evidence: Phase 1 storage increment, September 8, 2026. `storage.py` defines
UUID/UTC/actor/correlation records, row-lock plus expected-version mutation,
and immutable ORM helpers. The parish-owned AuditEvent envelope has a reversible
PostgreSQL append-only trigger and soft UUID subject/actor references, so session
deletion cannot cascade into history. PortalSession is expiring metadata over
Django's database-backed sessions, not a new authentication implementation.
It retains no raw credential in audit; ARC-04 owns login, revocation, cleanup,
and security policy, while ARC-07 owns validated event-specific payloads.

Storage verification covers UTC/queryset validation, SQL constraints/raw mutation
denial, session reconnect
durability, migration reversal/reapplication, transactional rollback, and two
independent concurrent connections with exactly one successful version update.
See the [database test guide](../../guides/stewardship-database-tests.md) for
repeatable commands and CI isolation. DAT-01.05 is delivered by the seventh increment;
DAT-01.01's explicit ownership integration is delivered by the sixth increment
below; the original storage increment had deployment-level ownership only.
DAT-01.06 is partial pending their constraints/recovery/privacy scenarios.
DAT-01.04's PostgreSQL-backed session configuration is delivered and verified
solely in the disposable database test profile. Non-test PostgreSQL connection
and startup integration remain ARC-02/OPS-04 prerequisites; production still
refuses startup. Review-specific test counts and CI evidence are tracked in the
[Phase 1 milestone](milestones.md#phase-1-secure-foundation).

Second increment: DAT-01.02/.03 preparation portions now include immutable
AppliedConfigurationVersion, Parish, and AppliedIntegration records, strict
non-secret schema validation, atomic/idempotent PostgreSQL preparation,
singleton-root constraints, and exact stored-projection verification. The
[preparation boundary guide](../../guides/stewardship-configuration-preparation.md)
defines the supported subset and integration handoff. At that checkpoint,
DAT-01.01/.02/.03/.06 remained incomplete: active-version/runtime state, request idempotency/status,
operator recovery, secret replacement, complete materializer/installer effects,
and historical audit ownership integration are not implemented by preparation.
Schema constraints, rollback, migration reversal, mismatches, reconnect
durability, and independent-connection preparation races are verified in the
PostgreSQL suite; review-specific results belong to the milestone evidence.

Third increment: immutable configuration request intents now bind actor-scoped
keys to canonical patch/base fingerprints and fixed candidate identities.
Versioned patch builders preserve retry semantics; append-only intake/cancellation
checkpoints and safe audits commit together under PostgreSQL guards. Tests cover
retries, independent-connection races, rollback, status ownership, and raw-SQL
constraints. See the [intake integration boundary](../../guides/stewardship-configuration-requests.md).
DAT-01.02/.03 remain partial: current-Admin/CSRF admission, installer activation/
failure checkpoints, active/runtime state, secret requests, operator recovery,
and applied-version status effects are not implemented by this storage increment.

Fourth increment: internal activation adds the singleton Testing runtime,
protected active pointer, immutable activation ledger, complete ordinary request
checkpoints, historical applied receipts/affected values, and the PostgreSQL
Materializer. See the [activation boundary](../../guides/stewardship-configuration-activation.md).
DAT-01.02/.03/.06 remain partial pending secret requests, full runtime mode/
campaign/restore state, offline recovery, authorization/service integration,
credential evidence, and remaining recovery/privacy verification. This does not
enable production or release Gate 1.

Fifth increment: secret-request storage adds immutable intent bindings,
target reservation, actor-scoped receipts, expiry/cancellation, append-only
checkpoints and audit, and retryable external-staging cleanup acknowledgement.
See the [secret-request storage boundary](../../guides/stewardship-secret-requests.md).
The payload store, sealing and installer identity are not implemented or exposed;
installation/consumer acknowledgement and the remaining DAT-01 work remain open.

Sixth increment: DAT-01.01 now integrates explicit Parish audit ownership with
immutable configuration profiles, retaining deployment ownership for pre-upgrade
history and context-free pre-bootstrap events. Database insertion guards cover
all current emitters, reject conflicting attribution, and preserve plain campaign
UUIDs without deletion cascades. See the
[ownership boundary](../../guides/stewardship-audit-ownership.md).
PostgreSQL tests cover profile changes, concurrent activation, legacy upgrade,
refused populated downgrade, raw writes and rollback. DAT-01.02/.03/.05/.06 remain
open for their remaining work; this does not enable the future ARC-07 payload
services, production startup, or Gate 1. TaskRun chains are the next ready task.

Seventh increment: DAT-01.05 adds self-rooted logical task operations, optional
execution keys, protected retry parents and deduplicated retry commands, live
lease/heartbeat/fencing metadata, per-attempt monotonic progress and immutable per-version
attempt/transition evidence. PostgreSQL guards enforce canonical state edges,
terminality, chain uniqueness, immutable intent and atomic history/audit writes.
Internal transaction primitives require explicit domain admission callbacks;
they expose no operational queue, worker or provider action. See the
[storage boundary](../../guides/stewardship-taskrun-storage.md).
Tests cover raw writes, genuine expiry, stale owners, independent-connection
claim/retry races, rollback, privacy and migrations. BG-01 still owns actual
scheduling, service identity/admission, task-specific phases/retry budgets and
reconciliation. DAT-01.02/.03/.06 retain their remaining integration work.

The Phase 1A policy batch adds tagged operator-recovery requests, an additive
manual Admin patch format, bound operation replay, and the ordinary installer
checkpoint protocol without impersonating a portal actor. Activation atomically
revokes administration-session metadata and records revocation/audit/security
intents. The [foundation boundary](../../guides/stewardship-authorization-foundation.md)
identifies the still-required OPS-04 offline interlock/command, ARC-04 pending
OAuth revocation and session enforcement, notification delivery, and remaining
runtime/secret integration. DAT-01.02/.03/.06 therefore remain partial.

Phase 1A completion batch: the remaining campaign/mode runtime schema, durable
transition effects and exceptional configuration recovery are implemented. All
Phase 1A portions of DAT-01 are now present and tested; these mixed-phase task
checkboxes remain open only for ARC-04/ARC-06 session/secret consumer enforcement
and ARC-02/OPS-04 operational installation/startup. They do not block Phase 1B.
See the [completion evidence and integration contracts](../../guides/stewardship-phase-1a-completion.md).

## DAT-02: Campaign lifecycle and schedule schema

Scope and dependencies: [DAT-02 work package](../../plans/stewardship/data.md#dat-02-campaign-lifecycle-and-schedule-schema).

- [x] DAT-02.01 — Implement Campaign structure and immutable timezone snapshot.
- [x] DAT-02.02 — Enforce campaign constraints, read guards, and bounded download admission.
- [x] DAT-02.03 — Implement lifecycle records and durable activation catch-up demands.
- [x] DAT-02.04 — Implement schedules, fulfillment, holds, and post-close resolutions.
- [x] DAT-02.05 — Test campaign and schedule transition races.

Evidence: September 10, 2026 campaign batch adds immutable v3 CampaignConfiguration
and ScheduleRevision projections, module-dependent validation, periods/mappings,
share/content references, resolved boundaries and atomic Testing draft activation.
Historical schemas and additive recovery remain supported. Tests cover competing
requests, rollback, raw-write denial, timezone independence, removal history and
migrations. See the [integration boundary](../../guides/stewardship-campaign-foundation.md).
The completion batch now supplies live structural/lifecycle state, pinned
response-lifetime reads, deployment-wide download capacity, ordered boundaries,
fenced catch-up checkpoints/failures, schedule history/fulfillment, restore holds
and exact post-close coverage. PostgreSQL regressions exercise every lifecycle
race named in DAT-02.05, raw-write denial, replay, rollback and migration reversal.
See [validation and review evidence](../../guides/stewardship-phase-1a-completion.md#validation-and-reviews).
Storage task completion does not expose operational endpoints: DAT-04/06/07,
BG-01/02/04, ADM-05/06, OPS-04/06 and DAT-09/BG-11 retain the linked concrete
credential, submission/outbox, readiness, restore and purge integration.

## DAT-03: Versioned ParishSoft source corpus

Scope and dependencies: [DAT-03 work package](../../plans/stewardship/data.md#dat-03-versioned-parishsoft-source-corpus).

- [ ] DAT-03.01 — Implement normalized source snapshot records.
- [ ] DAT-03.02 — Implement immutable payload digests and deduplication.
- [ ] DAT-03.03 — Implement atomic source promotion and current indexes.
- [ ] DAT-03.04 — Implement fenced SourceMutationLease.
- [ ] DAT-03.05 — Implement compaction metadata and retention anchors.
- [ ] DAT-03.06 — Implement daily facts, rebuild-demand constraints, and compaction guards.
- [ ] DAT-03.07 — Test staging, promotion, facts, references, and compaction.

Evidence: Not started.

## DAT-04: Family campaign identity and credentials

Scope and dependencies: [DAT-04 work package](../../plans/stewardship/data.md#dat-04-family-campaign-identity-and-credentials).

- [ ] DAT-04.01 — Implement FamilyCampaign state and first-eligibility provenance.
- [ ] DAT-04.02 — Enforce code-fingerprint and token-digest uniqueness.
- [ ] DAT-04.03 — Implement atomic Family population and reconciliation.
- [ ] DAT-04.04 — Implement token generations, credential epochs, reservation records, and cleanup.
- [ ] DAT-04.05 — Test generation, rotation, and reactivation races.

Evidence: Phase 1B implements Family identities and eligibility/cohort provenance,
stable encrypted manual codes, versioned MAC lookup/collision constraints,
population reconciliation, sealed token generations, rehearsal epochs/reservations
and isolated sessions. Actual source promotion and submission/mail-state pointers
remain with DAT-03/DAT-06/DAT-07; do not fabricate those later tables. Additional
credential lifecycle/reactivation and retirement cases remain in progress. See
[Phase 1B evidence](../../guides/stewardship-phase-1b.md); the mixed-phase tasks stay
unchecked rather than claiming complete integration from the storage foundation.

## DAT-05: Portal users and authorization policy records

Scope and dependencies: [DAT-05 work package](../../plans/stewardship/data.md#dat-05-portal-users-and-authorization-policy-records).

- [ ] DAT-05.01 — Implement portal users, login rules, and assignments.
- [x] DAT-05.02 — Materialize policy from applied YAML versions.
- [x] DAT-05.03 — Enforce rule precedence and last-Administrator guards.
- [ ] DAT-05.04 — Implement provenance-aware chair seeds and runtime suspension overlays.
- [x] DAT-05.05 — Index login and Ministry authorization queries.
- [ ] DAT-05.06 — Test policy activation, source transitions, and idempotent autosave races.

Evidence: The Phase 1A policy batch adds immutable normalized rule/grant/
assignment projections, explicit provenance, verified identity metadata, runtime
overlays, lookup indexes, strict v2 schema/patch dispatch, last-Admin protection
and immediate exact-address/hosted-domain decisions. High-impact activation
commits security-notification intent and denial-namespace evidence atomically.
See the [foundation boundary](../../guides/stewardship-authorization-foundation.md).
DAT-05.01/.04/.06 remain partial: source-backed suggestions/review tasks,
Admin-confirmed seeds, promotion integration, actual login/audit/session
enforcement, and autosave/service admission races require Phase 1B/2 consumers.
The Phase 1A policy-schema portion is complete; these mixed-phase checkboxes
do not represent additional Phase 1A PRs.

## DAT-06: Immutable submissions and proposal overlay

Scope and dependencies: [DAT-06 work package](../../plans/stewardship/data.md#dat-06-immutable-submissions-and-proposal-overlay).

- [ ] DAT-06.01 — Implement immutable complete submission versions.
- [ ] DAT-06.02 — Persist submission baselines, cutoffs, and provenance.
- [ ] DAT-06.03 — Implement atomic validation and final submission.
- [ ] DAT-06.04 — Implement proposal states, writability, and supersession.
- [ ] DAT-06.05 — Test response variants, stale writes, and rollback.

Evidence: Not started.

## DAT-07: Follow-up, content, templates, jobs, and audit

Scope and dependencies: [DAT-07 work package](../../plans/stewardship/data.md#dat-07-follow-up-content-templates-jobs-and-audit).

- [ ] DAT-07.01 — Implement follow-up, Ministry, content, and template records.
- [ ] DAT-07.02 — Implement outbox, audit, logs, and semantic uniqueness.
- [ ] DAT-07.03 — Implement transition, hold, export, and publication records.
- [ ] DAT-07.04 — Enforce append-only history and terminal secret scrubbing.
- [ ] DAT-07.05 — Test corrections, terminal states, and audit privacy.

Evidence: Not started.

## DAT-08: Merge and source reconciliation services

Scope and dependencies: [DAT-08 work package](../../plans/stewardship/data.md#dat-08-merge-and-source-reconciliation-services).

- [ ] DAT-08.01 — Implement effective source/Family/Admin value merging.
- [ ] DAT-08.02 — Classify every merge and upstream catch-up outcome.
- [ ] DAT-08.03 — Preserve Family-submitted provenance through Admin edits.
- [ ] DAT-08.04 — Reconcile source promotions into derived workflows.
- [ ] DAT-08.05 — Test all merge branches and downstream effects.

Evidence: Not started.

## DAT-09: Publication, retention, and purge schema behavior

Scope and dependencies: [DAT-09 work package](../../plans/stewardship/data.md#dat-09-publication-retention-and-purge-schema-behavior).

- [ ] DAT-09.01 — Implement immutable publication plans and entity outcomes.
- [ ] DAT-09.02 — Implement purge requests, backup/recovery evidence, gates, and checkpoints.
- [ ] DAT-09.03 — Implement source, Testing, and campaign retention services.
- [ ] DAT-09.04 — Test publication/purge races, fencing, and recovery.

Evidence: Not started.
