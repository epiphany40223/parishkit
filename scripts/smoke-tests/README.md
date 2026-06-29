# Smoke tests

Smoke tests are small scripts you run by hand to confirm that a set of
credentials actually works, *before* you wire it into a scheduled job. They are
for a human operator with real credentials installed locally — they are not part
of normal automated testing.

Run them only after reviewing what they will read or send. They prefer read-only
checks, offer dry-run/preview options, and expect token files to live outside
git. Most preview what they will do and require `--send` before making any
network call.

## Install the optional dependencies

Each integration's client libraries are optional dependency groups:

```sh
python -m pip install '.[slack]'    # Slack smoke test
python -m pip install '.[google]'   # Google smoke tests
```

## ParishSoft (read-only)

```sh
scripts/smoke-tests/parishsoft-connectivity.py \
  --api-key-file /opt/parishkit/credentials/parishsoft-api-key.txt \
  --expected-organization "Example Parish"
```

Add `--send --deep` to run representative read-only probes across the family,
member, contact, workgroup, ministry, and offering endpoints.

## Google (read-only)

Use `google-api.py` for a read-only service-account or user-OAuth check, and
`google-workspace-email.py` to preview or send a Google Workspace SMTP/XOAUTH2
message. When running `google-api.py` with `--send`, give it a read-only target
for the selected service:

```sh
# Calendar
scripts/smoke-tests/google-api.py --service calendar --version v3 \
  --scope https://www.googleapis.com/auth/calendar.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --calendar-id parish-calendar@example.org --send

# Drive
scripts/smoke-tests/google-api.py --service drive --version v3 \
  --scope https://www.googleapis.com/auth/drive.metadata.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --drive-file-id example-file-id --send

# Sheets
scripts/smoke-tests/google-api.py --service sheets --version v4 \
  --scope https://www.googleapis.com/auth/spreadsheets.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --spreadsheet-id example-sheet-id --sheet-range A1:A1 --send

# Google Workspace Admin SDK group membership
scripts/smoke-tests/google-api.py --service admin --version directory_v1 \
  --scope https://www.googleapis.com/auth/admin.directory.group.member.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org --group-key group@example.org --send
```

Create a user OAuth token for the rare workflow that cannot use a service
account:

```sh
scripts/smoke-tests/google-api.py \
  --client-secrets-file /opt/parishkit/credentials/google-oauth-client.json \
  --user-token-file /opt/parishkit/credentials/google-user-token.json \
  --scope https://www.googleapis.com/auth/calendar.readonly \
  --service calendar \
  --version v3 \
  --bootstrap-user-token \
  --send
```

## Constant Contact (read-only)

```sh
scripts/smoke-tests/constant-contact-lists.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json
```

Add `--send --deep` to read lists, contacts, and custom fields without mutating
data. The one-time `constant-contact-device-oauth.py` token bootstrap and the
automatic refresh behavior are documented in
[`scripts/pk-sync-ps-to-cc/README.md`](../pk-sync-ps-to-cc/README.md).

## Slack

```sh
scripts/smoke-tests/slack-notification.py \
  --slack-token-file /opt/parishkit/credentials/slack-token.txt \
  --slack-channel '#bot-alerts'
```

The Slack notification smoke test previews the target and message by default. Add
`--send` only after confirming the preview is safe to post.
