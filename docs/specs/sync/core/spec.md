# ParishKit Sync — Core

The Django application that hosts sign-in, administration, settings and the
scheduler. Overview and goals: [Sync spec](../spec.md). General UX rules:
[Stewardship UX conventions](../../stewardship/ui-conventions/spec.md).

## Process model

- One container runs gunicorn with **one worker**; the worker also hosts the
  scheduler thread (see [jobs](../jobs/spec.md)). gunicorn listens only on a
  private Podman network behind the Caddy proxy (see
  [HTTPS](../deployment/spec.md#https)); Django trusts the proxy's forwarded
  scheme and host headers from that network only.
- SQLite in WAL mode on a data volume holds config, credentials, users,
  logs, audit records and module state.
- The admin UI requires JavaScript and refreshes live regions in place rather
  than holding long-lived connections.

## Sign-in

- OpenID Connect through django-allauth, with presets for **Google** and
  **Microsoft Entra**. Each parish registers its own OAuth client; the setup
  wizard explains how.
- There are no local passwords.
- Only addresses on the **administrator allowlist** may sign in. Each entry
  is one specific email address; there are no domain-wide or
  group-membership rules.
- **Verified identity:**
  - Google: the ID token's `email_verified` must be true.
  - Microsoft Entra: the app registration is **single-tenant**, pinned to the
    parish's own tenant ID, and only tokens from that tenant are accepted;
    the address matched is the tenant's `preferred_username` (UPN), not the
    unverified `email` claim. This closes the multi-tenant "nOAuth"
    account-takeover hole.
- **Subject binding:** at first sign-in the (issuer, subject) pair is
  recorded on the allowlist entry. After that it is authoritative: the same
  address arriving with a different subject (a re-created account, or an
  address reassigned to someone else) is refused and audited. An
  administrator resets the binding by removing and re-adding the entry.
- Sessions, cookies and CSRF tokens use Sync-specific names so they cannot
  collide with another ParishKit portal on the same host.

## First-run setup

1. The installer generates a **one-time setup token** and prints the setup
   URL (see [deployment](../deployment/spec.md#installer)).
2. The setup wizard, reached only with that token, walks the administrator
   through: choosing the identity provider and entering its OAuth client;
   naming the first administrator; escrowing the credential master key (see
   [credentials](../credential-store/spec.md#master-key)); and the timezone and
   core settings.
3. Finishing setup invalidates the token. Later changes happen in Settings.

### Host-side recovery

If nobody can sign in (the only administrator's account was re-created, the
OAuth client broke, or the allowlist is wrong), recovery happens on the
host, which is already trusted with the master key. A management command
run inside the container (for example `parishkit-sync recover`) can reset
an allowlist entry's subject binding, add an allowlist entry, or issue a new
one-time setup token that reopens the identity-provider step. Every use is
audited and notified.

The wizard uses stepper Next/Back navigation and plain-language help, per the
UX conventions.

## Settings

Core settings are configured once and shared by every module:

- Identity provider and the administrator allowlist.
- Parish timezone.
- ParishSoft connection: credential slot and the expected organization name.
- Notification channel(s) and the level that triggers a notification.
- Mail sender and provider.
- Updates: on/off, deferral window, maintenance window (see
  [deployment](../deployment/spec.md#updater)).
- Logging retention (see [logging](../logging/spec.md#retention)).
- Maximum concurrent module runs (see [jobs](../jobs/spec.md#concurrency)).

These replace the blocks every legacy YAML file repeated (`common`,
`logging`, `slack`, `parishsoft`, `google`, `email`).

## Pages

- **Dashboard:** module status, last and next run per job, current
  activity with live progress, alerts (broken references, failed runs, low
  disk, update status).
- **Modules:** enable, disable, schedule, configure, Run now, Preview (see
  [modules](../modules/spec.md)).
- **Runs:** history and live view (see [jobs](../jobs/spec.md#visibility)).
- **Logs** and **Audit** (see [logging](../logging/spec.md)).
- **Credentials** (see [credentials](../credential-store/spec.md)).
- **Settings** and **About** (current version, available versions, release
  notes; see [deployment](../deployment/spec.md#updater)).

## Shared code

The sign-in, allowlist and audit pieces may become a Django app shared with
Stewardship if they fit without changing Stewardship's behavior; that
extraction is its own change. Stewardship's allauth setup is prior art, but
Sync never imports `parishkit.stewardship`.

## Rejected approaches

- **Local passwords:** more to secure, and every parish already has Google or
  Microsoft accounts.
- **Multiple gunicorn workers:** each adds tens of MB; one worker is enough
  for a handful of administrators.
