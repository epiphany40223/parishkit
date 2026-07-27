# Stewardship operations and quality

This specification defines developer/production Compose environments,
credentials and durable volumes, release images, backup/restore, monitoring,
and validation. Shared ParishKit repository/release rules remain in the
[top-level specification](../../intro/spec.md).

## Compose files and images

The repository provides a base Compose definition plus explicit development
and production overlays/profiles. Running the documented development command on
Linux, macOS, or Windows starts a usable HTTP environment without TLS. Source,
templates, and static inputs are bind-mounted from the checkout so ordinary
changes reload without rebuilding the application image.

Production Compose references immutable GHCR image tags/digests, never a host
checkout. It includes web, worker, scheduler, PostgreSQL, Redis, and Caddy
services from the [architecture](../architecture/spec.md#technology-and-component-model).
Only Caddy publishes host ports. PostgreSQL/Redis are on an internal network;
worker/scheduler have no inbound public ports.

One application image contains package code/static build and supplies web,
worker, scheduler, migration, bootstrap, backup, and restore entry commands.
It runs as a non-root UID, has a read-only root filesystem where practical,
uses `/tmp`/declared volumes for writes, includes health checks, and handles
termination signals. Third-party database/proxy/broker images are official,
pinned major/minor lines, and updated to supported security patch releases.

Release-tag workflow builds `linux/amd64` and `linux/arm64` application images,
attests provenance, produces an SBOM, scans critical/high vulnerabilities, and
pushes `ghcr.io/<repository>/parishkit:<version>` plus the immutable commit tag.
It preserves existing Python sdist/wheel and GitHub Release behavior. A human
still explicitly authorizes release-tag push.

## Runtime storage

`PARISHKIT_ROOT` or `/opt/parishkit` remains the default path root:

| Path | Stewardship use |
| --- | --- |
| `config/` | Deployment YAML and non-secret Compose/operator files |
| `credentials/` | Google, ParishSoft, Slack, application, backup secrets |
| `cache/` | Tenant-scoped short-lived ParishSoft/cache artifacts |
| `logs/` | Optional JSONL/container log exports |
| `reports/` | Authorized temporary generated exports |
| `run/` | Locks, bootstrap markers, health/runtime state |

PostgreSQL data, Redis state, Caddy ACME state, and uploaded media use named
durable volumes or explicit operator-selected host paths. Every runtime path is
overridable by deployment CLI/YAML. Container replacement/restart/upgrade must
not remove any durable volume.

Credential files/directories use the shared restrictive modes. Compose secrets
may mount them read-only. Images, Compose files, logs, exceptions, backup
metadata, and support bundles never contain credential values.

## Production ingress and TLS

Caddy terminates TLS, redirects HTTP to HTTPS, obtains/renews public
certificates automatically through ACME/Let's Encrypt, serves static assets,
and proxies dynamic traffic. Production requires a DNS hostname pointing to the
VM and inbound ports 80/443. Caddy's data/config volumes persist account and
certificate state across upgrades.

The application trusts forwarded scheme/client information only from the
single configured proxy hop. Caddy access logs redact `/access/<token>` path
segments and do not log cookies/query secrets. Upload/body/time limits protect
the app without blocking configured logo/export workflows.

Local development binds an unprivileged HTTP port and uses localhost Google
OAuth redirect registration. TLS remains optional locally.

## Startup and upgrades

Documented first deployment order is:

1. Create operator-owned config/credential/volume locations.
2. Start PostgreSQL/Redis and verify health.
3. Run `pk-stewardship migrate` as a one-shot container.
4. Run `pk-stewardship bootstrap` if not restoring.
5. Start web/worker/scheduler/proxy.
6. Complete the first-Admin wizard.

Application containers do not race to run migrations. A production upgrade
requires a successful recent backup, pulls pinned images, runs migration checks
and migrations, then restarts services. Migrations must be forward-safe for the
declared rollout; destructive column removal follows expand/migrate/contract
across releases.

Rollback instructions distinguish application rollback (only when schema is
compatible) from database restore. Startup refuses an unsupported newer schema
or missing credential file and reports a sanitized actionable error.

## Backup

The application provides a scheduled/operator command that creates one
consistent backup set containing:

- PostgreSQL logical/custom-format dump and schema/version metadata;
- uploaded media/branding required by retained campaigns;
- deployment configuration needed to locate services; and
- credential files only when the approved encrypted target is authorized to
  hold them, otherwise an explicit credential manifest/fingerprint list.

Backups are encrypted before leaving the VM and transferred to an
operator-configured off-host target. Defaults retain 30 daily and 12 monthly
successful backups. Failure to complete a successful backup within 24 hours is
CRITICAL. Backup logs contain sizes/digests/durations, never contents/secrets.

Backup creation uses PostgreSQL-supported consistency; copying a live data
directory is prohibited. A manifest has application version, schema migration,
files and cryptographic digests. Partial uploads never appear as successful
backup references.

## Restore

Restore is operator-driven and unavailable as an ordinary web action. It
requires application downtime or a new empty deployment target, exact backup
selection, decryption credentials, and destructive confirmation of the target.

The restore command verifies manifest/digests, application/schema compatibility,
credential availability, and target emptiness before writing. It restores
database/media/config, runs permitted forward migrations, validates one parish,
checks expected ParishSoft organization without mutation, and starts in Testing
mode with workers/mail disabled until an Admin completes readiness review.

Quarterly restore drills restore to an isolated environment, run integrity and
application checks, and record success/failure metadata. The target recovery
point objective is 24 hours; recovery time is documented/measured rather than
promised as HA.

The most recent successful off-host backup reference is exposed to the guarded
campaign purge workflow. A stale/missing backup blocks purge.

## Temporary retention and housekeeping

Generated export files expire after seven days; metadata remains. ParishSoft
HTTP cache follows configured freshness and bounded size. Upload staging,
failed wizard staging, old static bundles, expired sessions, worker results,
and rotated operational logs have documented cleanup jobs.

Cleanup is idempotent, scoped to explicit subdirectories/records, and cannot
follow unsafe symlinks or broad/unresolved paths. It never deletes promoted
snapshots, submissions, audit history, or backups under an operational cache
policy.

Live campaign data otherwise remains indefinitely until the Admin web purge
defined by the [Admin specification](../admin-portal/spec.md#campaign-purge).

## Observability and health

Containers log structured JSON to stdout/stderr with correlation IDs and safe
context. Application operational/audit storage is separately queryable in the
Admin UI. Metrics include request latency/error, sessions, queue depth/age,
task duration/failure, scheduler lag, outbox age/delivery, ParishSoft snapshot
age, database/broker health, disk usage, backup age, and TLS expiry.

`/health/live` confirms the web process loop only. `/health/ready` confirms
database, migrations, critical credential presence, and current configuration;
it must not call external services per probe. Worker/scheduler health uses
heartbeats and queue-lag records.

No health/metrics endpoint exposes parish names, Family/Member data, emails,
tokens, campaign content, or credentials. Production metrics endpoints are
internal/authenticated.

## Automated tests

Normal CI requires no real ParishSoft, Google, email, Slack, backup, or other
external credential and makes no live network calls. Dependencies are injected
and external responses use fakes/redacted fixtures.

`pytest-cov` enforces at least 80% line coverage across `src/parishkit`, with
authorization, Family credential verification, submission transaction,
three-way reconciliation, outbox idempotency, ParishSoft publication, and purge
state transitions receiving exhaustive branch-oriented tests.

Required suites include:

- pure unit tests for validation, normalization, dates/DST, money, percentages,
  role precedence, state machines, merge/diff, report calculations, and export
  escaping;
- Django request tests for every role/denial/object-scope and CSRF/session
  boundary;
- PostgreSQL integration tests for constraints, transactions, concurrent
  submissions, task claims, snapshot promotion, publication, and purge rollback;
- worker tests for retry/idempotency, partial failure, missed schedules, and
  abandoned-task recovery;
- email rendering/routing tests for live/testing/recipient/privacy behavior;
- browser tests for setup, Admin/Staff/leader workflows and the full responsive
  Family path, including stale submit and repeat visit;
- accessibility automation plus keyboard/screen-reader-oriented manual checks;
- CSV/XLSX/PDF/PNG structure/content tests without committing generated reports;
- backup/restore manifest and isolated restore smoke tests; and
- Compose startup, health, migration, bind-mount reload, and production image
  smoke tests.

## Acceptance scenarios

At minimum, end-to-end tests demonstrate:

1. Empty deployment through bootstrap/wizard, aborted wizard rollback, and
   restored deployment startup.
2. Google allow/deny, exact-address override, last-Admin guard, immediate role
   revocation, and assigned-Ministry scoping.
3. Testing email rerouting, segregated test submission, guarded deletion, and
   exactly-once live catch-up on Production transition.
4. Full/delta refresh success, interrupted/invalid load retaining prior truth,
   new/inactive/reactivated Family behavior, and non-overlap/manual coalescing.
5. No-change Family submission, every census field, proposed/terminal Member,
   Ministry request, zero/positive pledge, additional information, repeat
   submission, and stale concurrent submit.
6. Upstream catches up, three-way conflict, Admin edited approval, preflight,
   partial ParishSoft write failure/retry, read-after-write, and unsupported
   manual resolution.
7. Every report's access, counts/percentages, inactive/test exclusion, privacy
   columns, filters, chart parity, and CSV/XLSX/PDF/PNG output.
8. Missed initial/reminder/digest occurrence, worker/broker restart, systemic
   email failure, deduplicated CRITICAL notification, and recovery.
9. Closed campaign explicit reopen and archive.
10. Web purge blocked for active campaign/stale backup/wrong confirmation;
    successful transactional purge/tombstone; database rollback; retryable file
    cleanup; and failure notification.

## CI and local validation

The existing required commands remain:

```text
python -m ruff check .
python -m ruff format --check .
python -m pymarkdown --config .pymarkdown.json scan $(git ls-files '*.md')
python -m pytest
```

CI adds the coverage threshold, migration drift check, frontend static/build
check, browser/accessibility job, Compose smoke job, and image build/scan. Fast
unit checks remain useful locally; slow integration/browser/container jobs are
documented and reproducible before a pull request is considered complete.
