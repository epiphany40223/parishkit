# Stewardship application architecture

This specification defines the technical architecture and cross-cutting
security/nonfunctional behavior. Domain records and workflows are defined by
the [data specification](../data/spec.md); deployment details are extended by
the [operations specification](../operations/spec.md).

## Technology and component model

The application targets Python 3.12 or newer and uses the maintained patch
release of Django 5.2 LTS. PostgreSQL 18 is the only supported production
database. Celery 5.6 executes asynchronous work with Valkey as broker/cache;
the initial implementation baseline is the official Valkey 9.1 image at its
latest supported security patch release. Celery and its Python client retain
their Redis-named transport/URL scheme because that is the protocol adapter's
name. PostgreSQL, not Valkey, remains authoritative for schedules, outbox
messages, job state, every Admin and Family session, and application data.
Valkey holds only broker, cache, and rate-limiter state; its restart or eviction
never invalidates an authenticated session.
Valkey is selected for its BSD-3-Clause licensing and Redis-protocol
compatibility. Upgrades may advance only after the broker, result-independent
task dispatch, cache, atomic limiter scripts, expiry, restart, and outage
behaviors pass the integration suite against the candidate image.

The web UI uses Django templates and progressive enhancement. Small,
self-hosted JavaScript modules manage the Family wizard, inline validation,
browser-timezone rendering, and interactive charts. It is not a separately
deployed single-page application. Static assets are versioned and served by the
reverse proxy in production. Rich text uses a self-hosted WYSIWYG editor and is
sanitized on input and output.

Production Compose contains:

- `web`: Gunicorn-hosted Django application;
- `config-installer`: the only service with write access to the Stewardship
  configuration-authority directory;
- target-specific `credential-installer-*` workers, each able to decrypt only
  its own staged replacement and write only its own credential subdirectory;
- `worker`: general Celery workers for polls, rendering, exports, publication,
  backup, purge, and cleanup;
- `mail-dispatch`: a dedicated Celery worker for provider submission and link-
  token encryption-key rotation, with the private token-key mount unavailable
  to every other online service;
- `scheduler`: exactly one scheduler process that materializes due work;
- `postgres`: PostgreSQL with a durable volume;
- `valkey`: broker/cache with a durable local volume, though task correctness
  cannot depend on broker persistence; and
- `proxy`: Caddy on ports 80/443 with durable ACME state.

Local Compose uses the same service topology without Caddy/TLS by default,
bind-mounts the checkout into the Python containers, and enables development
reload. It supports Linux, macOS, and Windows Docker hosts. Optional development
profiles may add a local mail catcher, but must not create an authentication
bypass that can be enabled in production.

## Package boundary

Campaign code lives under `src/parishkit/stewardship/` as one Django project
with cohesive apps for accounts/configuration, source data, campaigns,
responses, workflows, reports, jobs, and audit.

Only service-level capabilities become general ParishKit code:

- ParishSoft v2 family-change feed and idempotent `PUT` contact operations;
- reusable validation/normalization needed by more than one tool;
- provider-neutral email improvements; and
- generic safe export helpers when they have no campaign semantics.

The application reuses `parishkit.cli`, `parishkit.config`,
`parishkit.parishsoft`, `parishkit.retry`, `parishkit.logging`,
`parishkit.email`, Google credential helpers, and runtime path helpers. The
console entry point and deployment-YAML layer use the shared CLI/config option,
loading, validation, and path behavior rather than reimplementing it. Campaign
models, authorization, content slots, reports, and workflows must not leak into
general modules.

## Public interfaces

The human-facing interface consists of:

- `/`: Family code entry or campaign-status page;
- `/access/<token>`: opaque email-link exchange, immediately redirected to a
  token-free Family URL after a session is established;
- `/family/...`: authenticated wizard steps and final submission endpoint;
- `/admin/login`: Google-only login;
- `/admin/...`: every administration page, JSON/HTML partial endpoint, export,
  job detail, and purge workflow.

`/health/live` and `/health/ready` are internal operational interfaces, not
public interfaces. They listen on the application service network and are not
routed by Caddy. Compose restart checks use only `/health/live`;
`/health/ready` is queried by operator diagnostics and alerting, not by an
ingress controller. Both return only an HTTP status plus the generic body `ok`
or `unavailable`. Detailed health phase/reason information is available only
through the operator CLI and protected logs.

There is no public REST/GraphQL API. Internal browser endpoints use the same
cookie session, authorization, CSRF, rate limits, and audit policy as their HTML
pages. They return stable machine-readable validation errors but are not a
supported third-party contract.

The package exposes a `pk-stewardship` console entry point and thin executable
wrapper. Subcommands cover bootstrap, configuration validation, migration,
health diagnostics, backup, operator-only secret escrow, and restore. Web-
serving and worker commands remain container entry points that import package
code. There is no console campaign purge; purge is intentionally a guarded
Admin web workflow.

## Configuration and secrets

Configuration and runtime state are intentionally distinct:

1. **Deployment configuration**: YAML/environment values required before the
   database is reachable, such as database/broker hosts, public origin, trusted
   proxy count, and credential-file paths.
2. **Parish configuration authority**: schema-versioned YAML below
   `<root>/config/stewardship/`. It contains parish profile, non-secret
   integration settings, login rules/manual role mappings, campaign definitions,
   content, schedules, share options, Ministry/fund mappings, and other parish-
   specific operational settings. Immutable version documents are selected by
   an atomically replaced active manifest; stable IDs make list entries and
   cross-references mergeable and auditable.
3. **Secret material**: files below `<root>/credentials`, written atomically as
   owner-only files. This includes Django signing/general-encryption keys,
   private email-link token keys, Google OAuth client secret, Workspace service-
   account JSON, ParishSoft API key, Slack bot token, backup-target credentials,
   and versioned data-backup encryption keys. Operator recovery private material
   for secret escrow is held off-host and never belongs to this directory.
4. **PostgreSQL runtime state and applied snapshots**: runtime mode/lifecycle,
   current-campaign pointer, source data, submissions, jobs, workflow decisions,
   sessions, and audit remain database-authoritative. PostgreSQL also holds an
   immutable canonical copy and normalized materialization of each applied YAML
   version so transactions and historical campaigns can refer to an exact
   configuration. Those rows cannot be edited independently and are not a
   second configuration authority.

The Admin UI remains the normal configuration editor. A save creates an
optimistically versioned `ConfigurationChangeRequest` against the active YAML
digest; web and ordinary workers have no writable configuration mount. The
dedicated installer renders and schema/cross-reference-validates a candidate,
writes/fsyncs an immutable version document, prepares its database
materialization, atomically switches the active YAML manifest, and then
activates the matching database snapshot. Only after both sides report the same
digest does the UI call the request applied. A crash is recovered idempotently
from the request, prepared snapshot, and active-manifest digest. While they
differ, readiness and configuration-dependent mutations/background work fail
closed; no process silently chooses one copy. Rollback creates and applies a
new YAML version derived from a prior version rather than mutating history.

Deployment configuration required to reach PostgreSQL is changed by the
documented operator workflow, not the web installer. Dynamic runtime state is
never exported to YAML merely because it names a parish or campaign.

Secret UI controls show only presence, last replacement time, and a fingerprint
safe for identification. They never return an existing secret. Replacement is
submitted over the authenticated TLS page, immediately sealed to the public
handoff key for that secret type, and retained only as an expiring ciphertext
linked to a `SecretReplacementRequest`. The web process does not retain
plaintext, possess a handoff private key, or have a writable credential mount.
A target-specific installer sees only its queue, handoff private key, and one
writable credential subdirectory; it decrypts in memory, validates/tests the
candidate, atomically replaces the owner-only file, records the safe
fingerprint, and destroys staged ciphertext. Consumers mount only the resulting
individual file read-only and acknowledge the new fingerprint before the UI
reports success. Failure or expiry destroys staging and leaves the old working
credential installed. No installer mounts the whole credential directory or
can claim another target's request. Every path defaults below `PARISHKIT_ROOT`
or `/opt/parishkit` and remains overridable through deployment configuration.

General application encryption uses a versioned symmetric keyring for manual
Family codes and other reversible values intentionally stored in PostgreSQL;
integration secret values remain in credential files. Every ciphertext envelope
records its algorithm/version and key ID; one
key is active for writes and older keys are decrypt-only during rotation.
Rotation installs and validates the new key, makes it active, re-encrypts
retained values in idempotent transactional batches, verifies that no online
ciphertext references the old key, and only then permits retirement. Failure
leaves both keys usable and the migration retryable. A key remains recoverable
for any retained backup that needs it, or that backup must be re-encrypted
before retirement. Signing-key rotation keeps the prior verification key only
for the maximum lifetime of credentials issued under it, then removes it after
audit confirms the transition window ended.

Reusable email-link tokens and credential-bearing mail substitutions use a
separate versioned public/private sealed-box keyring from a maintained
cryptographic library. Ciphertexts carry format version and recipient key ID.
`web` and general workers receive only active public encryption keys; they may
generate and seal a new random token but cannot recover any retained plaintext.
Only `mail-dispatch` and an explicitly invoked rotation-service profile receive
the private decryption-key ring. Rotation first distributes a new public key,
makes it active for encryption, re-encrypts retained ciphertext in idempotent
batches inside the private-key service, verifies migration, and retires an old
private key only after retained backups no longer require it. This keyring is
independent of the general application and Family-code MAC keyrings.

Family-code lookup uses a distinct versioned MAC keyring. Every fingerprint row
records its MAC algorithm/version and key ID; one key is active for new rows and
older keys may be lookup-only during migration. A lookup computes the
domain-separated HMAC of the canonical candidate under every accepted key and
matches any corresponding row.

MAC-key rotation installs the new key, makes it active, and idempotently
backfills new-version fingerprint rows by decrypting each retained display code.
Bulk code generation runs inside its surrounding atomic `READ COMMITTED`
population/promotion transaction. That transaction holds the accepted MAC-key
set stable against rotation, computes and inserts a fingerprint row under every
accepted key, and relies on the unique `(campaign, key ID, digest)` indexes to
arbitrate concurrent candidates. Each candidate attempt uses a database
savepoint; a uniqueness conflict rolls back only that candidate's rows and
retries with fresh randomness, never the complete source promotion. This also
prevents duplication of a code indexed only under an older accepted key. The
prior key and rows may be retired only after all online Families have a new-
version row and every retained backup containing old-only rows either remains
paired with the prior key or has been re-encrypted/migrated. Failure leaves both
versions accepted and the migration retryable.

## Identity and session security

Administration authentication uses Google through django-allauth with OAuth
authorization code flow, state, nonce, and PKCE. Only a Google-verified email is
accepted. The stable Google `sub` identifies the external account; normalized
email is re-evaluated against current login rules on every login and privileged
request. Password, recovery, signup, and non-Google authentication endpoints
are disabled.

An exact-address rule matches the verified normalized email without requiring a
hosted domain. A domain rule matches only when both the email suffix and the
signed Google ID-token `hd` claim equal the normalized configured domain. The
application never trusts an OAuth request hint, email suffix alone, DNS/MX
records, or a client-supplied value as proof of hosted-domain membership.
Personal Google accounts using addresses at consumer or externally hosted
domains therefore cannot inherit roles from a domain rule.

Administration OAuth endpoints use shared Valkey sliding-window counters after
resolving the source address through the configured trusted-proxy policy. The
default application limits are:

- 20 OAuth initiations per source IP per 10 minutes;
- 10 failed/invalid callbacks per source IP per 10 minutes; and
- 5 signed-but-denied callbacks per 15 minutes for each keyed Google `sub`/email
  fingerprint, plus the callback IP limit.

Exceeding a limit returns the same safe denial response with `429` and a
progressive `Retry-After`, capped at one hour. No identity receives a permanent
or global account lock; a successful authorized login clears only its identity
failure counter. Adding or broadening a login rule atomically increments a
denial-counter namespace version, invalidating existing identity-denial counters
so a newly authorized user is not held by earlier denials; it audits the reset
without clearing per-IP abuse counters. Counter keys and logs never store raw
callback tokens or an email solely for throttling. Deployment YAML may tune
thresholds, but production startup warns about values weaker than the defaults.

Early Django middleware applies a coarse token bucket to `/admin/login` and the
OAuth callback after trusted-client-address resolution but before OAuth
state/session allocation or django-allauth handling: 60 requests per source IP
per minute with a burst of 20, returning `429` for excess traffic. The more
specific sliding-window limits above still run for admitted requests. The
application additionally detects 100 failed or denied Admin callbacks across at
least 10 source IPs or identity fingerprints within five minutes. Crossing that
deployment-wide threshold emits one deduplicated WARNING/Admin notification and
increases progressive backoff; sustained abuse for three windows becomes
CRITICAL. Local and production deployments exercise the identical application
limits; stock Caddy provides no authentication rate-limit module.

The deployment-wide counter includes callback attempts rejected by either
specific sliding-window limiter. Such a request contributes only its keyed,
short-lived source-address fingerprint and, when already safely available, its
identity fingerprint; it does not allocate OAuth state, parse or retain a raw
token, call Google, or reach django-allauth. This telemetry increment occurs
even though the request receives its ordinary `429`, ensuring coordinated
traffic can cross the aggregate threshold after individual sources have been
limited. One request contributes only once to the aggregate counter.

Early middleware also applies a coarse token bucket to every
`/access/<token>` exchange, before token digest computation or database lookup:
120 requests per trusted source IP per minute with a burst of 30. Valid and
invalid tokens consume the same bucket and excess requests receive a generic
`429` with bounded `Retry-After`; limiter keys and telemetry contain only a
short-lived keyed source-address fingerprint, never the path or token. Valkey
is authoritative during normal operation. If Valkey is unavailable, each web
process immediately uses an equivalent bounded in-memory bucket so secure links
remain available with a per-process rather than deployment-wide ceiling. The
fallback resets on process restart, does not weaken the separate readiness/
CRITICAL signal, and returns to Valkey automatically when it recovers.

Admin sessions have a 30-minute idle timeout and 12-hour absolute lifetime.
Family sessions have a 60-minute idle timeout and four-hour absolute lifetime.
Both receive a visible warning before idle expiry. Privileged operations such
as Production transition, campaign reopening, ParishSoft publication, secret
replacement, and purge require fresh Google re-authentication no older than
five minutes.

Passive presence heartbeat and ordinary background polling never refresh idle
expiry. The sole setup exception is the first-Admin wizard's correlated staged-
source-load progress page: while that exact TaskRun remains nonterminal, its
CSRF-protected authenticated progress request may renew the bootstrap Admin's
30-minute idle deadline at most once every five minutes, but only while the
worker lease has a current valid heartbeat and for no more than two hours from
TaskRun creation. It carries only the wizard/task correlation, verifies the
same Admin/session server-side, and never extends the two-hour setup watchdog or
12-hour session lifetime. Renewal stops as soon as the task is terminal, the
worker heartbeat is stale, the watchdog expires, or the page stops polling; no
other wizard task, tab, Admin session, or ordinary background request qualifies.

While a Family form is visible, the conforming client schedules a CSRF-protected
activity keepalive after keyboard, input, pointer, or touch interaction, at most
once every five minutes. Merely focusing a tab, receiving a timer event, or
leaving it visible does not cause the supplied client to send one. The server
does not treat the client's activity claim as trustworthy or attempt to prove a
human interaction: any correctly authenticated, CSRF-valid keepalive within the
rate limit may refresh the 60-minute idle deadline. The request contains no
answers or field identifiers, returns the authoritative deadline, and never
extends the four-hour absolute lifetime. The idle-warning UI uses the returned
deadline and remains keyboard and screen-reader operable.

Authorization changes take effect on the next request and invalidate sessions
that no longer have any role. Removing the last specific-address Administrator
or the bootstrap Administrator before another Admin exists is prohibited.
An immediate exact-address Administrator grant creates the durable dashboard
security event and preexisting-Administrator operational notifications defined
by the Admin portal; notification delivery is not part of the grant transaction
and cannot erase or delay its audit evidence.

The no-reauthentication Administrator-grant policy is an explicit accepted
product risk favoring low-friction role maintenance. Its controls are detective,
not preventive: a compromised Admin session can create persistent access before
notification is acted upon. The durable event, preexisting-Admin notification,
CSRF/current-role checks, complete audit, and last-Admin guard are the selected
compensating controls; implementations must not imply they provide the same
protection as fresh authentication.

Cookies are `Secure` in production, `HttpOnly`, `SameSite=Lax`, narrowly
scoped, and rotated at login/privilege transition. Family and administration
sessions are separate namespaces; acquiring one never grants the other.

## Family credential security

Each participating Family receives one eight-character code per campaign. Code
generation uses `ABCDEFGHJKMNPQRSTUVWXYZ`, excluding visually confusable
`I`, `L`, and `O`. Codes are case-insensitive, collision checked, stable for
the campaign, and never recycled within it. A reactivated Family regains its
original code.

The manual code is a low-sensitivity, campaign-scoped access mechanism rather
than a high-security credential. Its usefulness ends when the campaign closes,
which limits disclosure impact. It remains encrypted at the application layer
to avoid accidental exposure from raw storage, while authorized Admin/Staff
report and export services may decrypt it in bulk. Versioned HMAC fingerprint
rows support unique lookup without decryption scans. Email links contain an
independent 256-bit random token. The reusable token is stored in a versioned
sealed-box ciphertext
envelope alongside an unkeyed SHA-256 lookup digest over a domain-separation
prefix and the token bytes; its entropy makes a rotatable lookup MAC
unnecessary. Incoming exchange uses only the digest. Only the mail-dispatch and
credential-rotation services may decrypt the token ciphertext; Admin pages,
reports, exports, logs, and general workers cannot.

Tokens are campaign-bound, reusable until invalidated, and rejected whenever
the campaign is closed or the Family is ineligible. Explicit rotation atomically
replaces ciphertext and digest, invalidating every prior email link without
changing the manual code. Campaign close destroys recoverable token ciphertext
and digest while retaining non-secret generation/revocation audit metadata; a
later guarded reopen generates new tokens before new Family mail can be sent.
Temporary Family ineligibility does not destroy the ciphertext, so reactivation
during the same open campaign can restore the existing link.

Stored uniqueness and submitted lookup use one canonical value: remove ASCII
spaces and hyphens, convert ASCII letters to uppercase, and require exactly
eight ASCII `A`-`Z` letters. The HMAC input is that validated canonical value.
Submitted `I`, `L`, and `O` are valid lookup candidates even though generation
never emits them; they therefore follow the same constant-behavior not-found
path as any other nonmatching candidate. Case and friendly delimiters cannot
create distinct credentials.

Access-token routes never log token path segments. Successful exchange rotates
the session, redirects to a clean URL, and emits `Referrer-Policy: no-referrer`.
Family pages and responses use `Cache-Control: no-store`.
Every administration report response containing Family PII, Family codes,
financial data, or census data uses the same no-store policy.
Exact-code search values are accepted only in a CSRF-protected POST request
body, never a URL/query string, and are omitted from application/proxy request
logs. Code-bearing exports use the ordinary authenticated temporary-export
controls
and complete report/export audit defined by the
[Family-code report](../reports/spec.md#family-code-lookup). Codes remain absent
from application logs, operational notifications, and unprivileged reports.

Failed Family-code attempts use Valkey sliding-window limits keyed by source IP
and by source-IP/code-fingerprint pair. Defaults are five failures per pair per
15 minutes and ten failures per IP per 10 minutes, followed by `429` responses
with increasing retry intervals. There is no limiter or lock keyed only by a
code fingerprint: failures from one or more other source addresses cannot
disable a valid Family credential. A successful request remains usable unless
its own source IP is limited and clears only that IP/code-pair failure counter.
Every unsuccessful eight-letter candidate, including one containing `I`, `L`,
or `O`, consumes both applicable failure counters. A server request with the
wrong length or nonletter input consumes the per-IP counter but has no
code-fingerprint counter; ordinary browser validation rejects that format
before submission.

The application also detects a distributed guessing burst when at least 100
invalid attempts, representing at least 100 distinct code fingerprints across
at least 20 source IPs, occur within five minutes. Crossing that deployment-wide
threshold emits one deduplicated WARNING/Admin notification, temporarily
tightens the per-IP limit, and adds progressive delay to unsuccessful responses;
valid codes continue to succeed. Abuse sustained for three consecutive windows
becomes CRITICAL. Recovery expires the elevated controls automatically and may
send one resolved notification. Thresholds are deployment-configurable, but
production startup warns about values weaker than these defaults. Counter keys,
logs, and notifications never contain plaintext codes. Error messages and
timing do not distinguish unknown, inactive, or non-Parishioner codes.

Attempts rejected by either Family-code sliding-window limiter still contribute
exactly once to the deployment-wide detector, using only the same keyed,
short-lived source-address and candidate fingerprints. Rejection never prevents
aggregate WARNING/CRITICAL detection or causes a plaintext candidate to be
retained.

Valkey limiter storage is required for public routes that accept guessable
credentials. If it is unavailable, Admin OAuth initiation/callback and manual
Family-code submission fail closed before credential evaluation with the same
generic temporary-unavailability response and bounded `Retry-After`. Existing
authenticated sessions, authorized Admin/Staff Family-code reports/search, and
`/access/<token>` exchange remain available because they do not expose a public
guessing oracle; token exchange uses the bounded per-process anti-flood fallback
above. Limiter-store unavailability
makes readiness unhealthy and creates one deduplicated durable CRITICAL event;
notification delivery resumes from PostgreSQL-backed work when workers can run.

Rate-limit windows are intentionally ephemeral. A Valkey restart or eviction may
start affected windows empty; the application records that loss but does not
reconstruct counters from security logs. This accepted reset never permits
serving a guessable-credential route while the limiter store itself is
unavailable.

## Web security and privacy

The implementation follows Django deployment checks and OWASP guidance:

- CSRF protection on every state-changing browser request;
- strict host/origin validation and trusted-proxy configuration;
- HTTPS redirect, HSTS, secure headers, frame denial, MIME sniff prevention,
  and a restrictive Content Security Policy;
- server-side authorization on every object query, export, and job action;
- parameterized ORM queries and output escaping;
- allowlist sanitization for Admin rich text, excluding scripts, forms,
  event-handler attributes, arbitrary styles, and remote tracking content;
- upload signature/type checks, image re-encoding, decompression limits, and
  randomized storage names;
- CSV formula-injection neutralization and safe XLSX/PDF generation; and
- log redaction for credentials, session IDs, access tokens, and Family-link
  secrets.

Production volumes and off-host backups must be encrypted. Application-level
encryption protects credentials, Family display codes, and other values whose
operational lookup requires reversible storage. Ordinary census data relies on
encrypted storage plus strict role access; field-level encryption must not make
required reporting/search impractical.

## Availability and performance

Production targets a recoverable single VM, not high availability. All
services use health checks and restart policies. A web or worker restart may
delay work but must not lose an accepted submission, approved review decision,
or outbox item.

At a reference size of 10,000 Members, 5,000 Families, 500 Ministries, and 100
concurrent Family sessions:

- ordinary cached pages should have a p95 server response below two seconds;
- filtered report first pages should return below three seconds;
- long polls, publication, digests, exports, purge, and backups are always
  asynchronous; and
- interactive traffic remains responsive while all worker categories run.

The default participation graph meets these targets through immutable
`CampaignDailyFactSet` materialization keyed by its exact source/submission/
scope/timezone inputs. Source promotions and live submissions enqueue
idempotent rebuild hints. Web, export, and digest rendering share those facts;
no request performs one independent corpus aggregation per campaign day.

Database indexes cover campaign/Family DUID, normalized email/domain, code
fingerprint, the campaign-scoped access-token lookup digest with uniqueness,
submission state/time, Ministry, workflow status, log time/level, and outbox/
task state. Pagination is server-side for potentially large tables.

## Accessibility and client behavior

The UI meets WCAG 2.2 AA: semantic landmarks, labels and instructions, keyboard
operation, visible focus, sufficient contrast, error summaries with field
links, non-color-only change indicators, reduced-motion support, and accessible
table/chart alternatives. Generated PDFs use tagged structure where the chosen
renderer supports it; every chart has an equivalent data table.

Client validation improves feedback but never replaces server validation.
Browser-local timezone conversion uses UTC ISO timestamps supplied by the
server. If JavaScript is disabled, administration CRUD and reports retain core
functionality; the Family multi-step flow may require JavaScript but must show a
clear supported-browser message rather than silently fail.
