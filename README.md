# ParishKit

![ParishKit logo](parishkit-logo.png)

ParishKit is an installable Python package and collection of command-line
tools for Catholic parish operations automation.

The project name is ParishKit. The repository and Python distribution name are
`parishkit`.

This repository is intentionally parish-neutral. Parish names, domains,
ministry mappings, external object IDs, credentials, and deployment paths belong
in runtime configuration, not in reusable package code.

## Goals and Non-Goals

ParishKit provides reusable automation for parishes that use systems such as
ParishSoft, Google Workspace, Constant Contact, Slack, and email providers.
Reusable code should be safe to share publicly and adaptable through command
line options and YAML configuration.

ParishKit does not ship parish-specific defaults, ministry mappings,
notification addresses, Google object IDs, API keys, OAuth tokens, or local
deployment policy. Those values belong in runtime config files and credential
stores managed by each parish.

## Requirements

- Python 3.12 or newer.
- Runtime credentials supplied outside git when external services are used.

## Local Development

Create a virtual environment, then install the package and development tools:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the same validation commands represented in CI:

```sh
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Normal validation uses mocked tests for external systems and does not require
real service credentials.

During development, run tools from the checkout when you need local edits under
`src/` to take effect without reinstalling console scripts:

```sh
PYTHONPATH=src python scripts/pk-cron-runner/pk-cron-runner.py --version
PYTHONPATH=src python scripts/pk-query-ps-memfam/pk-query-ps-memfam.py --help
```

The same pattern works for every `scripts/<tool-name>/<tool-name>.py` wrapper.
If you want to use the installed `pk-*` console scripts instead, reinstall the
editable package after changes to `pyproject.toml` entry points or dependency
groups:

```sh
python -m pip install -r requirements.txt
```

## Console Tools

The package exposes these planned commands:

- `pk-cron-runner`: cron-friendly scheduled job runner.
- `pk-query-ps-memfam`: ParishSoft member/family lookup utility.
- `pk-print-ps-ministries`: ParishSoft ministry listing utility.
- `pk-validate-gcalendar-reservations`: Google Calendar reservation auditor.
- `pk-create-ps-ministry-rosters`: ParishSoft-to-Google-Sheets roster
  generator.
- `pk-sync-ps-to-ggroup`: ParishSoft-to-Google-Group synchronizer.
- `pk-sync-ps-to-cc`: ParishSoft-to-Constant-Contact synchronizer.

The commands are installed through `pyproject.toml` entry points. Command
behavior belongs in `src/parishkit` package modules and should be reachable
through these entry points. During early migration, some commands may exist as
placeholders until their script phase is implemented.

Script wrapper directories live under `scripts/<tool-name>/`. Each completed
tool should have its own README and `example-config.yaml`. Wrapper scripts are
for operational convenience only; keep them thin and delegate real behavior to
the package entry point.

Generated reports should be written under the configured ParishKit reports
directory or another explicit output directory documented by the tool. Do not
write parishioner data exports into the source checkout.

## Default Runtime Paths

Deployments use `/opt/parishkit` as the fallback runtime root:

```text
/opt/parishkit/config/
/opt/parishkit/credentials/
/opt/parishkit/cache/
/opt/parishkit/logs/
/opt/parishkit/reports/
/opt/parishkit/run/
```

Set `PARISHKIT_ROOT` before invoking ParishKit commands to move all default
runtime paths under another root. For example, `PARISHKIT_ROOT=/srv/parishkit`
changes the default config directory to `/srv/parishkit/config`, the default
credential directory to `/srv/parishkit/credentials`, and so on.

Examples:

- Runner config: `/opt/parishkit/config/runner.yaml`
- ParishSoft API key: `/opt/parishkit/credentials/parishsoft-api-key.txt`
- Google service account key:
  `/opt/parishkit/credentials/google-service-account.json`
- Google OAuth client secret:
  `/opt/parishkit/credentials/google-oauth-client-secret.json`
- Google OAuth user token:
  `/opt/parishkit/credentials/google-oauth-user-token.json`
- Constant Contact tokens: `/opt/parishkit/credentials/`
- Logs: `/opt/parishkit/logs/`
- Generated reports: `/opt/parishkit/reports/`
- Lock files and process state: `/opt/parishkit/run/`

Log files written through `logging.log_file`, `logging.log_dir`, `--log-file`,
or `--log-dir` are JSON Lines: one JSON object per log record. Console output,
stderr error messages, and Slack notifications remain human-readable text.
When a log record has structured context, the JSONL record includes an
optional `extra` field containing that context as valid JSON while the
`message` field remains human-readable text.

All runtime paths must be overridable through CLI options or YAML config.
Explicit paths in YAML are used as written; `PARISHKIT_ROOT` only changes
ParishKit's built-in defaults.

Use `common.timezone` in YAML config for user-visible local timestamps. It
defaults to `America/Kentucky/Louisville`.

Commands that can modify external systems fail closed unless write intent is
explicit. Set `common.dry_run: true` for a dry run, set
`common.dry_run: false` for live writes, or pass `--dry-run` / `--no-dry-run`
on the command line.

## Linux and macOS Install

Use the top-level `install.py` script from a ParishKit checkout to create the
runtime tree, install the Python package into a virtual environment under that
tree, and expose ParishKit commands under `bin/`. The same instructions apply
on Linux and macOS.

```sh
./install.py
```

The install root is selected in this order:

1. `--installdir`
2. `PARISHKIT_ROOT`
3. `/opt/parishkit`

For example:

```sh
./install.py --installdir "$HOME/parishkit"
PARISHKIT_ROOT=/srv/parishkit ./install.py
```

The installer runs as the current user; it does not create users or groups.
Use `sudo ./install.py` only when the selected install root requires elevated
filesystem permissions.

The installer creates runtime directories with restrictive permissions:
`credentials/` is `0700`; `config/`, `cache/`, `logs/`, `reports/`, `run/`,
and the install root are `0750`; `bin/` is `0755`.

Credential files should normally be `0600` when only the service user needs
them, or `0640` when a dedicated administrative group needs read access. Do not
make credential directories or files world-readable. Exclude
`/opt/parishkit/credentials/` from routine backups unless the backup system is
approved for secrets.

## Secrets and Configuration

Do not commit secrets, credentials, parish-private IDs, local runtime config,
logs, caches, generated reports, or token files.

Secrets should be supplied through explicit runtime mechanisms such as:

- File paths passed on the command line.
- File paths in local YAML config.
- Environment-variable references when a tool documents that behavior.
- Deployment-specific secret managers outside this repository.

Committed examples must use fake or generic data only. Real YAML config files
should live outside git, typically under `/opt/parishkit/config/`.

## ParishSoft Setup

ParishSoft access requires an API key. Parishes generally need to contact
ParishSoft sales or support to purchase, enable, or obtain API access for their
organization.

Store the API key outside git, for example:

```text
/opt/parishkit/credentials/parishsoft-api-key.txt
```

Tools should receive the key path through `--ps-api-key-file` or YAML config.
Expected organization names and validation settings must be configurable; they
must not be hard-coded in reusable code.

## Google Cloud and Workspace Setup

Use a separate Google Cloud project per parish. This keeps IAM, API enablement,
quota, billing, audit logs, and offboarding scoped to the parish running the
software.

Do this setup once per parish deployment.

Create the Google Cloud project and enable APIs:

1. Create a Google Cloud project for the parish.
2. Record the project ID in the parish's private deployment notes. Do not put
   parish-specific project IDs in this repository.
3. Enable only the APIs required by the tools being deployed:
   - Google Calendar API for calendar reservation validation.
   - Google Sheets API for roster generation.
   - Admin SDK API for Google Group membership synchronization.
   - Gmail API is not used by the current SMTP/XOAUTH2 email provider. Enable
     Gmail API only for a future Gmail API provider.

Create the service account:

1. In Google Cloud Console, select the ParishKit project.
2. Go to **IAM & Admin > Service Accounts**.
3. Click **Create service account**.
4. Enter:
   - **Service account name:** `parishkit-automation`
   - **Service account ID:** accept the generated value or use
     `parishkit-automation`
   - **Description:** `Service account for ParishKit automation`
5. Click **Create and continue**.
6. Do not grant broad project roles. ParishKit's Google Workspace automation
   relies on domain-wide delegation and explicit Workspace API scopes instead
   of broad Google Cloud IAM roles.
7. Click **Done**.
8. Open the new service account.
9. Open **Advanced settings**.
10. Enable **Domain-wide delegation** and copy the service account's OAuth 2
    client ID.
11. Open the **Keys** tab.
12. Click **Add key > Create new key**.
13. Select **JSON** and click **Create**.
14. Move the downloaded JSON key into the ParishKit credentials directory:

   ```sh
   mkdir -p /opt/parishkit/credentials
   mv ~/Downloads/*.json /opt/parishkit/credentials/google-service-account.json
   ```

15. Set restrictive permissions on the key file:

   ```sh
   chmod 600 /opt/parishkit/credentials/google-service-account.json
   ```

16. Delete local browser downloads or temporary copies of the JSON key after
    it is stored under `/opt/parishkit/credentials/`.

Authorize domain-wide delegation in Google Workspace Admin:

1. In Google Workspace Admin Console, go to **Security > Access and data
   control > API controls**.
2. Open **Manage Domain Wide Delegation**.
3. Click **Add new**.
4. Paste the service account OAuth 2 client ID.
5. Add the exact comma-separated scopes for the deployed ParishKit tools from
   the matrix below.
6. Click **Authorize**.
7. Record the approved scopes and delegated subject users in private deployment
   notes.

Domain-wide delegation is required for these ParishKit service-account
deployments:

- `pk-validate-gcalendar-reservations`: uses `google.delegated_subject` to act
  as the Workspace user that can inspect and respond to resource-calendar
  invitations.
- `pk-create-ps-ministry-rosters`: uses `google.delegated_subject` to write
  roster data to Google Sheets as a Workspace user.
- `pk-sync-ps-to-ggroup`: uses `google.delegated_subject` to update Google
  Group membership through Admin SDK.
- Google Workspace email notifications configured with
  `email.provider: google-workspace`: uses `email.delegated_user` to send mail
  as a Workspace account. The example notification configs for
  `pk-sync-ps-to-ggroup` and `pk-sync-ps-to-cc` use this provider.

Reference the key file and delegated user from local YAML config or CLI
options. Do not hard-code either value in reusable package code. For example:

```yaml
google:
  service_account_file: /opt/parishkit/credentials/google-service-account.json
  delegated_subject: admin@example.org

email:
  provider: google-workspace
  service_account_file: /opt/parishkit/credentials/google-service-account.json
  delegated_user: no-reply@example.org
```

For Google Sheets targets, including spreadsheets stored in shared drives, the
effective Google identity must have write access to the spreadsheet. A Sheets
API 403 from `values.clear` or `values.update` means this effective identity
cannot write the spreadsheet range.

Find the effective Google identity for a tool:

1. Open the YAML config passed to the tool with `--config`.
2. Find the top-level `google:` section.
3. If `google.delegated_subject` is set, that email address is the effective
   identity. With service-account domain-wide delegation, Google treats API
   calls as coming from this Workspace user.
4. If `google.delegated_subject` is not set, open the JSON file named by
   `google.service_account_file` and use its `client_email` value as the
   effective identity.
5. Keep this email address in private deployment notes together with the target
   spreadsheet or shared drive IDs it can access.

For workflows that use `google.delegated_subject`, consider creating a friendly
Google Group such as `automation-bots@example.org` or `ParishKit automation
bots` in Google Workspace and adding the delegated Workspace user to it. Add
the delegated Workspace user, not the service-account email address, because
Google Drive and Calendar access checks happen as the impersonated Workspace
user. Then share Drive folders, shared drives, spreadsheets, and resource
calendars with the friendly group name instead of repeatedly sharing with a
long automation account address. Keep the group membership small and document
which delegated users it contains.

Grant that effective identity access:

1. For a single spreadsheet, open the spreadsheet in Google Sheets, click
   **Share**, add the effective identity email address, choose **Editor**, and
   save.
2. For a spreadsheet in a shared drive, open Google Drive, select the shared
   drive, click **Manage members**, add the effective identity email address,
   and choose an edit-capable role such as **Contributor**, **Content manager**,
   or **Manager**.
3. If only one spreadsheet in a shared drive should be writable, share just
   that spreadsheet with **Editor** instead of adding the identity to the whole
   shared drive.
4. Re-run the tool with `--dry-run` first to confirm it loads the target
   config, then run the write-capable command after reviewing the spreadsheet
   IDs in the config.

Run the relevant smoke-test script with the same config and credentials the
scheduled tool will use. Prefer read-only smoke tests first, then run any
write-capable test only after reviewing the target object IDs. Keep the
project, enabled APIs, service account client ID, approved scopes, key file
path, delegated users, and smoke-test result in private deployment notes.

For workflows that require user OAuth instead of service-account delegation,
configure an OAuth consent screen and OAuth client in the same project, then
generate user token files through a documented manual smoke-test or auth
helper. Store OAuth client secrets and user tokens under the credentials
directory with the same restrictive permissions.

Never commit Google service account JSON keys, OAuth client secrets, refresh
tokens, user token files, or domain-specific object IDs that should remain
private.

Starter Workspace Admin scope matrix:

| Tool or integration | API | Delegation | Initial scopes | Access |
| --- | --- | --- | --- | --- |
| `pk-validate-gcalendar-reservations` | Calendar API | Service account with domain-wide delegation | `https://www.googleapis.com/auth/calendar` | Write-capable calendar scope because the tool can patch this account's attendee responses |
| `pk-create-ps-ministry-rosters` | Sheets API | Service account with domain-wide delegation | `https://www.googleapis.com/auth/spreadsheets` | Write-capable for configured spreadsheet ranges |
| `pk-sync-ps-to-ggroup` | Admin SDK Directory API, Groups Settings API | Service account with domain-wide delegation | `https://www.googleapis.com/auth/admin.directory.group.member`, `https://www.googleapis.com/auth/apps.groups.settings` | Write-capable for group membership and group settings reads |
| Google Workspace email provider with SMTP XOAUTH2 | Gmail SMTP | Service account with domain-wide delegation | `https://mail.google.com/` | Restricted, high-risk write-capable mail scope; used by google-workspace email notifications |
| Future Google Workspace email provider with Gmail API | Gmail API | Service account with domain-wide delegation | `https://www.googleapis.com/auth/gmail.send` | Sensitive, Workspace Admin high-risk, write-capable send-only scope |
| User OAuth workflows | Tool-specific Google APIs | User consent instead of domain-wide delegation | Use the narrow scopes documented by the tool README before enabling the workflow | Tool-specific |

These scopes are starting points for planned tools. Before production use, check
the tool README and example config for the final scope list. Prefer read-only
scopes when a deployment only audits data. Treat restricted Google scopes and
Workspace Admin high-risk scopes, including Gmail send scopes, as requiring
extra administrative review before authorization.

## CI and Credentials

Normal CI runs Ruff linting, Ruff format checking, and Pytest. Pull request CI
also enforces DCO validation. CI must not require real ParishSoft, Google,
Constant Contact, Slack, or email provider credentials.

External-service behavior should be covered by mocked tests in CI. When real
credentials are needed, add a documented human-run smoke-test tool that:

- Reads credentials from explicit runtime paths or environment references.
- Prefers read-only checks.
- Requires dry-run mode or explicit confirmation before writes.
- Redacts sensitive values from output.
- Is excluded from normal CI.

## Release Automation

This repository uses semantic versioning. Releases are annotated git tags named
`vVERSION`, such as `v1.2.3`.

Use the release prep tool to inspect commits since the latest annotated release
tag, choose the next version, and generate release notes:

```sh
tools/prepare-release.py
```

For a normal release-prep pass:

```sh
tools/prepare-release.py \
  --write-version \
  --write-notes RELEASE_NOTES.md \
  --build-artifacts
```

The tool infers semver impact from commit messages. `feat:` commits select a
minor bump, `fix:` and `perf:` commits select a patch bump, and commits with
`!` or `BREAKING CHANGE:` select a major bump. If post-release commits do not
declare a semver impact, release prep exits with instructions; rerun with
`--bump` or `--version` when the automated choice needs human correction.

Creating a local annotated tag is explicit:

```sh
git add pyproject.toml RELEASE_NOTES.md
git commit -s -F release-commit-message.txt
tools/prepare-release.py --tag --notes-file RELEASE_NOTES.md
```

The tag command requires a clean worktree and verifies that the version in
`HEAD:pyproject.toml` matches the tag being created. It never pushes tags. A
human must explicitly authorize and run any `git push origin vVERSION`.

When a `v*` tag is pushed, the release GitHub Actions workflow requires an
annotated tag whose target is reachable from `origin/main`, validates the tag
against `pyproject.toml`, runs the normal checks, builds source and wheel
distributions, uses the committed `RELEASE_NOTES.md` when present, creates or
updates the GitHub Release, and uploads the built artifacts.

## Manual Smoke Tests

Credential-required smoke tests should live with the tool or integration they
validate. They should be safe for a human operator to run after credentials are
installed locally.

Smoke-test conventions:

- Accept credential paths through CLI options or local YAML config.
- Print what will be checked before making a request.
- Prefer read-only API calls.
- Require `--dry-run` or an explicit confirmation option before writes.
- Redact tokens, API keys, email addresses when appropriate, and private IDs
  from output.
- Return clear exit codes and actionable error messages.
- Stay out of normal CI.

Slack notification smoke test:

```sh
scripts/smoke-tests/slack-notification.py \
  --slack-token-file /opt/parishkit/credentials/slack-token.txt \
  --slack-channel '#bot-alerts'
```

## REST API Modernization

Migration work may modernize REST API usage when current supported APIs or
client-library patterns are better than the old script patterns. Keep
compatibility risks visible in code, tests, tool READMEs, or migration notes.
Use mocked tests for normal validation and documented manual smoke tests for
credential-required behavior.
