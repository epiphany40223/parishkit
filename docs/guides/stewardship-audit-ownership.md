# Stewardship audit ownership foundation

This implements DAT-01.01's ownership portion alongside the existing
[storage conventions](stewardship-database-tests.md). The
[data specification](../specs/stewardship/data/spec.md#core-records) owns the
normative record contract; [task evidence](../tasks/stewardship/data.md#dat-01-storage-conventions-and-base-records)
and [milestones](../tasks/stewardship/milestones.md#audit-ownership-increment)
track completion and review results.

## Attribution contract

`AuditEvent.ownership_scope` is either `deployment` or `parish`. Parish-owned
events protect one immutable `Parish` profile through a database foreign key.
The profile's `record_id` is the stable parish identity across configuration
versions. Its primary key identifies the historical profile, not a separate
tenant. Existing events are never reassigned after a configuration change.

The database resolves ownership in the same INSERT transaction as the event:

| Event context | Profile selection |
| --- | --- |
| `config_request_*` | The request's immutable base configuration, including its final Applied checkpoint |
| `configuration_activated` | The activation's selected configuration |
| All other events, including `secret_request_*` | The active runtime configuration visible to the attribution statement |
| No active runtime and no explicit request/activation context | Deployment ownership with no Parish reference |
| Resolved `bootstrap-policy-v1` configuration without a Parish profile | Deployment ownership until the setup owner supplies a real profile |

A prepared candidate alone does not become the active authority. A configuration
request can nevertheless have explicit base ownership before bootstrap activates
the runtime. Missing request/activation context or a missing required Parish
projection fails closed, except for the explicit minimal-bootstrap case installed
by accounts migration 0034. See the [bootstrap boundary](stewardship-phase-1c.md#runtime-primitives-and-initial-authority).
Caller-provided conflicting profiles or unknown scopes are rejected. Omitting
the fields, or submitting their deployment defaults, cannot force deployment
ownership when the trigger resolves a Parish.

Concurrent activation may change which committed profile a later statement sees.
The selected profile is immutable, and earlier audit events retain their original
reference. Attribution is not authorization, YAML coherence validation, or a
runtime readiness check. Those checks remain with each owning workflow.

The field database defaults let existing SQL checkpoint emitters omit these new
columns. Django's INSERT RETURNING returns trigger-derived ownership to both
ordinary and bulk ORM callers without a manual refresh. There is no arbitrary
payload, credential, URL, source value, or error-message field in this envelope.
The supported database schema is `public`. The attribution function fixes its
search path to trusted catalog/application schemas with temporary tables last;
caller search-path changes cannot substitute a shadow runtime/profile table.
Custom application schemas and runtime database-role grants remain OPS-04/OPS-02
integration work, not a supported configuration of this foundation.
This protects attribution after an INSERT reaches the real audit table. Earlier
checkpoint/activation/secret SQL emitters still use unqualified audit targets;
their pre-existing search-path exposure is tracked as a
[required OPS-02 integration check](../tasks/stewardship/operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets)
before runtime roles or production are enabled. This is not a claim of end-to-end
protection against arbitrary SQL sessions that can create shadow tables.

## Historical retention and migrations

`campaign_reference` is an optional plain UUID, never a Campaign foreign key.
It cannot appear on a deployment-owned event. Parish-owned history therefore
does not become campaign-owned or cascade away with a campaign. Future report
and purge workflows own campaign admission, reference validation, safe event
payloads, and tombstone presentation; setting this reference grants no access.

Migration `stewardship_audit.0006_parish_ownership` adds fields and the attribution
trigger atomically. Previously persisted rows keep their original values and
receive explicit deployment ownership via column defaults. It does not UPDATE
append-only history or guess historical attribution from today's configuration.

Empty or deployment-only bootstrap history can reverse/reapply this migration
when no retained activation, installer checkpoint or secret-request history would
block a dependent accounts downgrade. Otherwise reversal refuses before removing
the trigger, columns or migration marker, including on an upgraded legacy
deployment whose old audit events are still deployment-owned. Django reverses
each migration in its own transaction; checking those older populated-history
blockers here prevents a later refusal from stranding the ownership schema.
The optional secret table is inspected only if present. Forward corrections,
not destructive history rewriting, are the recovery path. No operator command
or UI for bypassing this guard is added.

## Verification and remaining boundaries

The real PostgreSQL suite in `test_audit_ownership_postgresql.py` covers raw and
ORM inserts, historical profile selection, unknown/missing context rejection,
immutable/protected references, legacy upgrade, refused populated downgrade,
transaction rollback on damaged projections, and independent connections across
an activation. Existing activation/secret tests continue to verify atomic audit
and state effects. Follow the [database test guide](stewardship-database-tests.md)
for the disposable test profile; no real provider credentials are required.

ARC-07 still owns actor types, validated event-specific payloads, source-IP policy,
and application privacy primitives. DAT-07/RPT-09 own the complete audit/log and
reporting workflows. DAT-09 owns purge integration. This foundation does not
enable any portal, production startup, export, purge, or authorization capability,
and does not complete Phase 1 or release Gate 1.
