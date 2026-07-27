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

## Identity and session security

Administration authentication uses Google through django-allauth with OAuth
authorization code flow, state, nonce, and PKCE. Only a Google-verified email is
accepted. The stable Google `sub` identifies the external account; normalized
email is re-evaluated against current login rules on every login and privileged
request. Password, recovery, signup, and non-Google authentication endpoints
are disabled.

Admin sessions have a 30-minute idle timeout and 12-hour absolute lifetime.
Family sessions have a 60-minute idle timeout and four-hour absolute lifetime.
Both receive a visible warning before idle expiry. Privileged operations such
as Production transition, campaign reopening, ParishSoft publication, secret
replacement, and purge require fresh Google re-authentication no older than
five minutes.

Authorization changes take effect on the next request and invalidate sessions
that no longer have any role. Removing the last specific-address Administrator
or the bootstrap Administrator before another Admin exists is prohibited.

Cookies are `Secure` in production, `HttpOnly`, `SameSite=Lax`, narrowly
scoped, and rotated at login/privilege transition. Family and administration
sessions are separate namespaces; acquiring one never grants the other.

## Family credential security

Each participating Family receives one six-character code per campaign from
the alphabet `ABCDEFGHJKMNPQRSTUVWXYZ`. Codes are case-insensitive, collision
checked, stable for the campaign, and never recycled within it. A reactivated
Family regains its original code.

Because Staff must retrieve codes, the display value is encrypted at the
application layer; an HMAC fingerprint supports unique lookup without
decryption scans. Email links contain an independent 256-bit random token.
Only its hash is stored. Tokens are campaign-bound, reusable until invalidated,
and cease working when the campaign closes or Family becomes ineligible. An
Admin may rotate a suspected token without changing the manual code.

Access-token routes never log token path segments. Successful exchange rotates
the session, redirects to a clean URL, and emits `Referrer-Policy: no-referrer`.
Family pages and responses use `Cache-Control: no-store`.

Failed Family-code attempts are limited per IP and code fingerprint. Defaults
are five failures per fingerprint per 15 minutes and ten failures per IP per 10
minutes, followed by `429` responses with increasing retry intervals. Limits
are configurable only to stricter values in production. Error messages do not
distinguish unknown, inactive, or non-Parishioner codes.

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
