# Manual Smoke Tests

Smoke tests in this directory are for human operators with real credentials.
They are not part of normal CI.

Run them only after reviewing what they will send or read. Prefer read-only
checks, use dry-run options where available, and keep token files outside git.

Slack smoke tests require the Slack optional dependency group:

```sh
python -m pip install '.[slack]'
```

The Slack notification smoke test previews the target and message by default.
Add `--send` only after confirming the preview is safe to post.

Google smoke tests require the Google optional dependency group:

```sh
python -m pip install '.[google]'
```

Use `google-api.py` for a read-only service-account or user-OAuth check. Use
`google-workspace-email.py` to preview or send a Google Workspace SMTP/XOAUTH2
message with a service account delegated to a mailbox.

When running `google-api.py` with `--send`, provide a read-only target for the
selected service:

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

Create a user OAuth token for workflows that cannot use service accounts with:

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

ParishSoft and Constant Contact smoke tests are read-only by default:

```sh
scripts/smoke-tests/parishsoft-connectivity.py \
  --api-key-file /opt/parishkit/credentials/parishsoft-api-key.txt \
  --expected-organization "Example Parish"

scripts/smoke-tests/constant-contact-lists.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json
```

Add `--send --deep` to the ParishSoft smoke test to run representative
read-only probes for family, member, contact, workgroup, ministry, and offering
endpoints. Add `--send --deep` to the Constant Contact smoke test to read
lists, contacts, and custom fields without mutating data.

The Constant Contact token bootstrap and refresh process is documented in
`scripts/pk-sync-ps-to-cc/README.md`.
