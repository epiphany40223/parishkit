# Stewardship application architecture

This specification defines the technical architecture and cross-cutting
security/nonfunctional behavior. Domain records and workflows are defined by
the [data specification](../data/spec.md); deployment details are extended by
the [operations specification](../operations/spec.md).

## Technology and component model

The application targets Python 3.12 or newer and uses the maintained patch
release of Django 5.2 LTS. PostgreSQL 18 is the only supported production
database. Celery 5.6 executes asynchronous work with Redis as broker/cache.
PostgreSQL, not Redis, remains authoritative for schedules, outbox messages,
job state, sessions requiring audit visibility, and application data.

The web UI uses Django templates and progressive enhancement. Small,
self-hosted JavaScript modules manage the Family wizard, inline validation,
browser-timezone rendering, and interactive charts. It is not a separately
deployed single-page application. Static assets are versioned and served by the
reverse proxy in production. Rich text uses a self-hosted WYSIWYG editor and is
sanitized on input and output.

Production Compose contains:

- `web`: Gunicorn-hosted Django application;
- `worker`: Celery workers for polls, mail, exports, and publication;
- `scheduler`: exactly one scheduler process that materializes due work;
- `postgres`: PostgreSQL with a durable volume;
- `redis`: broker/cache with a durable local volume, though task correctness
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

The application reuses `parishkit.parishsoft`, `parishkit.retry`,
`parishkit.logging`, `parishkit.email`, Google credential helpers, and runtime
path helpers. Campaign models, authorization, content slots, reports, and
workflows must not leak into general modules.

## Public interfaces

The human-facing interface consists of:

- `/`: Family code entry or campaign-status page;
- `/access/<token>`: opaque email-link exchange, immediately redirected to a
  token-free Family URL after a session is established;
- `/family/...`: authenticated wizard steps and final submission endpoint;
- `/admin/login`: Google-only login;
- `/admin/...`: every administration page, JSON/HTML partial endpoint, export,
  job detail, and purge workflow; and
- `/health/live` and `/health/ready`: non-sensitive container health endpoints.

There is no public REST/GraphQL API. Internal browser endpoints use the same
cookie session, authorization, CSRF, rate limits, and audit policy as their HTML
pages. They return stable machine-readable validation errors but are not a
supported third-party contract.

The package exposes a `pk-stewardship` console entry point and thin executable
wrapper. Subcommands cover bootstrap, configuration validation, migration,
health diagnostics, backup, and restore. Web-serving and worker commands remain
container entry points that import package code. There is no console campaign
purge; purge is intentionally a guarded Admin web workflow.

## Configuration and secrets

Three layers are intentionally distinct:

1. **Deployment configuration**: YAML/environment values required before the
   database is reachable, such as database/broker hosts, public origin, trusted
   proxy count, and credential-file paths.
2. **Secret material**: files below `<root>/credentials`, written atomically as
   owner-only files. This includes Django signing/encryption keys, Google OAuth
   client secret, Workspace service-account JSON, ParishSoft API key, Slack bot
   token, and backup credentials.
3. **Web-managed configuration**: versioned PostgreSQL records for parish,
   campaign, content, fund/Ministry mappings, roles, and schedules.

This database-backed application configuration is an explicit exception to the
general YAML rule because it is transactionally edited, audited, and retained
per campaign. Import/export to YAML may be added later but is not an authority
in the first release.

Secret UI controls show only presence, last replacement time, and a fingerprint
safe for identification. They never return an existing secret. Replacement is
staged, validated, atomically installed, and audited without value disclosure.
Every path defaults below `PARISHKIT_ROOT` or `/opt/parishkit` and remains
overridable through deployment configuration.

Application encryption uses a versioned keyring. Every ciphertext envelope
records its algorithm/version and key ID; one key is active for writes and older
keys are decrypt-only during rotation. Rotation installs and validates the new
key, makes it active, re-encrypts retained values in idempotent transactional
batches, verifies that no online ciphertext references the old key, and only
then permits retirement. Failure leaves both keys usable and the migration
retryable. A key remains recoverable for any retained backup that needs it, or
that backup must be re-encrypted before retirement. Signing-key rotation keeps
the prior verification key only for the maximum lifetime of credentials issued
under it, then removes it after audit confirms the transition window ended.

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

Administration OAuth endpoints use shared Redis sliding-window counters after
resolving the source address through the configured trusted-proxy policy. The
default application limits are:

- 20 OAuth initiations per source IP per 10 minutes;
- 10 failed/invalid callbacks per source IP per 10 minutes; and
- 5 signed-but-denied callbacks per 15 minutes for each keyed Google `sub`/email
  fingerprint, plus the callback IP limit.

Exceeding a limit returns the same safe denial response with `429` and a
progressive `Retry-After`, capped at one hour. No identity receives a permanent
or global account lock; a successful authorized login clears only its identity
failure counter. Counter keys and logs never store raw callback tokens or an
email solely for throttling. Deployment YAML may tune thresholds, but production
startup warns about values weaker than the defaults.

Caddy also applies a coarse token-bucket limit to `/admin/login` and the OAuth
callback: 60 requests per source IP per minute with a burst of 20, returning
`429` before proxying excess traffic. The application additionally detects 100
failed or denied Admin callbacks across at least 10 source IPs or identity
fingerprints within five minutes. Crossing that deployment-wide threshold emits
one deduplicated WARNING/Admin notification and increases progressive backoff;
sustained abuse for three windows becomes CRITICAL. Development exercises the
application limits even when Caddy is absent.

Admin sessions have a 30-minute idle timeout and 12-hour absolute lifetime.
Family sessions have a 60-minute idle timeout and four-hour absolute lifetime.
Both receive a visible warning before idle expiry. Privileged operations such
as Production transition, campaign reopening, ParishSoft publication, secret
replacement, and purge require fresh Google re-authentication no older than
five minutes.

Passive presence heartbeat and background polling never refresh idle expiry.
While a Family form is visible, genuine keyboard, input, pointer, or touch
interaction may schedule a CSRF-protected activity keepalive at most once every
five minutes. The request contains no answers or field identifiers. The server
refreshes the 60-minute idle deadline and returns the authoritative deadline,
but never extends the four-hour absolute lifetime. Merely focusing a tab,
receiving a timer event, or leaving it visible does not qualify. The idle-warning
UI uses the returned deadline and remains keyboard and screen-reader operable.

Authorization changes take effect on the next request and invalidate sessions
that no longer have any role. Removing the last specific-address Administrator
or the bootstrap Administrator before another Admin exists is prohibited.

Cookies are `Secure` in production, `HttpOnly`, `SameSite=Lax`, narrowly
scoped, and rotated at login/privilege transition. Family and administration
sessions are separate namespaces; acquiring one never grants the other.

## Family credential security

Each participating Family receives one eight-character code per campaign from
the alphabet `ABCDEFGHJKMNPQRSTUVWXYZ`. Codes are case-insensitive, collision
checked, stable for the campaign, and never recycled within it. A reactivated
Family regains its original code.

Because Staff must retrieve codes, the display value is encrypted at the
application layer; an HMAC fingerprint supports unique lookup without
decryption scans. Email links contain an independent 256-bit random token.
Only its hash is stored. Tokens are campaign-bound, reusable until invalidated,
and cease working when the campaign closes or Family becomes ineligible. An
Admin may rotate a suspected token without changing the manual code.

Code generation, uniqueness, and lookup use one canonical value: remove ASCII
spaces and hyphens, convert ASCII letters to uppercase, then reject any
character outside the configured code alphabet or any result of the wrong
length. The HMAC input is that validated canonical value. Stored uniqueness and
submitted-code lookup use the identical canonicalization function, so case and
friendly delimiters cannot create distinct credentials.

Access-token routes never log token path segments. Successful exchange rotates
the session, redirects to a clean URL, and emits `Referrer-Policy: no-referrer`.
Family pages and responses use `Cache-Control: no-store`.

Failed Family-code attempts use Redis sliding-window limits keyed by source IP
and by source-IP/code-fingerprint pair. Defaults are five failures per pair per
15 minutes and ten failures per IP per 10 minutes, followed by `429` responses
with increasing retry intervals. There is no limiter or lock keyed only by a
code fingerprint: failures from one or more other source addresses cannot
disable a valid Family credential. A successful request remains usable unless
its own source IP is limited and clears only that IP/code-pair failure counter.

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

Database indexes cover campaign/Family DUID, normalized email/domain, code
fingerprint, submission state/time, Ministry, workflow status, log time/level,
and outbox/task state. Pagination is server-side for potentially large tables.

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
