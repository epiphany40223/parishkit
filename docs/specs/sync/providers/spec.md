# ParishKit Sync — Providers

Provider interfaces let Sync use different back-ends for the same job.
Overview: [Sync spec](../spec.md).

Each provider is an interface in shared code (`src/parishkit/`, outside
`sync/` and `stewardship/`) with one module per back-end, following the
existing `parishkit.email` `EmailProvider` pattern. Back-ends are chosen in
core settings and use credential slots (see
[credentials](../credential-store/spec.md#slots-and-binding)).

| Interface | Back-ends now | Possible later |
|---|---|---|
| [Identity](#identity) | Google, Microsoft Entra | others via OIDC |
| [FileUpload](#fileupload) | Google Drive, including Shared Drives | OneDrive, SharePoint |
| [Mail](#mail) | Google Workspace, Microsoft 365 | others |
| [Notify](#notify) | Slack | others |

## Identity

OpenID Connect sign-in with per-provider presets; see
[core](../core/spec.md#sign-in).

## FileUpload

Create, update and look up files in a drive, including Google Shared Drives.
The Shared Drive handling is extracted from Stewardship's
`backup_drive.py` without changing Stewardship's behavior. Used by the
[ministry rosters](../ministry-rosters/spec.md) module.

## Mail

- **One core send-email service.** Every module that sends email (change
  notifications, the unsubscribed report, and any future module) calls the
  core service through `ctx.mail` (see [jobs](../jobs/spec.md#module-api)).
  The core routes each message to whichever Mail back-end the administrator
  configured: Google Workspace, Microsoft 365, or a future back-end. Modules
  never import or call a provider directly, so a new back-end needs no
  module changes.
- **Back-ends** build on the shared `parishkit.email` providers. The
  Microsoft 365 back-end is a placeholder today (its `send` raises
  `ConfigError`); it is implemented in
  [#2218](https://github.com/epiphany40223/parishkit/issues/2218) with
  Microsoft Graph `sendMail` and app-only credentials from the credential
  store, tested with fake HTTP in normal CI plus a human-run smoke test. A
  back-end is **not offered** in setup or settings until it is implemented
  and tested.
- **No mail from dry runs:** a Preview run sends no notifications; it shows
  what it would have sent. The shared providers have no Testing-mode
  recipient redirect (that lives in Stewardship-specific code), so Sync
  does not promise one. The mail settings page's "send a test message"
  sends only to the signed-in administrator.

## Notify

Operational alerts (failed runs, broken references, credential refresh
failures, low disk, update outcomes) at a configured minimum level. The Slack
code that lives in `parishkit.logging` and `parishkit.cli` today moves behind
this interface.

## Extraction rules

- Each extraction is its own small change.
- Anything Stewardship uses keeps identical behavior and gets
  Stewardship-side review.
- Tracking: [#2218](https://github.com/epiphany40223/parishkit/issues/2218).
