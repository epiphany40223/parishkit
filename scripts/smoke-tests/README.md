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

A key that reaches several ParishSoft organizations needs
`--organization-id N` to say which one to check; the script prints how many
organizations the key reaches. With such a key, add `--organization-fields`
to count, on one page of Members and Member contact rows, how many rows'
`organizationID`, `ownerOrganizationID` and `registeredOrganizationID` equal
that organization (counts only, no record values). That confirms which field
ParishKit should keep Members on
([#1554](https://github.com/epiphany40223/parishkit/issues/1554)).

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

## Google People API email addresses (#2352)

`google-people-emails.py` shows what the Google People API returns for **your own** Google account's email addresses, to find out whether Stewardship sign-in could match a user by a verified alternate address ([#2352](https://github.com/epiphany40223/parishkit/issues/2352), for [#2343](https://github.com/epiphany40223/parishkit/issues/2343)). It changes nothing in ParishKit or Google. It signs you in with the OAuth 2.0 authorization-code flow and PKCE on a loopback redirect (`http://127.0.0.1:<port>/`), asks for `openid email profile https://www.googleapis.com/auth/user.emails.read` (`people/me` answers 403 without `profile`), and prints the ID token's `email` and `email_verified`, the scopes Google granted, and one row per address: the address, `metadata.verified`, `metadata.primary` and `metadata.source.type` (`-` means Google omitted the field). Nothing is written to disk or logged; at exit it revokes the access token. The module ([`src/parishkit/google/people_emails.py`](../../src/parishkit/google/people_emails.py)) uses only the Python standard library and imports nothing from ParishKit, so the one file can be copied to a host and run with `python3` 3.12 or newer.

### Before you run it (Google Cloud console)

Use a Google Cloud project where you can change the OAuth settings. Do not change Production's sign-in client.

1. **APIs & Services → Library**: find **Google People API** and click **Enable**.
2. **Google Auth Platform → Audience**: note the user type. *Internal* admits only accounts in the project's Workspace domain, and needs no test users or Google verification. *External* admits any Google account: leave the publishing status at **Testing** and add your Google account under **Test users** (Testing avoids Google's app verification for test users).
3. **Google Auth Platform → Data Access → Add or remove scopes**: add `https://www.googleapis.com/auth/user.emails.read` (shown as "See and download all of your Google Account email addresses"), plus `openid`, `.../auth/userinfo.email` and `.../auth/userinfo.profile`, and save.
4. **Google Auth Platform → Clients → Create client**: application type **Desktop app**, any name (for example "People API smoke test"). Download its JSON. A Desktop client accepts `http://127.0.0.1` on any port, so it needs no redirect URI. This is the recommended client: the Production sign-in client stays unchanged.
5. To use a **Web application** client instead (such as an existing sign-in client, whose file on a Stewardship host is `RUNTIME_ROOT/credentials/google_oauth/credential`), Google accepts only redirect URIs registered on it exactly: pick a port, add `http://127.0.0.1:<port>/` under its authorized redirect URIs, and pass `--port <port>`. Avoid this for Production's client.

Keep the downloaded JSON outside every git checkout, readable only by you (`chmod 600`), and delete it, and the Desktop client in Google Cloud if you no longer need it, when you are done.

### Run it

On a laptop with a browser (the tool opens it and reads the redirect itself):

```sh
scripts/smoke-tests/google-people-emails.py --client-file ~/client_secret.json
```

The wrapper needs ParishKit installed in the Python it runs with. Without that, run the module file itself with any Python 3.12 or newer:

```sh
python3 -I src/parishkit/google/people_emails.py --client-file ~/client_secret.json
```

`-I` (isolated mode) keeps the file's own directory, the current directory and `PYTHON*` environment variables off the import path. Without it, the file's sibling `calendar.py` would shadow the standard library's `calendar`; the module drops its own directory from the import path when run as a script, so plain `python3 people_emails.py` also works, but `-I` is the safer habit.

On a headless host over ssh, copy the one file and the client JSON there, and use `--manual`:

```sh
scp -p src/parishkit/google/people_emails.py ~/client_secret.json root@HOST:/root/
ssh -t root@HOST python3 -I /root/people_emails.py --manual \
    --client-file /root/client_secret.json
ssh root@HOST rm /root/people_emails.py /root/client_secret.json
```

`scp -p` keeps the client file's `600` mode on the host; without `-p` the copy gets the host's umask (usually `644`). Alternatively: `ssh root@HOST 'umask 077; cat > /root/client_secret.json' < ~/client_secret.json`.

`--manual` prints a URL. Open it in your laptop's browser and sign in with your Google account. After the consent screen the browser goes to `http://127.0.0.1:<port>/?state=…&code=…` and the page fails to load: that is expected. Copy the whole address from the address bar and paste it at the tool's prompt (pasting only the `code` value also works, but then the `state` check is skipped). The code is single-use and expires within minutes.

Add `--redact` to print each address's domain only (`…@example.org`). Other options: `--no-browser` (print the URL, still listen), `--port N`, `--timeout SECONDS` (default 300). The tool exits `0`, or `2` with one `ERROR:` line.

### Incremental authorization (`--two-step`)

`--two-step` tries the sign-in sequence planned for [#2343](https://github.com/epiphany40223/parishkit/issues/2343): sign in first with identity scopes only, and ask for the email-addresses permission afterwards, on its own. It works with the default loopback redirect and with `--manual` (then you paste two addresses, one per step).

```sh
python3 -I src/parishkit/google/people_emails.py --two-step --client-file ~/client_secret.json
```

1. **Step 1** asks for `openid email profile` only. After you sign in it prints `Step 1: signed in as <address> (account <hash>); granted scopes: …`. The hash is the first 12 hex characters of the SHA-256 of Google's account id (`sub`); the id itself is never printed.
2. **Step 2** asks for `https://www.googleapis.com/auth/user.emails.read` alone, with `include_granted_scopes=true`, your step 1 address as `login_hint` and `prompt=consent`. Google's consent screen shows the permission ("See and download all of your Google Account email addresses") as a checkbox. The tool prints `Step 2: consent from account <hash>; granted scopes: …` and then what ParishKit would do.
3. At exit it revokes both steps' access, newest first: `Step 2 access revoked at Google.` and, because that usually ends the whole grant, `Step 1 access had already ended at Google.` (either line may say `revoked`).

Run it three times, once per case:

| In step 2 you… | The tool prints |
| --- | --- |
| tick the checkbox and continue with the same account | the same account hash as step 1, granted scopes including `user.emails.read`, then the address table |
| leave the checkbox unticked and continue | `Not granted: ParishKit would show the normal no-access page.` (it does not call the People API) |
| choose a **different** Google account on the account chooser (if Google offers one; otherwise use **Use another account**) | a different account hash, then `Refused: a different Google account consented; ParishKit would refuse.` (no People API call) |

Google may show a lone permission without a checkbox, only **Allow** and **Cancel**; if so, the unticked case cannot happen: report that, and use **Cancel** instead. Cancelling step 2 prints `Step 2 cancelled (Google answered access_denied): ParishKit would show the normal no-access page.` For the different-account case, the other account must be allowed by the project (a test user, if the audience is External and in Testing); if Google blocks it, report the message Google shows.

For each case, report the printed `Step 1` and `Step 2` lines (the `Step 2` line says in parentheses whether the account id came from the ID token or from tokeninfo), the outcome line or table, and what Google's screens looked like in step 2 (whether it showed the account chooser or went straight to consent, and how the checkbox was worded).

### What to report back

Copy into the issue (addresses may be redacted to their domain):

- the printed block: the `ID token email` / `email_verified` lines, the `Granted scopes` line, and the address table;
- for each alternate address, whether it is confirmed in Google Account → Personal info → Email, and whether it is a Gmail "Send mail as" address or a Workspace alias;
- what the consent screen said about the email-address permission, and whether Google showed an "unverified app" warning;
- the project's user type (Internal or External) and publishing status (Testing or In production).

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

# ParishSoft Member contact read: probe one test Member's record; with
# --send, write one marker field, read it back and restore the original.
# Note the test Member's values first; see the smoke tools guide.
docker compose ... exec -T worker pk-stewardship smoke \
  --config /opt/parishkit/config/services/worker.yaml \
  --target parishsoft_contact --organization-id 12345 \
  --member-id 67890 --confirm-member-id 67890

# Google login: validate the OAuth client document and print the redirect
# URI to register; then sign in at the public origin yourself.
docker compose ... exec -T web pk-stewardship smoke \
  --config /opt/parishkit/config/services/web.yaml --target google_oauth
```

Each command prints one JSON line (`credential` is `valid`, `invalid` or
`unavailable`; `sent` says whether a message went out) and exits `0`, or one
generic line and exit `2`. Omit `--send-to` or `--send` to check without
sending. Nothing is printed about the credential itself.
