# Stewardship authorization foundation

This Phase 1A batch implements the policy side of
[DAT-05](../plans/stewardship/data.md#dat-05-portal-users-and-authorization-policy-records),
[DOM-03](../plans/stewardship/campaign-domain.md#dom-03-authorization-capability-policy),
and DAT-01's offline-recovery record/activation contract. It also supplies the
[DOM-02 interval resolver](../../src/parishkit/stewardship/campaigns/intervals.py).
The normative [role matrix](../specs/stewardship/spec.md#actors-and-authorization)
and [identity rules](../specs/stewardship/architecture/spec.md#identity-and-session-security)
control behavior; this guide describes implementation boundaries, not new policy.

## Integrated behavior

`foundation-policy-v2` adds strict `login_rules` to immutable configuration
snapshots. Domain rules, exact-address rules, per-role provenance, and Ministry
assignments are projections of those exact YAML records. PostgreSQL checks
projection equality and deferred completeness. Historical v1 snapshots and
request parsers retain their original discriminators and retry behavior.

Ordinary policy requests use the existing serialized installer. Exact-email
manual provenance refers to the request UUID returned by
`policy_operation_id(actor_id, request_key)`; new provenance is checked at both
intake and installer validation. Existing origins remain unchanged. Exact-email
overrides, including explicit denial, replace hosted-domain roles. Seed-only
Ministry scope needs an active source overlay; missing or suspended overlays
fail closed. Manual origins and assignments remain independent. The installer
does not manufacture Chairperson provenance. Snapshot verification preserves
retired record identities and checks every ancestor's projections.

The pure capability and report-column helpers cover Admin implication, Staff
workflow exceptions, assigned-Ministry scope and separate Family-own scope.
`current_principal` reloads coherent applied policy and current overlays; its
result is a point-in-time decision, not a credential or mutation authorization.

Policy activation commits high-impact security-event/notification intents and
denial-counter namespace generations in the same transaction as the active
pointer and request receipt. Failed intent persistence rolls back activation;
acknowledgement retries do not duplicate events. These are durable intents, not
claims that email was delivered or that a banner was acknowledged.

## Offline recovery boundary

`operator_recovery.recover_admin` is an internal protocol with no route, command,
worker, or provider exposure. It requires an offline interlock context manager
from the future OPS-04 operator command. That command must prove online services
are stopped, prevent concurrent startup and concurrent recovery for the entire
operation, show the minimal change, and collect explicit deployment/target
confirmation. Tests use only an isolated disposable deployment; their synthetic
context manager is not suitable for an operational caller.

Recovery records a unique operation and operator name/reason without a fabricated
PortalUser. It adds only an exact-address manual Admin grant, preserving other
roles and provenance. The same installer checkpoints support crash recovery.
Activation atomically revokes existing administration session metadata, appends
the Admin revocation generation and audit, and includes both prior Admins and
the replacement in its security-notification intent. The ordinary installer
entry point refuses recovery requests. Replays still acquire the interlock and
verify bound intent and YAML/database coherence.

ARC-04 must consume the revocation generation for pending OAuth state and enforce
session revocation on every request. There are no operational OAuth states yet.
OPS-04 must supply the real offline interlock and operator UI before exposing
recovery. Neither the synthetic tests nor stored UUIDs establish authority.

## Remaining integration

- ARC-04/ADM-07: Google verification, current-role and CSRF admission, session
  enforcement, fresh-auth workflows, serialized autosave, and alert acknowledgement.
- DAT-03/BG-05/ADM-07: confirmed chair seeds, source-generation validation,
  source-driven overlay updates, suggestions and review tasks.
- BG-10: deliver/retry security intents; ARC-04: use denial namespace generations
  without resetting per-IP abuse limits.
- DAT-02/remaining DOM-02: campaign records, lifecycle transactions, shared read
  guards, schedules and exact-boundary admission using the pure resolver.
- DAT-01/ARC-02/OPS-04: remaining runtime and secret-installation integration.

Production services still refuse startup. No authentication endpoint, recovery
command, Family portal, mail delivery, lifecycle transition or gate is enabled.
This is a coherent policy/installer subphase, not completion of Phase 1A or G1.
Campaign/lifecycle/schedule storage is the next coherent batch; keeping that
separate avoids reviewing two independently substantial state-machine changes
in the same PR. This batch groups models, migrations, policies, installer effects,
failure recovery and tests rather than submitting them as separate PRs.

## Verification

The [database test guide](stewardship-database-tests.md) describes disposable
PostgreSQL setup. Run the complete coverage runner, plus Ruff, Markdown,
migration drift and opt-in Docker checks. Focused tests are
`test_policy.py`, `test_intervals.py`, `database/test_policy_postgresql.py` and
`database/test_recovery_postgresql.py`. Exact validation counts and three-round
review dispositions belong in the [milestone record](../tasks/stewardship/milestones.md).
