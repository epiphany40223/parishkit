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

# Drive scope used by pk-create-ps-ministry-rosters. The smoke test still reads
# only file metadata, but it verifies that the delegated client can use the
# write-capable scope authorized for roster uploads.
scripts/smoke-tests/google-api.py --service drive --version v3 \
  --scope https://www.googleapis.com/auth/drive \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org \
  --drive-file-id example-spreadsheet-file-id --send

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

## Stewardship (deployed credentials)

The Stewardship application keeps each provider credential in the deployed
consumer that uses it, so its smoke check runs inside that container with the
same Compose file and project name the deployment uses, and reads only the
credential already mounted there. The
[smoke tools guide](../../docs/guides/stewardship-smoke-tools.md) explains
what each check proves; the
[deployment runbook](../../docs/guides/stewardship-deployment-runbook.md#validation-in-testing-mode)
says when to run them.

```sh
# ParishSoft: the key sees exactly the configured organization (read-only).
docker compose ... exec -T worker pk-stewardship smoke \
  --config /opt/parishkit/config/services/worker.yaml \
  --target parishsoft --organization-id 12345

# Google Workspace mailbox: authenticate, then send one fixed message.
docker compose ... exec -T mail-dispatch pk-stewardship smoke \
  --config /opt/parishkit/config/services/mail-dispatch.yaml \
  --target google_workspace --delegated-email stewardship@parish.example \
  --send-to operator@parish.example

# Slack (worker recreated from compose-slack.json): authenticate the bot
# token, then post one fixed message.
docker compose ... exec -T worker pk-stewardship smoke \
  --config /opt/parishkit/config/services/worker-slack.yaml \
  --target slack --channel-id C0123456789 --send

# Google login: validate the OAuth client document and print the redirect
# URI to register; then sign in at the public origin yourself.
docker compose ... exec -T web pk-stewardship smoke \
  --config /opt/parishkit/config/services/web.yaml --target google_oauth
```

Each command prints one JSON line (`credential` is `valid`, `invalid` or
`unavailable`; `sent` says whether a message went out) and exits `0`, or one
generic line and exit `2`. Omit `--send-to` or `--send` to check without
sending. Nothing is printed about the credential itself.
