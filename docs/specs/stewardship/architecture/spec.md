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

The web UI uses Django templates and progressive enhancement, except that the
Admin portal [requires
JavaScript](../admin-portal/spec.md#javascript-requirement). Small,
self-hosted JavaScript modules manage the Family wizard, inline validation,
browser-timezone rendering, and interactive charts. It is not a separately
deployed single-page application. Static assets are versioned and served by
the reverse proxy in production. Rich text uses a self-hosted WYSIWYG editor
and is sanitized on input and output.

Production Compose contains:

- `web`: Gunicorn-hosted Django application;
- `config-installer`: the only online service with write access to the Stewardship
  configuration-authority directory;
- target-specific `credential-installer-*` workers, each able to decrypt only
  its own staged replacement and write only its own credential subdirectory;
- `worker`: general Celery workers for polls, rendering, exports, publication,
  purge, and cleanup, with ParishSoft source work on a second process in the
  same container (see
  [worker queues and processes](../background-processing/spec.md#worker-queues-and-processes));
- `backup-worker`: a dedicated queue/service with only database/media/config
  read access, backup-target credentials, and the active data-backup key;
- `mail-dispatch`: a dedicated Celery worker for provider submission;
- `token-key-rotation`: an explicitly invoked maintenance profile that alone
  performs link-token private-key migration/rotation;
- `scheduler`: exactly one scheduler process that materializes due work;
- `postgres`: PostgreSQL with a durable volume;
- `valkey`: broker/cache with a durable local volume, though task correctness
  cannot depend on broker persistence; and
- `proxy`: Caddy on ports 80/443 with durable ACME state.

Initial provisioning uses the separate operator-only, one-shot
[offline bootstrap profile](../operations/spec.md#offline-bootstrap-profile).
Its narrowly scoped write exception does not expand online service mounts.

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
- `/files/<token>`: unauthenticated hosted files for parishioners, defined by
  [hosted files](../hosted-files/spec.md#public-serving);
- `/admin/login`: Google-only login;
- `/admin/...`: every administration page, JSON/HTML partial endpoint, export,
  job detail, and purge workflow.

Health and metrics are internal operational interfaces, not public interfaces.
Their authoritative paths, response disclosure, caller, authentication, and
ingress behavior are defined only by
[operations](../operations/spec.md#observability-and-health).

There is no public REST/GraphQL API. Internal browser endpoints use the same
cookie session, authorization, CSRF, rate limits, and audit policy as their HTML
pages. They return stable machine-readable validation errors but are not a
supported third-party contract.

The package exposes a `pk-stewardship` console entry point and thin executable
wrapper. Subcommands cover bootstrap, configuration validation, migration,
health diagnostics, backup, operator-only secret escrow, Admin-access recovery,
and restore. Web-
serving and worker commands remain container entry points that import package
code. There is no console campaign purge; purge is intentionally a guarded
Admin web workflow.

The host-only [Admin automation interface](../admin-automation/spec.md)
(`pk-stewardship admin`) reaches Admin portal actions from inside the web
container through the same service layer, authorization and audit as the
pages. It is neither a public API nor a network listener.

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
Selected-but-unapplied cancellation is limited to the journaled
[exceptional end-edit and initial-setup aborts](../data/spec.md#parish-and-integrations).
Neither can rewind an applied version or act as general configuration rollback.

Deployment configuration required to reach PostgreSQL is changed by the
documented operator workflow, not the web installer. Dynamic runtime state is
never exported to YAML merely because it names a parish or campaign.

Secret UI controls show only presence, last replacement time, and a fingerprint
safe for identification. They never return an existing secret. Replacement is
submitted over the authenticated TLS page, immediately sealed to the public
handoff key for that secret type, and retained only as an expiring ciphertext
linked to a `SecretReplacementRequest`. The web process does not retain
plaintext or possess a handoff private key. The matching target-specific
installer decrypts in memory, validates/tests the candidate, atomically replaces
the owner-only file, records the safe fingerprint, and destroys staged
ciphertext. Consumers acknowledge the new fingerprint before the UI reports
success. Failure or expiry destroys staging and leaves the old working
credential installed. The authoritative mount and cross-target isolation rules
are defined only by
[operations](../operations/spec.md#runtime-storage). Every path defaults below
`PARISHKIT_ROOT` or `/opt/parishkit` and remains overridable through deployment
configuration.

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
Only `mail-dispatch` and the explicitly invoked `token-key-rotation` profile
receive the private decryption-key ring. Rotation first distributes a new public
key, makes it active for encryption, re-encrypts retained ciphertext in
idempotent batches inside `token-key-rotation`, verifies migration, and retires
an old private key only after retained backups no longer require it. This
keyring is independent of the general application and Family-code MAC keyrings.

Family-code lookup uses a distinct versioned MAC keyring. Every fingerprint row
records its MAC algorithm/version and key ID; one key is active for new rows and
older keys may be lookup-only during migration. A lookup computes the
domain-separated HMAC of the canonical candidate under every accepted key and
matches any corresponding row.

Key usage distinguishes `active` (new fingerprints and authentication),
`lookup-only` (authentication during migration), and `collision-only`
(reservation checks, never authentication). Rehearsal reservations use a
separate domain-separation prefix plus campaign UUID and canonical code; they
are never queried as login fingerprints. Rehearsal issuance checks its active
key and every key referenced by that campaign's retained reservations, including
collision-only keys, using the stable key-set/generation lock protocol below.
Missing a required reservation key blocks new rehearsal-code issuance with a
sanitized diagnostic rather than silently weakening non-reuse; it does not
make that key accepted for login.

MAC-key rotation installs the new key, makes it active, and idempotently
backfills new-version fingerprint rows by decrypting each retained display code.
Bulk code generation runs inside its surrounding atomic `READ COMMITTED`
population/promotion transaction. It locks the campaign's code-generation row,
holds the accepted MAC-key set stable against rotation, and generates bounded
candidate batches in memory. One set-based query rejects any candidate whose
digest under any accepted key already exists and bulk-inserts every fingerprint
row for the remaining candidates. The unique `(campaign, key ID, digest)`
indexes remain a final safety constraint. A batch-level savepoint/retry handles
an unexpected constraint race; the number of subtransactions is bounded by
retry rounds, never Families, and accepted existing codes never change. This
also prevents duplication of a code indexed only under an older accepted key.
The prior key and rows may be retired only after all online Families have a new-
version row and every retained backup containing old-only rows either remains
paired with the prior key or has been re-encrypted/migrated. Failure leaves both
versions accepted and the migration retryable.

Successful authentication-fingerprint migration may move an old key to
collision-only even while anonymous rehearsal reservations still reference it.
Those HMAC-only records cannot be rekeyed after their display-code ciphertext
has been deleted: do not retain/reconstruct plaintext or pretend to migrate a
digest without it. Retain the key for collision checking until the last
referencing campaign reservation is purged, potentially indefinitely for an
unpurged campaign. Only then may it leave the operational keyring; its escrow
copy remains subject to every retained backup's key requirements. Key inventory,
backup manifests, rotation status, and retirement checks include collision-only
dependencies separately from login acceptance. Public code lookup iterates
only active/lookup-only keys and cannot authenticate using a reservation.

## Identity and session security

Administration authentication uses Google through django-allauth with OAuth
authorization code flow, state, nonce, and PKCE. Only a Google-verified email is
accepted. The stable Google `sub` identifies the external account; normalized
email is re-evaluated against current login rules on every login and privileged
request. Password, recovery, signup, and non-Google authentication endpoints
are disabled. The only exception is the
[local test sign-in](../local-environment/spec.md#local-test-sign-in), which
exists only in the LOCAL deployment profile. An
[automation session](../admin-automation/spec.md#automation-sessions) is not
an authentication exception: an Administrator approves it from an Admin
session that signed in with Google within five minutes, and it then acts as
that Administrator, including for
[fresh-gated actions](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line),
until it expires (at most 30 days) or is revoked.

If an external account rename/deactivation leaves no usable Admin login, an
authorized host operator may use the separate
[offline Admin-access recovery workflow](../operations/spec.md#offline-admin-access-recovery).
It repairs an exact-address grant through the configuration authority; it does
not authenticate anyone, migrate a Google `sub` binding, or add a web recovery
endpoint. The replacement account must complete ordinary Google login and
current-rule authorization. First-time bootstrap is not a recovery mechanism.

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

Every per-source limit and source count in this specification (the token
buckets, the per-IP and IP/code-pair windows, and the distinct-source counts of
the deployment-wide detectors) keys on the source's network, not its exact
address: an IPv4 address as is, an IPv6 address by its /64 prefix, and an
IPv4-mapped IPv6 address (`::ffff:192.0.2.1`) as the IPv4 address it names. One
IPv6 end site normally holds a whole /64, so keying each address separately
would give one attacker a new budget per address. Hosts sharing a /64, like
hosts behind one IPv4 NAT, share its budget. The resolved client address itself
stays exact everywhere else.

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
limits. The proxy's non-participation in authentication limiting is owned by the
[production ingress specification](../operations/spec.md#production-ingress-and-tls).

The deployment-wide counter includes callback attempts rejected by either
specific sliding-window limiter. A request rejected by a pre-verification IP
limiter contributes only its keyed, short-lived source-address fingerprint and
does not allocate OAuth state, parse or retain a raw token, call Google, or
reach django-allauth. The keyed-identity limiter necessarily runs only after
provider exchange and signed identity verification; its rejection contributes
the safe identity and source-address fingerprints but retains no raw callback/
provider token. Either path increments telemetry despite its ordinary `429`,
and one request contributes only once to the aggregate counter.

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

Public authentication failures use bounded, sampled durable audit signals: at
most one event per deployment per five minutes independently for invalid secure
Family links, failed manual Family codes, and denied Admin logins. PostgreSQL
time and cross-process exclusion enforce this bound, including during Valkey
outages. Samples contain the rejection class/outcome, not attempted values,
token paths, source addresses, or source/candidate/token fingerprints. They are
not an exact attempt count or a replayable history of failed authentication.
Per-attempt accounting for distributed-abuse detection remains in the
short-lived keyed telemetry described above when its backing store is available;
sampling never substitutes for that accounting. Every successful login remains
individually audited with its session and actor attribution, without credentials.

Admin sessions have a 60-minute idle timeout and 12-hour absolute lifetime.
Family sessions have a 60-minute idle timeout and four-hour absolute lifetime.
Both receive a visible warning before idle expiry. On every Admin page,
including the initial-setup wizard, one shared modal dialog opens five minutes
before the session's nearer deadline with a live countdown. Its **Stay signed
in** button is a CSRF-protected POST that counts as Admin activity and renews
only the idle deadline (and therefore the setup attempt's, which follows the
same session); it never extends the absolute lifetime, so when that limit is
the nearer one the dialog says so and offers only signing in again. Before
warning, when the tab becomes visible again, and periodically while the dialog
is open, the page re-reads the deadlines from a passive status endpoint that
renews nothing, so activity in another tab dismisses the warning. When the
deadline passes the dialog reports that the Admin was signed out and links to
sign-in. A status read that finds the session already ended (signed out in
another tab, revoked, or the Admin's access removed) ends the countdown at once
and shows the same dialog without blaming inactivity. A changed role is not an
ended session: the status read still reports the deadlines, and the next page
the Admin opens applies the new role. A failed status read (a server error or
no connection) keeps the countdown going. Privileged operations such
as Production transition, campaign reopening, ParishSoft publication, secret
replacement, and purge require fresh Google re-authentication no older than
five minutes, as do Family-directory, mail-merge and financial exports,
integration setting changes and starting Testing cleanup ([#547](https://github.com/epiphany40223/parishkit/issues/547)). The one exception is a full-scope
[automation session](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line),
which an Administrator approved with a fresh sign-in and which stands in for it
for the actions listed there.

Fresh authentication is a step-up of the current session, not a new login.
When a privileged action finds the session's verified Google instant too old,
the Admin page offers **Confirm with Google**, which posts to the ordinary
sign-in with the current page as its return path; a form whose address
accepts only a submission (an export) returns to the page the form came from
instead, so nothing is posted again. That return path is
accepted only as a same-origin `/admin/` page path; anything else returns to
the Admin home page. If the browser still holds a live, authorized session
for the same Google account, the callback advances that session's verified
instant in place (never backwards and never past the database clock),
records activity and a step-up audit event, rotates the CSRF secret, and
returns to that page. The session key, lifetime and session-bound work, such
as the initial setup wizard, are unchanged. A different Google account, or a
revoked, idle or expired session, gets a new session as at first login. Keeping
the key does not create session fixation: every initial login already
replaced any pre-login key with a new server-issued one, and step-up changes
neither the principal nor its authority, which is re-derived from current
policy on every request.

Passive presence heartbeat and ordinary background polling never refresh idle
expiry. The sole setup exception is the first-Admin wizard's correlated staged-
source-load progress page: while that exact TaskRun remains nonterminal, its
CSRF-protected authenticated progress request may renew the bootstrap Admin's
60-minute idle deadline at most once every five minutes, but only while the
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
An immediate exact-address Administrator grant, creation of any domain rule, or
addition of Staff to an existing domain rule creates the durable dashboard
security event and preexisting-Administrator operational notifications defined
by the Admin portal. Notification delivery is not part of the activation
transaction and cannot erase or delay its audit evidence.

The no-reauthentication role-change policy is an explicit accepted product risk
favoring low-friction role maintenance. Its controls are detective, not
preventive: a compromised Admin session can create persistent or domain-wide
access before notification is acted upon. The durable event and preexisting-
Admin notification for high-impact expansions, CSRF/current-role checks,
complete audit, and last-Admin guard are the selected compensating controls;
implementations must not imply they provide the same protection as fresh
authentication.

Cookies are `Secure` in production, `HttpOnly`, `SameSite=Lax`, narrowly
scoped, and rotated at login/privilege transition. Family and administration
sessions are separate namespaces; acquiring one never grants the other.

## Family credential security

Each participating Family receives one Production eight-character code per
campaign. Code
generation uses `ABCDEFGHJKMNPQRSTUVWXYZ`, excluding visually confusable
`I`, `L`, and `O`. Codes are case-insensitive, collision checked, stable for
the campaign, and never recycled within it. A reactivated Family regains its
original code.

Testing email uses a separate rehearsal credential set, never the Production
code or token, including explicit readiness-test sends made while global mode
is Production. Such sends may prepare rehearsal credentials under their
maintenance/readiness authorization but never make them usable in Production
or bypass restore, campaign-date, or go-live gates. Safe-sample previews without
a real eligible Family use clearly labelled non-authenticating placeholders.
Testing codes are eight letters: reserved leading `I` followed
by seven random letters from the Production generation alphabet. This disjoint
format prevents a retained Testing code from matching any Production code,
including in a later campaign. Testing link tokens use a `test.` discriminator
followed by an independent 256-bit random payload at the ordinary access route;
Production tokens never use that discriminator. Both use the same encryption,
redaction, normalization, abuse controls, and clean-session exchange protections
as their Production equivalents. Testing lookup is additionally scoped to the
current campaign and current rehearsal epoch; Production rejects Testing
credentials without falling back to another namespace. Testing likewise does
not accept Production credentials. Admin/Staff code reports retain their
authorized access to the stable Production codes; rehearsal credentials are
identified separately in Testing mail, not substituted into those reports.

Go-live gate acquisition invalidates the rehearsal epoch and all its Family
sessions before cleanup begins. Every Family request checks its session's mode
and epoch; an existing Testing session cannot become a Production session.
Cleanup destroys rehearsal credentials and sealed message substitutions. A
cancelled transition, pre-start withdrawal, or later Testing return creates a
fresh epoch lazily when rehearsal next begins, never reviving old credentials.
Final Production activation requires the prior epoch to remain invalidated and
its sensitive credential records to be removed. Stable Production codes and
Production token generations are not replaced by this cleanup. Storage and
retired-code reservations follow the
[data model](../data/spec.md#family-campaign-identity).

The manual code is a low-sensitivity, campaign-scoped access mechanism rather
than a high-security credential. It is unusable while the campaign is closed,
but the same code becomes usable again if that campaign is formally reopened.
It never grants access to another campaign, and a successor campaign issues a
new code, limiting disclosure to one Family in one campaign. It remains
encrypted at the application layer to avoid accidental exposure from raw
storage, while authorized Admin/Staff report and export services may decrypt it
in bulk. Versioned HMAC fingerprint rows support unique lookup without
decryption scans. Email links contain an independent 256-bit random token. The
reusable token is stored in a versioned sealed-box ciphertext
envelope alongside an unkeyed SHA-256 lookup digest over a domain-separation
prefix and the token bytes; its entropy makes a rotatable lookup MAC
unnecessary. Incoming exchange uses only the digest. Only `mail-dispatch` and
`token-key-rotation` may decrypt the token ciphertext; Admin pages,
reports, exports, logs, and general workers cannot.

Tokens are campaign-bound, reusable until invalidated, and rejected whenever
the campaign is closed or the Family is ineligible. Explicit rotation atomically
replaces ciphertext and digest, invalidating every prior email link without
changing the manual code. Campaign close destroys recoverable token ciphertext
and digest while retaining non-secret generation/revocation audit metadata; a
later guarded reopen prepares a new inactive token generation asynchronously
and selects it only at final confirmation, before new Family mail can be sent.
All Production token lookup and dispatch checks use the Campaign's active generation
pointer as defined by the [data model](../data/spec.md#family-campaign-identity).
Temporary Family ineligibility does not destroy the ciphertext, so reactivation
during the same open campaign can restore the existing link.

Restore is an exception to that reuse: it invalidates every restored link and
prepared generation before web access resumes. A scheduled/active release
requires fresh generation preparation and atomic activation under the
[restore workflow](../admin-portal/spec.md#restore-release). Manual codes remain
stable; no token rotation implicitly authorizes another email delivery.

Stored uniqueness and submitted lookup use one canonical value: remove ASCII
spaces and hyphens, convert ASCII letters to uppercase, and require exactly
eight ASCII `A`-`Z` letters. The HMAC input is that validated canonical value.
Submitted `I`, `L`, and `O` are valid lookup candidates even though Production
generation never emits them. Only a matching current Testing code may succeed
in Testing; otherwise these candidates follow the same constant-behavior
not-found path as any other nonmatching candidate. Case and friendly delimiters
cannot
create distinct credentials.

Access-token routes never log token path segments. Successful exchange rotates
the session, redirects to a clean URL, and emits `Referrer-Policy: no-referrer`.
Every other response uses `Referrer-Policy: same-origin`: nothing is sent to
another site, and browsers keep sending the real `Origin` with same-site form
POSTs, which the CSRF check requires (under `no-referrer` they send
`Origin: null`).
Family pages and responses use `Cache-Control: no-store`.
The security middleware sets `Cache-Control: no-store` on every response
except static assets and two public, anonymous byte routes whose views choose
their own caching: [hosted files](../hosted-files/spec.md) under `/files/`
(`no-cache` with an ETag) and retained branding images under `/branding/`
(`public, max-age=31536000, immutable`, since each image URL names one
immutable asset). It keeps a view's own caching only for a marked `200` or
`304` response on those paths that neither sets nor varies on cookies, so a
private, authenticated or error response is never made cacheable by mistake.
Every administration report response containing Family PII, Family codes,
financial data, or census data uses the same no-store policy.
Exact-code search values are accepted only in a CSRF-protected POST request
body, never a URL/query string, and are omitted from application/proxy request
logs. Code-bearing exports use the ordinary authenticated temporary-export
controls
and complete report/export audit defined by the
[active parishioner family directory](../reports/spec.md#active-parishioner-family-directory). Codes remain absent
from application logs, operational notifications, and unprivileged reports.

Failed Family-code attempts use Valkey sliding-window limits keyed by source IP
and by source-IP/code-fingerprint pair, where "source IP" is the source network
defined under [identity and session security](#identity-and-session-security).
Defaults are five failures per pair per 15 minutes and 100 failures per IP per
10 minutes, followed by `429` responses with increasing retry intervals. There is no limiter or lock keyed only by a
code fingerprint: failures from one or more other source addresses cannot
disable a valid Family credential. A successful request remains usable unless
its own source IP is limited and clears only that IP/code-pair failure counter.
Every unsuccessful eight-letter candidate, including one containing `I`, `L`,
or `O`, consumes both applicable failure counters. A server request with the
wrong length or nonletter input consumes the per-IP counter but has no
code-fingerprint counter; ordinary browser validation rejects that format
before submission.

The larger per-IP allowance accommodates different Families sharing parish
Wi-Fi or another network egress address. Those Families still share the IP
budget, and reaching it limits valid requests from that address until the
window permits them again. The smaller pair limit constrains repeated attempts
against one candidate without consuming the entire shared-network allowance
after only a few mistakes. Both defaults remain deployment-configurable.

The application also detects a distributed guessing burst when at least 100
invalid attempts across at least 20 source IPs occur within five minutes.
Distinct candidate-fingerprint count is diagnostic information only, never a
prerequisite for detection; repeated guesses from a small dictionary count
toward the same attempt threshold. Crossing that deployment-wide
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

Authentication recovery notifications require observed evidence. Abuse and
counter-loss episodes resolve after five minutes of healthy observations, with
no observation gap greater than 90 seconds. Each observation verifies actual
store continuity and reads current aggregate counts without adding attempts or
extending counter lifetimes. Missing evidence, a new failure, counter loss or
a longer gap resets the window; elapsed silence alone cannot resolve an episode.
Store-unavailability recovery still follows a successful real store operation.
Continuing unavailability updates a fixed-size recovery fence on each failure,
without creating per-request history rows. It adds at most one operational
episode observation per minute; notification repetition follows the independent
[suppression policy](../background-processing/spec.md#critical-errors-and-notification).

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
  secrets, including in debug logging (see
  [production ingress](../operations/spec.md#production-ingress-and-tls)).

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
  asynchronous, except a small CSV rendered on request from the rows of the
  page it is downloaded from (see
  [campaign read guards](../data/spec.md#campaign-read-guards)); and
- interactive traffic remains responsive while all worker categories run.

These targets also apply with the guarded-download admission limit saturated
by slow transfers. Use the dedicated download pool and reserved database/web
execution capacity defined by the
[operations budget](../operations/spec.md#download-capacity-and-timeouts).

The default participation graph meets these targets through immutable
`CampaignDailyFactSet` materialization keyed by its exact source/submission/
scope/timezone inputs. Source promotions and live submissions enqueue
idempotent rebuild hints. Web, export, and digest rendering share those facts;
no request performs one independent corpus aggregation per campaign day.

Database indexes cover campaign/Family DUID, normalized email/domain, code
fingerprint, the campaign-scoped access-token lookup digest with uniqueness,
submission state/time, Ministry, workflow status, log time/level, and outbox/
task state. Pagination is server-side for potentially large tables.

### Work-order lock scope

This section is the design for narrowing the common work-order lock
(PostgreSQL advisory lock `736220,1`) after the v1 launch. The measurements
behind it are in
[#147](https://github.com/epiphany40223/parishkit/issues/147). Readers
already avoid the lock ([Admin page
snapshots](../data/spec.md#admin-page-snapshots)), and export admission uses a
per-campaign lock ([export admission
order](../data/spec.md#export-admission-order)). What remains is the writers'
steady-state work.

The baseline below is what #147 measured, before changes 1 and 2 in [Ranked
changes](#ranked-changes). The lock is also the admission lock for every
background task step. Each handler's scope is the work transaction, so every
claim, effect, progress or heartbeat transition, in-flight check and scheduler
admission check joins it. Admission then locks the runtime row
(`SystemConfiguration`) `FOR UPDATE`. The busiest holders are:

- ParishSoft refresh staging: one hold per 125-row batch, back to back for the
  whole staging phase. Change 3 removed these holds.
- Fetch admission: one hold per ParishSoft request. Change 3 removed these
  holds.
- Family mail: eight holds per message in the consumer (the claim, the
  PREPARING progress, the effect, the submission, two in-flight checks, the
  outcome and the completion).
- The scheduler's admission check of each due row, repeated on every sweep
  until a consumer claims the row: about six holds per message during a
  launch-size backlog.
- The scheduler's Family schedule sweep: 20 of its 43 holds per idle loop,
  even when nothing is due. Change 5 removed these holds.

Because these holders all serialize with each other, a Family login waits too.
A login takes the runtime row `FOR SHARE`, so it waits for every admission
transaction that holds that row `FOR UPDATE`.

Changes 1 and 2 change this baseline for Family mail only. Its admission takes
the runtime and credential rows `FOR SHARE`, so logins no longer wait for it,
and a message joins the lock fewer times: the PREPARING progress commits with
the effect, and the SQL in-flight check runs at most once a second, so a
typical half-second send needs none.

#### Invariants

Any narrower scheme must keep what the lock provides today:

1. **No deadlocks.** Writers take one global order: the work lock, then task
   root and run rows, then domain rows.
2. **Atomic admit-then-write against transitions.** A transition sees every
   write admitted before it, and any admission after it sees the transition.
   Transitions include lifecycle and controls, configuration and credential
   activation, mode changes, restore holds, purge and go-live gates, and
   source promotion.
3. **Exclusion around shared mutable state.** Examples are the source pointer,
   the per-campaign submission sequence, and the runtime row.
4. **SQL guards stay the backstop.** About 50 SQL functions take the lock
   themselves, and most guards on writer tables refuse a transaction that does
   not hold it in `ExclusiveLock` mode.

#### Design

Split the order into two tiers.

- **Transitions keep the exclusive work lock.**
- **Steady-state work on one entity** (one task's steps, one message, one
  Family, one source attempt) holds that entity's own lock instead: a row lock
  or an advisory key. Two mechanisms keep it ordered against transitions:
  - **Share the rows transitions update.** This needs no schema change. The
    work takes the runtime, campaign, credential and source-lease rows
    `FOR SHARE` (or the lease row `FOR UPDATE`) before any task row. Every
    transition already locks those rows `FOR UPDATE` after the work lock, so
    the two still exclude each other. The exclusion does not depend on that
    explicit lock: any `UPDATE` of a row first takes a row lock that
    conflicts with `FOR SHARE`, so a writer that skips the explicit lock
    still waits for the shared work to commit.
  - **Hold the work lock in shared mode.** This needs a schema change. Guards
    must accept `ShareLock` together with the entity's key, and functions that
    take the exclusive lock must not upgrade a shared hold.
  - **Publish nothing; verify under the lock at completion.** This needs no
    schema change. Work whose writes nobody else can see yet takes neither
    the work lock nor the rows transitions update. A source refresh's
    staging is the example: snapshot membership and payload versions become
    source truth only when the snapshot is promoted. The work locks only its
    own task, lease and entity rows, and reads its admission without row
    locks, so it can stop early. The step that publishes takes the
    exclusive lock and re-verifies the whole scope. A transition that
    commits in between is then always seen before anything becomes visible.
    The worst case is wasted work in an attempt that is then refused. Change
    3 uses this pattern, and so may the parts of changes 8 and 9 that only
    read or stage (attempt start, the decode and validation before
    promotion). Change 9's Family writes and change 7's submissions publish
    as they go, so they cannot.

Three rules apply to every narrowed path:

- Never upgrade a lock inside a narrowed transaction. In particular, a
  transaction that takes the runtime row `FOR SHARE` must not later lock it
  `FOR UPDATE`. A Family login holds the same share, so the upgrade can
  deadlock with it.
- Take locks in a fixed order: the runtime row, then the campaign, then the
  task root, then the task run, then the entity's rows.
- Take singleton rows only `FOR SHARE` during admission.

#### Ranked changes

The changes are ranked by benefit to the launch send and to Family
responsiveness, against risk.

| Rank | Change | Schema | Risk |
| --- | --- | --- | --- |
| 1 | Family mail admission locks the runtime and credential rows `FOR SHARE`, so logins stop waiting for mail | no | low |
| 2 | Fewer lock takes per Family message: the PREPARING progress joins the effect, and the SQL in-flight check runs at most once a second | no | low |
| 3 | Source fetch admission and staging batches leave the work lock: lock the task, lease and snapshot rows, and read admission without locks | no | medium |
| 4 | The scheduler does not re-admit a due row it hinted within the last minute, while keeping the due-work health proof | no | medium |
| 5 | Family schedule sweep skips, without the lock, Families whose groups cannot change, with a periodic full sweep | no | medium |
| 6 | Task steps (claim, progress, heartbeat, settle, in-flight check, scheduler admission) in shared mode with a per-task key | yes | high |
| 7 | Family form issue and submission in shared mode with a per-Family key, plus a campaign row lock for the submission sequence | yes | high |
| 8 | Source attempt start and snapshot completion leave the work lock | yes | medium |
| 9 | Promotion precomputes its Family and chair effects outside the lock and applies them in a short, verified step | yes | high |

**Change 3** has the largest effect on the launch send while a refresh runs.
Issue #147 implemented it without a schema change. A full refresh of a
1,100-Family parish used to take the lock about 190 times and hold it for
21–24 s, about 85% of its database time. It now takes it about 40 times and
holds it for 2–5 s. What remains is 24 task progress transitions at about
15 ms each (20 staging reports, one per 500 staged rows, and the fetching,
first staging, validating and promoting reports), the attempt start,
snapshot completion and promotion.

A fetch admission and a staging batch are each a source step: one short
transaction outside the work lock. They publish nothing. A fetch admission
reserves the source lease for one request. A staging batch writes only its
own attempt's snapshot membership and immutable payload versions, which
become source truth only at promotion. Each step:

- waits out a configuration activation in progress;
- locks the task root and run with the claim's fence, then the source lease
  with its fence, then (for staging) the snapshot row, the order every other
  holder of those rows uses;
- reads the request's admission (runtime row, campaign, work gates, tenant,
  window and credential fingerprint) without row locks.

That admission read stops a stale attempt early but is not the proof. It
takes no runtime or campaign row lock, because transitions lock task rows
and the runtime row in both orders, so a step holding both could deadlock
with one of them. Snapshot completion and promotion keep the exclusive lock
and the `FOR UPDATE` admission and re-verify the attempt's whole scope, so a
transition that commits while staging runs is always seen before anything is
published. The SQL membership guard still refuses staging unless the
snapshot is staging and its lease and task are live.

The other holders of the source lease are scheduler supersession, setup
completion, compaction, credential switching and promotion. All of them
serialize on the lease row and the task fence. Compaction and rejection touch
only rejected or compacted snapshots, and a credential or window switch is
refused at the next step and, for certain, at completion.

A holder of the work lock that locks the lease row can now wait for one
source step to commit. The scheduler's hint or claim admission of a queued
second refresh is the main case. It also holds the runtime row
`FOR UPDATE`, so a Family login waits too. Before this change that holder
waited for the step's work lock instead, so the waits were already serial.
What is new is that other work-lock waiters queue behind the holder for
that time. The wait is bounded by one step (a staging batch is 15–280 ms).
On the common path, where hint and claim admission check for a live
lease, they read the lease with `SKIP LOCKED` and treat a row locked by a
step as busy, as they already treat a live lease, so they never wait. Setup
admission reads the lease without a row lock. Its answer is advisory,
because acquisition locks the row and rechecks both deadlines.

A few rarer admissions still lock the lease `FOR UPDATE` under the work
lock, and so may wait for one step:

- a hint or claim whose full-refresh fallback failed or was cancelled
  (the drain check);
- the recovery hint for an abandoned root;
- the safe-cancel, permanent-failure and retryable-failure actions.

Each waits at most one step, and none can deadlock: a step never waits for
the work lock, and it takes its row locks in the same order (task root,
task run, lease, snapshot).

A refresh's inputs step reads source corpora outside the lock too, with no
schema change. A quick update's base is the whole current snapshot, and a
current snapshot that predates recorded derived counts is counted from its
rows. Both reads used to run inside the inputs' work-order effect: about
200 ms of its 220–265 ms hold for a quick update at 1,100 Families. The
effect now holds the lock for about 50 ms. It still verifies the attempt
and its scope, and that the snapshot's base is the current snapshot. The
corpus is read after it commits, through the snapshot read (the promoted,
uncompacted snapshot row held `FOR SHARE`), and a promoted snapshot's rows
are immutable. Promotion, and recording an unchanged update, require the base
to still be current, so nothing read this way can be published stale.

**Change 4** is only an optimization. A hint is advisory, and the consumer
rechecks admission under its own locks, so a skipped re-check costs nothing.
However, the due-work health sample counts each admitted row. A row skipped
because it was hinted recently must still count as recently admitted, not as
unknown. #394 implemented this change before launch; the [durable
scheduling](../background-processing/spec.md#durable-scheduling-and-task-execution)
spec describes the behavior.

**Change 5** plans a Family only when its inputs or the campaign clock can
change its plan, with an hourly full sweep. #640 implemented it without a
schema change. An idle loop takes no work-order lock; the [Family schedule
sweep](../background-processing/spec.md#family-schedule-sweep) spec
describes it.

The other scheduler producers also take the work-order lock only when a
read without it finds something to do (#715), with no schema change. The
[durable
scheduling](../background-processing/spec.md#durable-scheduling-and-task-execution)
spec describes those reads and why each covers its locked pass.

**Changes 6–9** need schema changes, which the v1 schema freeze defers. The
SQL functions to change are:

- the task, delivery and Family guards that assert `mode='ExclusiveLock'`
- `stewardship_delivery_new_hold_v1` and `stewardship_control_guard_v1`,
  which take the lock themselves
- `stewardship_refresh_attempt_guard_v1` and
  `stewardship_refresh_snapshot_completion_v1`
- the `SystemConfiguration FOR UPDATE` in the baseline and submission guards

## Accessibility and client behavior

The UI meets WCAG 2.2 AA: semantic landmarks, labels and instructions, keyboard
operation, visible focus, sufficient contrast, error summaries with field
links, non-color-only change indicators, reduced-motion support, and accessible
table/chart alternatives. Generated PDFs use tagged structure where the chosen
renderer supports it; every chart has an equivalent data table.

A person never sees raw JSON. Request errors use closed, server-owned
messages. Scripts that ask for JSON (`Accept: application/json`, or any
non-navigation fetch) receive the machine-readable error codes. A browser
navigation or HTML form submission (`Sec-Fetch-Mode: navigate`, or an
explicit `text/html` Accept) instead receives an ordinary page in the site
layout with the same status code. That page shows the message, the next step
(correct and resubmit, reload, sign in again, or try later), a link back to
the same-origin Admin page the person came from, and the home link. A missing
fresh authentication offers **Confirm with Google** as described under
[identity and session security](#identity-and-session-security). Forms that
can re-render with inline field errors still do so.

Client validation improves feedback but never replaces server validation.
Browser-local timezone conversion uses UTC ISO timestamps supplied by the
server. The Admin portal [requires
JavaScript](../admin-portal/spec.md#javascript-requirement) and shows a plain
notice without it. The Family multi-step flow may require JavaScript but must
show a clear supported-browser message rather than silently fail. A browser
too old for the Family flow's JavaScript likewise gets a plain notice asking
the Family to update the device's software or use another device or browser; a
small ES5 feature check reveals it and never alters the form.
