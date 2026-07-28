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
checkout. It includes web, general worker, dedicated mail-dispatch worker,
scheduler, configuration installer, target-specific credential installers,
PostgreSQL, Valkey, and Caddy services from the
[architecture](../architecture/spec.md#technology-and-component-model). Only
Caddy publishes host ports. PostgreSQL/Valkey are on an internal network;
workers/scheduler have no inbound public ports. Development Compose preserves
the same key-mount and queue separation.

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
| `config/` | Deployment YAML, versioned Stewardship authority, active manifest, and non-secret Compose/operator files |
| `credentials/` | Google, ParishSoft, Slack, application, backup secrets |
| `cache/` | Tenant-scoped short-lived ParishSoft/cache artifacts |
| `logs/` | Optional JSONL/container log exports |
| `reports/` | Authorized temporary generated exports |
| `run/` | Locks, bootstrap markers, health/runtime state |

PostgreSQL data, Valkey state, Caddy ACME state, and uploaded media use named
durable volumes or explicit operator-selected host paths. Every runtime path is
overridable by deployment CLI/YAML. Container replacement/restart/upgrade must
not remove any durable volume.

Credential files/directories use the shared restrictive modes. Compose mounts
each secret read-only only into services that need it; mounting the whole
credentials directory into an application service is prohibited. In
particular, token public keys are available to `web`/general workers, while
token private keys and Google mail-provider credentials are mounted only into
`mail-dispatch` or the explicit rotation profile. The scheduler, web, general
worker, report/export jobs, and ordinary maintenance commands cannot read the
private token-key path. Images, Compose files, logs, exceptions, backup
metadata, and support bundles never contain credential values.

Only `config-installer` mounts `<root>/config/stewardship` read-write; other
online services either mount its active manifest/versions read-only for startup
verification or consume the matching PostgreSQL materialization. Each
`credential-installer-*` instance mounts a separate target subdirectory read-
write plus only its own handoff private key and queue. Consumers mount the
resulting individual credential file read-only. Neither installer class receives
the whole credentials directory, broad host paths, Docker socket, campaign
answers, or unrelated secrets. Compose and runtime tests inspect these mounts
and service identities.

The application backup container receives only its target credential and active
data-backup encryption key as individual read-only mounts. It receives no OAuth,
ParishSoft, mail, Slack, Django, general-encryption, or token-key secret. The
backup manifest records credential/key fingerprints and key IDs only. All
credential files, including every retained data-backup decryption key version,
are protected by the separately authorized operator secret-escrow process below;
copying them into an application database, report, or ordinary backup task is
prohibited.

`pk-stewardship backup-secrets` runs only in an explicit operator profile with
the credential files mounted read-only and no application database access. It
creates a versioned manifest and encrypted secret bundle at a distinct off-host
escrow target. The bundle is encrypted to one or more operator-controlled
recovery public keys or an equivalent external recovery service; the
corresponding private recovery material is never stored on the application VM
and the bundle is never encrypted solely by a key contained within itself.
Restore drills verify that an authorized operator can combine a data-backup
manifest with the matching secret bundle without exposing secret values in
logs. Normal application services cannot invoke this profile.

## Production ingress and TLS

Caddy terminates TLS, redirects HTTP to HTTPS, obtains/renews public
certificates automatically through ACME/Let's Encrypt, serves static assets,
and proxies dynamic traffic. Production requires a DNS hostname pointing to the
VM and inbound ports 80/443. Caddy's data/config volumes persist account and
certificate state across upgrades.

Caddy has explicit highest-priority matchers that return the ordinary public
not-found response for `/health/live` and `/health/ready` before the catch-all
application reverse proxy. Container health checks call the application service
directly over the internal Compose network.

The application trusts forwarded scheme/client information only from the
single configured proxy hop. Caddy access logs redact `/access/<token>` path
segments and do not log cookies/query secrets or request bodies. Exact Family-
code searches are POST-body-only and therefore never enter access-log URLs.
Upload/body/time limits protect the app without blocking configured logo/export
workflows. The official stock
Caddy image is used without third-party rate-limit modules; coarse and specific
administration-login limits are application middleware defined by the
[identity security policy](../architecture/spec.md#identity-and-session-security).

Local development binds an unprivileged HTTP port and uses localhost Google
OAuth redirect registration. TLS remains optional locally.

## Startup and upgrades

Documented first deployment order is:

1. Create operator-owned config/credential/volume locations.
2. Start PostgreSQL/Valkey and verify health.
3. Run the pre-migration phase of `pk-stewardship bootstrap` if not restoring;
   it creates/validates deployment configuration, the minimal initial-Admin
   Stewardship YAML authority, installer handoff keys, and signing/encryption
   secret files without requiring application tables.
4. Run `pk-stewardship migrate` as a one-shot container, then let bootstrap
   validate the migrated empty database and import the initial applied YAML
   snapshot/Admin marker.
5. Start web/worker/scheduler/installers/proxy.
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

The application provides a shared backup service invoked by its scheduled task,
operator command, or guarded campaign-purge web workflow. Each invocation
creates one consistent backup set containing:

- PostgreSQL logical/custom-format dump and schema/version metadata;
- uploaded media/branding required by retained campaigns;
- deployment configuration plus every retained Stewardship YAML version and
  active manifest needed to match database configuration snapshots; and
- an explicit credential/key manifest and fingerprint list, never credential
  values or files.

Backups are encrypted before leaving the VM and transferred to an
operator-configured off-host target. Defaults retain 30 daily and 12 monthly
successful backups. Failure to complete a successful backup within 24 hours is
CRITICAL. Backup logs contain sizes/digests/durations, never contents/secrets.

Each data-backup manifest records its encryption-key ID. Rotation stages a new
data-backup key, successfully escrows and verifies the updated credential set,
then activates the key for new backups. Every old decryption key remains in
verified off-host escrow until all backups using it expire or are re-encrypted;
retirement is blocked otherwise. The data-backup key never encrypts its own
secret-escrow bundle.

Backup creation uses PostgreSQL-supported consistency; copying a live data
directory is prohibited. A manifest has application version, schema migration,
database-snapshot instant, files, and cryptographic digests. Partial uploads
never appear as successful backup references.

The purge web action can enqueue this service only for an Admin-owned
PurgeRequest that has reached quiescence. The web process never receives backup
credentials or performs the backup inline; the authorized worker reads the
existing credential reference. Purge-triggered backups follow ordinary
retention and are additionally referenced immutably by the PurgeRequest.

## Restore

Restore is operator-driven and unavailable as an ordinary web action. It has two
explicit modes. Empty-target restore is the default and refuses any existing
application data. In-place disaster recovery requires application downtime, an
explicit replace-existing option, exact target identity and backup selection,
and a separate destructive confirmation; it never infers permission from a
non-empty target.

Before either restore mode begins, the operator restores the manifest-matching
credential set from independently held secret escrow and verifies fingerprints
without printing values. Missing escrow, recovery material, or a required
historical data-backup key blocks restore with a sanitized diagnostic.

Both modes verify manifest/digests, application/schema compatibility, credential
availability, and the target-mode precondition before writing. They restore
database/media/config, run permitted forward migrations, and require the
restored active Stewardship YAML digest to match an applied/prepared database
configuration snapshot. A recoverable installer checkpoint is completed
idempotently; an unexplained mismatch blocks readiness and requires operator
diagnosis rather than choosing either copy. Restore then validates one parish,
checks expected ParishSoft organization without mutation, and starts in Testing
mode with the scheduler, ordinary worker admission, production outbox dispatch,
and Family mail disabled. Before the web service becomes externally ready,
restore atomically sets the durable `restore_review_required` gate. That gate
blocks every Family authentication, access-token exchange, form, and submit
route regardless of campaign dates or Testing behavior; public requests receive
a neutral parish-branded maintenance page without Family-specific information.

Administration login and the restore-readiness workflow remain available. A
restricted maintenance worker pool/queue runs while the gate is closed and may
claim only tenant-validation and read-only full refresh, integration tests,
mail routed to the configured Testing recipient, restore inventory/hold
calculation, backup verification, integrity/health diagnostics, operational
notifications, and explicitly authorized recovery of an interrupted purge.
It cannot materialize live schedules, dispatch a `production` outbox row,
publish/write to ParishSoft, generate ordinary campaign exports, or claim other
restored work. Every task carries a restore-maintenance type checked at durable
creation and worker claim; routing to the queue alone is not authorization.

The gate can be cleared only after applicable readiness passes and a freshly
authenticated Admin reviews and confirms the proposed state-aware release
defined by the
[Admin workflow](../admin-portal/spec.md#restore-release). A release that yields
a sole current `scheduled`, `active`, or `closed` campaign enters Production;
`closed` admits only its ordinary post-campaign work and does not enable Family
access or live Family mail. A `draft`, archived/purged history, or no current
campaign releases into Testing. Clearing the gate, reconciling lifecycle state,
selecting mode, materializing holds, and enabling the corresponding work
admission are one audited transaction. Failed or abandoned review leaves the
restore gate, Family access, and live delivery disabled while restricted
maintenance work remains available.

Restore review calculates a delivery-uncertainty window from the backup's
database-snapshot instant through the eventual mail-release instant. It creates
durable `RestoreDeliveryHold` rows for every reconstructable campaign delivery
that could have become due in that interval but whose outcome is absent from the
backup. Full source refresh during the gate expands the inventory to newly
visible Families whose already-due initial invitation may have been delivered
after the snapshot. Refresh never materializes or dispatches an ordinary
initial invitation while the gate is active. Immediately before release, the
transition recomputes and atomically materializes the applicable initial
occurrence plus a hold whenever its restored delivery is uncertain; inability
to complete that inventory leaves the gate closed.

Unreviewed holds are safe at release because they suppress only the uncertain
semantic occurrence, not future distinct schedules. They never apply to
operational notifications. The Admin can later resolve each hold as assumed
delivered or authorize resend after acknowledging duplicate risk; neither the
restore command nor readiness workflow may globally treat unknown delivery as
provider success.

Quarterly restore drills restore to an isolated environment, run integrity and
application checks, and record success/failure metadata. The target recovery
point objective is 24 hours; recovery time is documented/measured rather than
promised as HA.

Campaign purge accepts only the verified, post-quiescence backup created for
that PurgeRequest. Ordinary scheduled backup references never satisfy purge
readiness.

## Temporary retention and housekeeping

Generated export files expire after seven days by default; their metadata
remains. This is the normative retention policy used by the
[export worker](../background-processing/spec.md#exports-and-graph-rendering).
ParishSoft HTTP cache follows configured freshness and bounded size. Upload
staging, failed wizard staging, old static bundles, expired sessions, worker
results, and rotated operational logs have documented cleanup jobs.

Cleanup is idempotent, scoped to explicit subdirectories/records, and cannot
follow unsafe symlinks or broad/unresolved paths. Generic operational-cache
cleanup never deletes source snapshots, submissions, audit history, or backups.
Only the dedicated source-compaction service may thin unprotected snapshot
corpora, under the normative retention and reference guards in the
[data specification](../data/spec.md#source-snapshot).

Live campaign data otherwise remains indefinitely until the Admin web purge
defined by the [Admin specification](../admin-portal/spec.md#campaign-purge).

## Observability and health

Containers log structured JSON to stdout/stderr with correlation IDs and safe
context. Application operational/audit storage is separately queryable in the
Admin UI. Metrics include request latency/error, sessions, queue depth/age,
task duration/failure, scheduler lag, outbox age/delivery, ParishSoft snapshot
age, database/broker health, disk usage, backup age, and TLS expiry.

`/health/live` confirms the web process loop only. `/health/ready` confirms the
database, migrations, Valkey limiter store, and configuration needed for the
deployment's current setup phase; it must not call external services per probe.
A bootstrapped but product-unconfigured deployment is ready when it can safely
serve login and the first-Admin wizard, even though campaign/integration
readiness is incomplete. After the wizard commits the configured marker,
readiness additionally requires the critical credential references and durable
configuration for normal operation. Worker/scheduler health uses heartbeats and
queue-lag records.

Container restart health checks use `/health/live`, not `/health/ready`.
Readiness is an operator and alerting signal only; no proxy or orchestrator
removes this single application instance from traffic when it fails. Admission
middleware independently fails closed for guessable-credential authentication
when Valkey is unavailable, without restarting an otherwise live web process;
existing authenticated sessions and opaque-token exchange can remain available
as specified by the architecture.

The two HTTP health routes are internal-only and return no phase or reason
detail. `pk-stewardship health` provides detailed operator diagnostics on the VM
without creating a public endpoint.

No health/metrics endpoint exposes parish names, Family/Member data, emails,
tokens, campaign content, or credentials. Production metrics endpoints are
internal/authenticated.

## Automated tests

Normal CI requires no real ParishSoft, Google, email, Slack, backup, or other
external credential and makes no live network calls. Dependencies are injected
and external responses use fakes/redacted fixtures.

`pytest-cov` always measures `src/parishkit/stewardship` plus the exact shared
`src/parishkit` module paths listed in the checked-in
`coverage-stewardship.toml` manifest. The manifest may add shared modules but
cannot remove the stewardship package; missing, duplicate, non-Python, or
out-of-repository paths fail CI. Any shared module implemented or materially
extended for stewardship must be added to the manifest in the same change;
pre-existing unrelated tools remain excluded.

Coverage runs with branch measurement. CI reads machine-readable coverage
output and independently requires at least 80% line coverage and at least 80%
branch coverage across the combined manifest scope; a blended percentage cannot
mask either failure. Authorization, Family credential verification and
access-token exchange, submission transaction, three-way reconciliation,
outbox idempotency, ParishSoft publication, encryption/signing-key rotation,
secret replacement, rate limiting, and purge state transitions receive
exhaustive branch-oriented tests.

Required suites include:

- pure unit tests for validation, normalization, dates/DST including
  midnight-gap/fold campaign boundaries, money, percentages, role precedence,
  state machines, merge/diff, report calculations, and export escaping;
- scheduler/transaction tests for idempotent campaign-boundary occurrence
  creation, exact interval gating despite state lag, start/close lock races,
  end-date replacement, retry, and overdue recovery after scheduler outage;
- Django request tests for every role/denial/object-scope and CSRF/session
  boundary;
- authentication tests proving that domain rules require matching verified
  email and signed Google hosted-domain claims, while exact-address rules do not;
- configuration-authority tests for canonical YAML, schema migration, stale
  base-digest denial, immutable version/manifest activation, crash at every
  installer checkpoint, exact YAML/database digest recovery, fail-closed
  unexplained mismatch, and rollback-as-new-version;
- administration-login tests for early-middleware token-bucket and specific
  application thresholds, trusted source address handling, rejection before
  OAuth state/session allocation, keyed identity counters, progressive
  `Retry-After`, distributed-abuse notification, recovery after window expiry,
  fail-closed limiter-store outage, empty-window recovery after counter loss,
  and denial-counter namespace reset after an authorizing rule change without
  clearing IP counters;
- security tests for Family code normalization, reduced-alphabet generation,
  full-A-Z lookup candidates, malformed-attempt accounting, IP/code-pair and
  per-IP throttling, distributed-guessing detection and recovery, successful
  access during an attack from other addresses, fail-closed limiter-store
  outage, continued opaque-token access with 120-per-minute/burst-30 limiting
  and bounded per-process fallback, generic/audited invalid-token handling,
  access-token exchange and revocation plus digest uniqueness/index use,
  MAC dual-read rotation/backfill/cross-key collision/retirement, encryption and
  signing-key rotation/migration/retirement, target-key-sealed secret staging,
  expiry/destruction, cross-target claim denial, consumer fingerprint
  acknowledgement, absence of plaintext from storage/logs, and atomic secret
  replacement rollback;
- reusable-token tests for digest-only exchange, public-key sealing,
  dispatch/rotation-only private-key mounts and decryption, failure to decrypt
  from web/general-worker service profiles, repeat-mail rendering, atomic token
  rotation, ineligibility/reactivation, and ciphertext destruction plus new-
  token issuance across close/reopen;
- code-report tests for Admin/Staff-only direct display, exact-code lookup,
  no-store responses, bulk CSV/XLSX/PDF inclusion, report/export audit without
  code values, denial to Ministry leaders, continued availability during
  limiter-store outage, and manual-code inclusion in the no-deliverable-email
  mail-merge export;
- security-content tests using sanitizer allow/deny corpora, upload signature
  and media-type rejection, image re-encoding/decompression bounds, and
  template placeholder validation for both correct substitution and unknown-
  placeholder rejection;
- session tests distinguishing passive heartbeats from the untrusted activity
  keepalive, including CSRF enforcement, hostile-client rate limiting, idle
  renewal, empty payload enforcement, and absolute-expiry denial;
- PostgreSQL integration tests for constraints, transactions, concurrent
  submissions, task claims, source-mutation lease fencing/heartbeat/takeover,
  stale-owner promotion/PUT denial, snapshot promotion, publication, and purge
  rollback;
- worker tests for retry/idempotency, partial failure, missed schedules, and
  abandoned-task recovery, plus schedule replacement/removal races proving
  atomic cancellation, sealed-credential scrubbing on every terminal outbox
  state, direct-activation coalescing, and cross-revision fulfillment;
- Valkey integration tests using the production major/minor line for Celery
  broker delivery, cache operations, atomic sliding-window/token-bucket scripts,
  expiry, restart with accepted counter loss, and fail-closed outage behavior;
- email rendering/routing tests for live/testing/recipient/privacy behavior,
  provider acceptance followed by timeout, provider-status reconciliation,
  idempotent safe retry, unresolved `delivery_unknown`, authorized resend, and
  immutable operational-notification classification/bypass in Testing;
- delivery-pause race tests for pre-provider recheck, continued live submission,
  held receipt creation, immutable operational/test exemption, no Testing
  reroute, atomic backlog coalescing/resume, close-during-pause cancellation,
  and post-close held-message release/cancellation before archive;
- browser tests for setup, Admin/Staff/leader workflows and the full responsive
  Family path, including stale submit and repeat visit;
- browser and authorization tests proving that role checkbox changes autosave
  without reauthentication or a confirmation dialog while enforcing CSRF,
  optimistic concurrency, current-Admin authorization, last-Administrator
  protection, and complete audit records;
- limiter tests proving that rejected Admin callback attempts contribute once
  to distributed-abuse telemetry without OAuth state allocation, provider
  calls, raw token retention, or duplicate counting;
- accessibility automation plus keyboard/screen-reader-oriented manual checks;
- CSV/XLSX/PDF/PNG structure/content tests without committing generated reports;
- backup/restore manifest and isolated restore smoke tests, including
  uncertainty-window inventory, newly discovered Families, atomic release-time
  hold creation, catch-up suppression, assumed-delivered accounting, and
  duplicate-aware authorized resend; and
- Compose startup, health, migration, bind-mount reload, and production image
  smoke tests, including proof that internal health succeeds while Caddy does
  not proxy either health path.

## Acceptance scenarios

At minimum, end-to-end tests demonstrate:

1. Empty deployment through bootstrap/wizard, aborted wizard rollback, restored
   deployment startup with Family access gated, restricted maintenance refresh/
   test-send/hold work while ordinary work remains blocked, and atomic Admin-
   approved release of restored `draft`, future `scheduled`, current `active`,
   expired-to-`closed`, and already `closed`/`archived` campaigns. Both closed
   cases remain Production without Family/live-Family-mail access; draft,
   archived, purged, and no-current cases release to Testing. Interrupted purge
   state blocks release pending explicit recovery.
   First-Admin setup additionally proves that correlated source-load polling
   renews idle only with a current worker heartbeat, never renews the two-hour
   watchdog or absolute expiry, stops renewing when the page/task ends, and
   watchdog/expiry cleanup prevents a late worker from restoring discarded
   staging. A simulated two-hour overrun is treated as a failed/stuck import and
   leaves redacted diagnostic correlation for operator investigation.
   Wizard and later Admin edits additionally prove that authoritative YAML and
   the active PostgreSQL snapshot expose one matching digest, every induced
   installer interruption is recoverable without partial configuration, and
   sealed credential replacement cannot be claimed by the wrong target.
2. Google allow/deny, exact-address override, last-Admin guard, immediate role
   revocation after applied-YAML activation, immediate Administrator grant upon
   activation with durable dashboard event and preexisting-Admin notification/
   retry, assigned-Ministry scoping, immediate runtime suspension after a
   seeded Chairperson relationship disappears, auto-role cleanup, YAML-backed
   manual restoration, and source-return reactivation.
3. Testing email rerouting, mandatory Family-facing test acknowledgments,
   segregated test submission, blocked transition with in-flight test delivery,
   aggregate creation, go-live admission gating, resumable bounded cleanup of
   test submissions/outbox detail, irreversible cancellation semantics, short
   atomic final activation, structural lock, pre-start `draft`-to-`scheduled`,
   in-interval direct `draft`-to-`active` with exactly-once live catch-up, and
   at/after-close rejection on Production transition;
   guarded pre-start withdrawal cancels future live work, returns atomically to
   Testing/draft, and unlocks structural settings, while an active campaign and
   unresolved provider-submitting/delivery-unknown work cannot be withdrawn.
   Production readiness locks the Campaign timezone snapshot; later Parish
   timezone changes do not alter its boundaries, schedules, digests, or
   historical report buckets.
4. Full/delta refresh success, interrupted/invalid load retaining prior truth,
   new/inactive/reactivated Family behavior, and non-overlap/manual coalescing.
5. No-change Family submission, every census field, proposed/terminal Member,
   Ministry request, zero/positive pledge, additional information, repeat
   submission, and stale concurrent submit.
6. Upstream catches up, three-way conflict, Admin edited approval, preflight,
   partial ParishSoft write failure/retry, read-after-write, and unsupported
   manual resolution.
7. Every report's access, counts/percentages, inactive/test exclusion, privacy
   columns, filters, consistent historical/current participation-chart scopes,
   chart parity, and CSV/XLSX/PDF/PNG output.
8. Missed initial/reminder/digest occurrence, per-Family and daily-digest
   recovery coalescing, worker/broker restart, systemic email failure,
   deduplicated CRITICAL notification, active-campaign delivery pause with
   continued live submission and held receipts, pre-provider race checks,
   atomic coalescing/resume, and post-close held-message resolution.
9. Closed campaign explicit reopen directly to `active`, atomic access-token/
   Production activation, no replay of work skipped while closed, archive,
   guarded post-archive return to Testing, denial of successor draft before
   both steps complete, guarded unarchive to `closed`, and denial of unarchive
   after purge preparation begins.
10. Web purge blocked for every non-archived campaign, stale backup, or wrong
    confirmation; atomic gate acquisition; concurrent admission rejection;
    cancellation of queued/retrying work; drain/reconciliation of running and
    externally uncertain work; post-quiescence inventory/backup freshness;
    durable preparation/resumption/cancellation; request/Campaign state
    consistency; gate release only on cancellation/pre-delete failure; atomic
    worker-claim recheck; pre-delete rollback; successful resumable batched
    purge and atomic visible tombstone transition; interrupted-batch recovery;
    retryable database/file cleanup; retained parish-owned audit of read-only
    report access without inventory invalidation; and failure notification.

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
