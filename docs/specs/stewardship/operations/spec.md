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
scheduler, dedicated backup worker, configuration installer, target-specific
credential installers, the explicit token-key-rotation profile, PostgreSQL,
Valkey, and Caddy services from the
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
| `run/` | Locks, bootstrap markers, health/runtime state, isolated persistent service storage |

PostgreSQL data, Valkey state, Caddy ACME state, and uploaded media default to
durable host bind mounts at `<root>/run/persistent/postgresql`,
`<root>/run/persistent/valkey`, `<root>/run/persistent/caddy`, and
`<root>/run/persistent/media`. The root is resolved before Compose renders, so
changing `PARISHKIT_ROOT` relocates every default store. Provision these paths
for their respective service UIDs; generic run/temporary cleanup must never
traverse `run/persistent`. Each store remains independently overridable by
deployment CLI/YAML, including explicit opt-in Docker-managed named volumes.
Container replacement/restart/upgrade must not remove durable storage.

The export root and its subdirectories use owner-only mode `0700`; generated
exports and their temporary files use owner-only mode `0600` from creation,
including before atomic rename. Export generation, authorized download, and
cleanup services use the owning application UID for these mounts. Provisioning
and startup validate ownership/modes on configured export paths. The proxy has
no export-storage mount. These rules also apply to overridden paths and
development storage; no intermediate export file may use permissive defaults.

Credential files/directories use the shared restrictive modes. Compose mounts
each secret read-only only into services that need it; mounting the whole
credentials directory into an application service is prohibited. In
particular, token public keys are available to `web`/general workers, while
token private keys are mounted only into `mail-dispatch` or the explicit
`token-key-rotation` profile, except for initial provisioning by the
[offline bootstrap profile](#offline-bootstrap-profile), and Google mail-provider credentials only into
`mail-dispatch`. The scheduler, web, general
worker, report/export jobs, and ordinary maintenance commands cannot read the
private token-key path. Images, Compose files, logs, exceptions, backup
metadata, and support bundles never contain credential values.

Among online services, only `config-installer` mounts
`<root>/config/stewardship` read-write; the sole initial-provisioning exception
is the [offline bootstrap profile](#offline-bootstrap-profile). Other
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
and collision-only MAC keys required by RehearsalCodeReservation records,
are protected by the separately authorized operator secret-escrow process below;
copying them into an application database, report, or ordinary backup task is
prohibited.

Scheduled, operator-requested, and purge-triggered application backups are
claimed only by `backup-worker` through its dedicated queue. The web and general
worker may create an authorized durable backup TaskRun but never receive target
credentials or the data-backup key. The backup worker cannot claim source,
mail, publication, export, purge, or configuration-installer tasks.

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

### Secret escrow recovery verification

Before approving an exceptional purge, an authorized operator uses an isolated
off-host recovery environment to retrieve and verify the selected data backup
and matching escrow bundle. Using independently held recovery private material,
the operator verifies that the bundle decrypts, contains every credential/key
version required by the backup manifest, matches their fingerprints, and
provides the key that successfully decrypts and verifies the selected data
backup. Merely possessing a recovery public key or uploading an encrypted
bundle is insufficient. Verification must not connect to production providers
or write to ParishSoft; this check is not a production restore or release.

The operator-only verification tooling produces a strict, non-secret summary
of backup/bundle references and manifest digests, required credential/key and
recovery-key fingerprints, completion time, and pass/fail. It never prints key
values, decrypted records, or credentials; temporary plaintext follows the
isolated recovery environment's restrictive storage and cleanup procedure.
The Admin records successful evidence through the
[purge workflow](../admin-portal/spec.md#campaign-purge).
Normal web/worker/backup services receive neither recovery private material nor
permission to invoke the escrow profile. The application checks manifest
binding and freshness, while trusting the named operator's attestation of the
off-host check. The runbook requires reporting lost escrow/recovery access so
pending evidence can be invalidated before deletion.

## Production ingress and TLS

Caddy terminates TLS, redirects HTTP to HTTPS, obtains/renews public
certificates automatically through ACME/Let's Encrypt, serves static assets,
and proxies dynamic traffic. Production requires a DNS hostname pointing to the
VM and inbound ports 80/443. Caddy's data/config volumes persist account and
certificate state across upgrades.

Caddy has explicit highest-priority matchers that return the ordinary public
not-found response for `/health/live`, `/health/ready`, and `/metrics` before
the catch-all application reverse proxy. Container health checks and authorized
metrics clients call the application service directly over the internal Compose
network.

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
3. Run the pre-migration phase of `pk-stewardship bootstrap` through the
   [offline bootstrap profile](#offline-bootstrap-profile) if not restoring;
   it creates/validates deployment configuration, the minimal initial-Admin
   Stewardship YAML authority, installer handoff keys, Django signing/general-
   encryption keyring, Family-code MAC keyring, and email-link sealed-box
   public/private keyring, and the `/metrics` bearer credential without
   requiring application tables. Provisioning is
   idempotent, owner-only, and never overwrites a nonmatching existing keyring.
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

### Offline bootstrap profile

The `bootstrap` Compose profile is an explicitly operator-invoked, one-shot
provisioning command, excluded from ordinary service startup and never callable
from the web UI, scheduler, or task queue. It is the initial-provisioning
exception to online installer-only write authority, not a general configuration
repair or credential-rotation interface. Both bootstrap phases run before
web, workers, scheduler, installers, and proxy start; PostgreSQL and Valkey may
run for readiness checks, migration, and initial database materialization.
The deployment startup workflow must enforce mutual exclusion between
bootstrap and online application services, including concurrent start attempts,
without granting bootstrap Docker-socket access.

Use a dedicated one-shot service identity with operator-provisioned ownership
and restrictive file/directory modes. Its write mounts are limited to the
deployment-config target, the Stewardship-authority directory, and separately
enumerated credential target subdirectories for initial installer handoff keys,
Django signing/general-encryption keys, Family-code MAC keys, email-link
public/private keys, and the metrics bearer credential. Mount existing Google
OAuth and database credentials individually read-only as needed; these are
operator-provided inputs, not permission to rewrite unrelated credentials.
Any startup-interlock storage must likewise be narrowly scoped. Do not mount
the whole config or credentials tree, broad host paths, campaign files, or a
Docker socket. Honor configured path overrides and final consumer ownership;
never relax permissions to accommodate provisioning.

Pre-migration provisioning requires no application tables. After migration,
bootstrap may import only the initial configuration/Admin marker into the
empty application database. Matching partial provisioning can be resumed
idempotently; nonmatching existing configuration/key material or an already
configured/restored deployment is refused without overwriting it. Remove the
one-shot container after completion; its write mounts are never inherited by
online services. Test the rendered mount/identity boundaries, offline startup
interlock, partial-run recovery, nonmatching-input refusal, and both bootstrap
phases in local and production Compose configurations.

### Offline Admin-access recovery

`pk-stewardship recover-admin` is a separate operator-only command for loss of
usable Google Admin access, including account rename or deactivation. Invoke
it explicitly through an `admin-recovery` one-shot Compose profile with host
operator authority; it is absent from ordinary startup and cannot be invoked
by a web route, task queue, or scheduler. It creates no application session,
password, recovery token, or alternate authentication provider. An operator must
independently establish the parish's authorization to grant the replacement
Google email; the command does not claim to verify account ownership offline.

Stop web, all workers, scheduler, online installers, and proxy before recovery.
Use the same deployment-wide offline/startup interlock as bootstrap, including
concurrent start exclusion; PostgreSQL/Valkey may remain running. This profile
runs the existing config-installer implementation with its service identity
and narrow authority-directory write mount, not a second general-purpose YAML
writer. Mount only deployment configuration and required database credentials
read-only plus narrowly scoped interlock storage. Grant no credential-target
write mounts, whole-directory secrets, provider/token private keys, campaign
files, or Docker socket. No online service gains this command's operator
authorization; bootstrap retains its empty-deployment-only behavior.

Require an initialized, supported database and matching active YAML/database
configuration. An unresolved configuration installation or mismatch must be
recovered through the existing installer protocol first, never overwritten.
The command displays deployment/parish identity, current Admin rules, the exact
normalized target email, and a minimal diff. Require a named operator, reason,
and explicit confirmation of deployment identity and target email; unattended
invocation must supply equivalent explicit confirmations, never default to yes.
The permitted patch only adds Administrator with manual provenance to that
exact-address rule, creating it if absent. Preserve other roles, rule origins,
assignments, all existing Admin grants, and all unrelated configuration. Adding
Admin to an explicit-deny rule is shown as an intentional access grant. No
domain grants, account rebinding, removal, or general configuration edits are
accepted; removal of an obsolete Admin is a later normal authenticated action.

Create a tagged operator-recovery ConfigurationChangeRequest and apply a new
immutable YAML version through the same base-digest, schema-validation,
prepare/manifest/activation, and crash-recovery protocol as the installer.
Its authority is the explicit offline operator workflow, not a fabricated
PortalUser or bypass flag accepted by ordinary configuration APIs. Repeated
execution/resume of one recovery operation is idempotent and cannot emit
duplicate grants/events. Preserve prior configuration and audit history.

Matching database activation also revokes all administration-portal sessions
and pending administrative OAuth/reauthentication state, records a parish-owned
audit with operator identity/reason, target, operation ID, and before/after
digests/roles, and creates a persistent unacknowledged security event. Queue
operational notifications to preexisting Admins and the replacement address
under normal notification/routing policy; the offline command sends no email
itself and delivery failure cannot erase the audit or roll back recovery.
Family sessions, campaign state, submissions, and purge/restore/other admission
gates remain unchanged. A failed/interrupted activation stays fail-closed and
resumes by operation ID; never report success until YAML/database digests and
the session revocation/security event agree.

After removing the one-shot container and restarting services, the replacement
Admin must complete ordinary Google authentication. The runbook verifies that
login and reviews the security event before any obsolete access is removed.
This workflow cannot repair a Google outage, inaccessible database, or missing
required configuration/credentials; report those blockers without weakening
authentication or clearing maintenance gates.

### Download capacity and timeouts

Deployment YAML defines a finite guarded-download cap, bounded download pool,
and separate finite connection budgets for interactive traffic, background
services, and operator/recovery work. Follow the default cap and admission
behavior in [campaign read guards](../data/spec.md#campaign-read-guards).
Download pool capacity may not exceed its admission cap or spill into other
pools. Budget all processes, replicas, rollout overlap, and auxiliary database
connections; their combined configured maxima plus PostgreSQL-reserved slots
must fit the database connection limit. Each non-download class retains
positive dedicated headroom sized and load-tested for its configured worker
concurrency and the architecture's reference load. Downloads must also leave
web execution capacity for interactive requests; limiting database connections
alone is insufficient if streaming occupies every web worker/thread.

Startup/deployment validation rejects an inconsistent pool, process, database,
or timeout budget. The admission cap remains deployment-wide during scaling,
restarts, and rolling upgrades; a per-process semaphore alone is insufficient.
Expose active downloads, busy rejections, pool utilization, and guard timeouts
without recording downloaded data.

For the dedicated download connections, set a finite
`idle_in_transaction_session_timeout` longer than the total download deadline
but shorter than the purge reader-drain timeout. With the five-minute download
and six-minute drain defaults, use five minutes 30 seconds. Any applicable
database transaction/session, application, or proxy timeout must likewise allow
the declared download lifetime; do not globally disable database hardening for
other traffic. The application still enforces the hard total response deadline
and closes the stream/transaction; idle timeouts are only a fallback, not the
continuous-transfer time limit. Guard-connection loss aborts the response.
Validate these relationships when overriding any deadline or scaling services.

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
credentials or performs the backup inline; `backup-worker` reads the existing
credential reference. Purge-triggered backups follow ordinary
retention and are additionally referenced immutably by the PurgeRequest.
The same isolated service supports guarded revalidation of the selected backup
without creating a new backup or resetting retention, as defined by the
[purge workflow](../admin-portal/spec.md#campaign-purge). Verification failures
are recorded durably with sanitized diagnostics; no backup credentials or
decrypted contents are returned to the web process.

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
and Family mail disabled. Before exposing any web route, restore invalidates
all restored administration and Family sessions, pending OAuth state, and
cached reauthentication evidence. Fresh Google login is required for Admin,
Staff, and Ministry leaders; neither a saved cookie nor a previously fresh
reauthentication timestamp survives restore. In the same fenced initialization
step, restore sets the durable `restore_review_required` gate, assigns a fresh
Family-link credential epoch generated after loading the backup, clears all
restored active-token-generation and rehearsal-epoch pointers, and invalidates
restored prepared generations. This applies to every Family, including those
currently ineligible, so later reactivation cannot revive a restored link.
Restarting recovery resumes that restore instance; performing another restore
creates a new epoch. The credential epoch is never taken from backup state.
That gate
blocks every Family authentication, access-token exchange, form, and submit
route regardless of campaign dates or Testing behavior; public requests receive
a neutral parish-branded maintenance page without Family-specific information.

Administration login and the restore-readiness workflow remain available. The
restricted maintenance pool uses existing services and their unchanged secret
mounts, not an additional privileged worker. During the gate, the owning
services claim the following dedicated queues:

| Service | Restore queue | Allowed maintenance work |
| --- | --- | --- |
| `worker` | `restore-general` | Tenant validation, read-only full refresh, public-key-only restore token preparation and stale-credential scrubbing, provider-independent integration checks and mail rendering, hold inventory, integrity diagnostics, non-mail operational notifications, and explicitly authorized interrupted-purge recovery |
| `mail-dispatch` | `restore-mail` | Provider checks, Testing-recipient mail, and operational email using the normal private-key/provider boundary |
| `backup-worker` | `restore-backup` | Backup verification using only its existing backup credentials/keys |

Queues hold references to the same authoritative TaskRun/outbox records; routing
does not copy message state or grant access to another service's secrets.
Provider-dependent integration checks follow their owning service above.
Configuration/credential installers retain only their existing authorized
target-specific workflow queues. The scheduler may recover admissible
maintenance hints but may not materialize live campaign schedules. Services
must not claim ordinary restored work, dispatch `production` outbox rows,
write to ParishSoft, or generate ordinary campaign exports while gated. Every
maintenance task carries a type checked at creation, claim, and external-effect
boundaries; a queue name alone never authorizes work. On release the existing
services resume their ordinary queue admission under the same credential mounts.

The gate can be cleared only after applicable readiness passes and a freshly
authenticated Admin reviews and confirms the proposed state-aware release
defined by the
[Admin workflow](../admin-portal/spec.md#restore-release), which is the sole
authority for every current-pointer/lifecycle-to-mode mapping, including the
archived-current-pointer case and all blocked states. Clearing the gate,
reconciling lifecycle state, selecting mode, materializing holds, and enabling
the corresponding work admission are one audited transaction. Failed or
abandoned review leaves the restore gate, Family access, and live delivery
disabled while restricted maintenance work remains available.

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
Exports, including Family-code and mail-merge exports, are intentionally stored
without application-layer encryption during this interval. Plaintext exposure
to the owning application account and privileged host operators is an accepted
product risk; the low-sensitivity classification of campaign codes does not
make the accompanying parishioner information public. Owner-only storage under
[runtime storage](#runtime-storage), authenticated downloads, and expiration
are the selected controls. Export encryption or a shorter code-specific
retention period is not required. This exception does not change encrypted
backup or database-field encryption requirements.

ParishSoft HTTP cache follows configured freshness and bounded size. Upload
staging, failed wizard staging, old static bundles, expired sessions, worker
results, and rotated operational logs have documented cleanup jobs.

Cleanup is idempotent, scoped to explicit subdirectories/records, and cannot
follow unsafe symlinks or broad/unresolved paths. Generic operational-cache
cleanup never deletes source snapshots, submissions, audit history, or backups.
Only the dedicated source-compaction service may thin unprotected snapshot
corpora, under the normative retention and reference guards in the
[data specification](../data/spec.md#source-snapshot).

Run dedicated derived-fact compaction hourly by default, with a configurable
positive interval and bounded batches, under the
[derived fact retention policy](../data/spec.md#derived-fact-retention).
It removes only eligible superseded generations, respects campaign purge/
restore gates, and resumes safely after interruption. Record counts, duration,
last successful completion, and eligible backlog without copying report values;
surface failures through normal task monitoring. Do not put fact deletion in
unrestricted generic cache cleanup. This is an explicit exception for disposable
derived calculations, not a change to source/submission or pinned-report
retention.

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

The application exposes Prometheus-compatible metrics only at `/metrics` on its
internal Compose interface. The route requires an `Authorization: Bearer`
credential, compares it in constant time, and returns the ordinary not-found
response when authentication fails. Bootstrap generates the random credential
as an owner-only file; Compose mounts that individual file read-only only into
`web` and an explicitly authorized metrics client. Rotation atomically replaces
the credential through its target-specific credential installer, and clients
must acknowledge the new safe fingerprint before the old value is retired.
Neither the credential nor its hash appears in URLs, logs, metrics, support
bundles, or the database.

No health or metrics endpoint exposes parish names, Family/Member data, emails,
tokens, campaign content, or credentials. Caddy never proxies any of these three
internal paths, even when a caller supplies a valid metrics credential.

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

- direct-activation catch-up tests at 5,000 Families with many overdue schedules,
  measuring confirmation/global-lock duration and concurrent Family response
  latency against architecture targets. Assert a constant-size demand/task
  insertion rather than per-Family materialization during confirmation. Test
  bounded batches even for one large coalescing group, atomic checkpoints,
  repeated activation/lost hints, worker restart, changed schedules/eligibility,
  competing scheduler/source producers, close/restore/pause races, hold-aware
  dispatch, and unfinished-demand archive/purge exclusion. Final completion
  must release only its own hold with no duplicate semantic delivery;
- Family-form concurrency tests for unrelated versus relevant source promotion,
  canonical equivalence, household/Member and Ministry-option additions/removals,
  financial changes, disabled sections, relevant form-definition changes, and
  another effective Family response. Verify trusted session-bound baseline
  reconstruction, forged/expired reference denial, source-pin/compaction races,
  pin transfer on commit, promotion/Submit serialization, current eligibility/
  campaign checks, and in-memory conflict review preserving actual edits without
  storing drafts or exposing inaccessible data;
- offline Admin-recovery tests for sole-account rename/deactivation, an
  existing explicit-deny target, manual role provenance, and preservation of
  old grants/unrelated configuration. Verify host-profile-only invocation,
  narrow installer mounts, startup mutual exclusion, confirmation, stale
  digests, uninitialized/mismatched configuration refusal, crash/resume
  idempotency, session/OAuth revocation, durable audit/security notifications,
  unchanged campaign/maintenance gates, and mandatory normal Google login.
  No test or command may mint a session or rebind an existing Google account;
- exhaustive TaskRun/ScheduleOccurrence transition tests using the canonical
  background tables, including rejection of every unlisted edge and idempotent
  repeated commands. Check each state against archive, purge quiescence,
  schedule replacement, and recovery; cover abandoned-owner fencing, unknown
  external effects, terminal failure versus semantic coverage, concurrent
  explicit retry deduplication, linked attempts with unchanged occurrence/
  delivery identity, and immutable prior failure/uncertainty history;
- rehearsal-reservation tests for atomic issue/reserve rollback, concurrent
  epoch issuance, duplicate retry, and collision with an earlier epoch's code
  after credential cleanup and MAC rotation. Verify reservation records contain
  no Family/epoch links or recoverable codes, collision-only keys never enter
  authentication lookup, missing keys fail issuance closed, and retirement is
  blocked until all reservation/backup dependencies are satisfied. Test purge
  of one versus the last referencing campaign and restore with the required
  historical collision keys; no test may rekey an HMAC-only reservation by
  assuming deleted plaintext is still available;
- role-provenance tests for manual versus seed-created rules, manual/seed/mixed
  Ministry-leader grants, inherited-domain roles copied into overrides, and
  source loss/return with zero or remaining active assignments. Verify explicit
  independent retention, unrelated checkbox/assignment edits, role removal,
  repeated suggestion refresh, YAML round-trip/activation, and audit history;
  reject missing/inconsistent origins and prove provenance never replaces
  Ministry row-scope authorization;
- purge-reader tests that pause a report between queries and a download between
  chunks, claim purge concurrently, and prove no deletion starts until their
  shared guards release. Verify new readers are denied after claim, first-batch
  prerequisites are rechecked after drainage, stale deadlines never substitute
  for the exclusive guard, timeouts leave all rows/files intact, and crashes
  before the first checkpoint repeat drainage. Cover lazy queries, streaming
  transaction scope, guard-connection loss, multi-campaign lock order, slow
  transfers, cancellation, and post-deletion retry with admission still closed;
- download-capacity tests across multiple web processes, restart/rollout
  overlap, and slow clients. Saturate the download limit while running the
  reference Family workload and background jobs; verify interactive response
  targets, reserved connection/web execution capacity, prompt retryable busy
  responses without file bytes, and no pool spillover. Exercise disconnects,
  timeout/connection loss, stale admission leases, and purge drainage to prove
  slot recovery never precedes guard/stream closure. Reject conflicting pool
  and timeout configurations, including overridden deadlines;
- purge-recovery evidence tests for missing/wrong escrow, unavailable recovery
  private material, missing historical keys, failed backup decryption, manifest
  mismatch, future timestamps, and exact 60-minute expiry. Verify new evidence
  does not overwrite history, replacing a backup or relevant key reference
  invalidates dependent evidence, expiry at queued worker claim prevents all
  deletion, and expiry after committed deletion does not prevent safe resume.
  Exercise manifest-change/claim races, operator attestation authorization,
  strict non-secret fields, redacted verification output, and unchanged service
  secret mounts using synthetic keys/backups only;
- pure unit tests for validation, normalization, dates/DST including
  midnight-gap/fold campaign boundaries, money, percentages, role precedence,
  state machines, merge/diff, report calculations, and export escaping;
- scheduler/transaction tests for idempotent campaign-boundary occurrence
  creation, exact interval gating despite state lag, start/close lock races,
  end-date replacement, retry, and overdue recovery after scheduler outage;
- recovery tests for close-before-start delivery when both boundaries are
  overdue, atomic ordered restore catch-up, and re-emission of lost broker
  hints for already-existing due records without redispatching unknown mail;
- purge/successor exclusion tests covering every nonterminal request state,
  creation/confirmation/claim races, and permitted successor creation after
  successful purge despite its retained tombstone gate;
- report security tests for non-identifying cursors, requester/scope reuse,
  application-only file serving, and owner-only storage without a proxy mount;
- outbox tests for paused direct receipts without schedule membership, durable
  hold/version restoration, resume cancellation, and stale dispatch hints;
- source-compaction integration tests for every age/anchor tier, each protected-
  reference class, promotion-lease exclusion, a late reference winning under
  row locks, idempotent restart, payload garbage collection only after the last
  reference, and coherent reconstruction of every retained snapshot;
- participation-fact integration tests for idempotent rebuild hints, interrupted
  generation invisibility, exact-input atomic publication, pinned generation/
  source protection, deterministic recalculation, and drift detection;
  fake-clock tests cover burst/sustained debounce, claim/completion races,
  duplicate hints, recovery preserving newer demand, fixed pinned cutoffs and
  priority, and prevention of interactive-pointer regression;
- fact-compaction tests for many successive generations and both population
  scopes, hourly scheduling, bounded batches, and crash/retry idempotency.
  Preserve current/stale-display, pinned, building/recoverable, and actively
  consumed generations; race cleanup against pointer publication, new pins,
  reader selection, drift verification, and task claims. Verify export-file
  expiry does not release retained-parent pins, purge/restore gates block
  ordinary compaction, removed generations cannot be partially served, and
  task/audit history plus underlying submissions/protected source inputs are
  unchanged. After protection ends, eligible daily rows are reclaimed;
- archive/Return tests for required daily and weekly digests not yet due or
  materialized, explicit audited skips, empty/no-recipient coverage, coalesced
  replacements, failed and unknown deliveries, and new input racing final
  inventory verification; verify scheduler retries, schedule revisions, and
  unarchive/reopen do not resurrect resolved coverage or suppress newer inputs;
- reopen preparation tests for interrupted batches, idempotent retry,
  cancellation, changed source/eligibility/configuration/key inputs, stale
  completion, exact Family coverage, and rejection of inactive generation
  tokens by lookup/dispatch; measure final confirmation at reference scale and
  prove it performs no token-generation/encryption/per-Family insertion work;
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
- distributed Family-code detector tests proving that 100 invalid attempts
  from 20 IPs within five minutes trigger even when candidates repeat, while
  either unmet threshold does not trigger; include limiter-rejected attempts
  exactly once, boundary expiry, diagnostic-only candidate diversity, and
  deduplicated WARNING/CRITICAL escalation and recovery;
- shared-network Family-code tests covering distinct Families behind one IP,
  successful requests below its failure limit, enforcement at 100 failures per
  ten minutes, the independent five-failure IP/code-pair limit per 15 minutes,
  configured overrides, and recovery as each sliding window expires;
- restore-token tests that take a backup, rotate/revoke a link, restore, and
  prove every pre-restore link is rejected while fresh links and unchanged
  manual codes work after active release. Cover scheduled release, crossing
  close during preparation, ineligible-Family reactivation, draft go-live,
  repeated restore versus process restart, stale prepared generations, changed
  manifests, failed/retried preparation, and no partial activation. Verify
  restored sealed outbox credentials are never sent, authorized resealing
  preserves delivery identity/history/holds, ambiguous attempts are not resent
  automatically, and token replacement alone creates no email;
- rehearsal-credential tests proving Testing email never contains Production
  codes/tokens; rehearsal code/link login succeeds only in the current Testing
  epoch with ordinary acknowledgments and date/eligibility gates. Verify
  mode-disjoint formats, same-code collision prevention across rehearsal epochs,
  namespace/epoch dispatch checks, no missing-credential fallback, and that
  credentials captured from delivered Testing messages plus their sessions fail
  after gate acquisition and Production activation. Cover cancelled cleanup,
  withdrawal/new rehearsal, future campaigns, stale mail workers, and unchanged
  Production codes; verify sensitive cleanup, retained unlinked reservations,
  and HMAC-key migration without old credential revival;
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
- autosave browser/installer tests for rapid multi-row/table edits, repeated
  checkbox changes while applying, and dispatch only after `applied` with its
  returned digest. Verify queued versus saved indicators, uncertain-outcome
  reconciliation, same-key retry deduplication, failure/cancellation pauses,
  genuine multi-tab/Admin conflicts with preserved unsaved intent, removed
  targets, current-Admin revocation, and page-exit/revisit behavior. Conflicts
  never silently rebase or bypass provenance, last-Admin, or CSRF guards;
- limiter tests proving that pre-verification IP rejections contribute once
  without OAuth state allocation, provider calls, raw token retention, or
  duplicate counting, and that post-verification identity-limit rejections
  contribute once after signed identity validation without retaining raw
  callback/provider tokens;
- accessibility automation plus keyboard/screen-reader-oriented manual checks;
- CSV/XLSX/PDF/PNG structure/content tests without committing generated reports;
- backup/restore manifest and isolated restore smoke tests, including
  uncertainty-window inventory, newly discovered Families, atomic release-time
  hold creation, catch-up suppression, assumed-delivered accounting, and
  duplicate-aware authorized resend; and
- Compose startup, health, migration, bind-mount reload, and production image
  smoke tests, including proof that both internal health routes succeed, an
  authenticated internal `/metrics` request succeeds, missing/invalid metrics
  credentials fail without disclosure, and Caddy does not proxy any of the
  three internal paths even with a valid metrics credential.

## Acceptance scenarios

At minimum, end-to-end tests demonstrate:

1. Empty deployment through bootstrap/wizard, aborted wizard rollback, restored
   deployment startup with Family access gated, restricted maintenance refresh/
   test-send/hold work while ordinary work remains blocked, and atomic Admin-
   approved release of restored `draft`, future `scheduled`, current `active`,
   expired-to-`closed`, already `closed`, archived-current-pointer, historical
   archived, purged, and no-current cases. The results exactly match the Admin
   restore mapping: in particular, archived-current preserves its pointer and
   Production until separate Return to Testing, while interrupted purge state
   blocks release pending explicit recovery.
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
   revocation after applied-YAML activation, and immediate high-impact expansion
   upon activation with durable dashboard event and preexisting-Admin
   notification/retry for exact-address Administrator grants, all new domain
   rules, and Staff additions to existing domain rules; assigned-Ministry
   scoping, immediate runtime suspension after a
   seeded Chairperson relationship disappears, runtime suppression without YAML
   mutation, configuration-request role removal, YAML-backed manual restoration,
   and source-return reactivation.
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
   new/inactive/reactivated Family behavior, and non-overlap/manual coalescing;
   compaction at every retention boundary preserves every protected reference,
   retains deterministic anchors, remains idempotent after interruption, and
   reconstructs each uncompacted snapshot exactly.
5. No-change Family submission, every census field, proposed/terminal Member,
   Ministry request, zero/positive pledge, additional information, repeat
   submission, and stale concurrent submit.
6. Upstream catches up, three-way conflict, Admin edited approval, preflight,
   partial ParishSoft write failure/retry, read-after-write, and unsupported
   manual resolution.
7. Every report's access, counts/percentages, inactive/test exclusion, privacy
   columns, filters, consistent historical/current participation-chart scopes,
   atomic/pinned CampaignDailyFactSet generation and drift verification, chart
   parity, and CSV/XLSX/PDF/PNG output.
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
