# pk-sync-ps-to-cc

Keep Constant Contact email lists in step with ParishSoft. The tool works out
who should be on each list from your ParishSoft workgroups, respects anyone who
has unsubscribed in Constant Contact, and adds or removes contacts to match.

## What you need first

- A working ParishSoft API key.
- A Constant Contact developer application and a saved OAuth token. See
  **Constant Contact** in the [top-level README](../../README.md) for the
  one-time setup, then the [authorization steps below](#constant-contact-authorization).
- Optionally, an email provider configured for the change-summary notifications.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-sync-ps-to-cc.yaml
```

List mappings live under `sync.lists`: each entry maps one ParishSoft workgroup
to one Constant Contact list. The tool resolves the desired list membership from
the workgroup, filters out contacts that have unsubscribed in Constant Contact,
computes the additions and removals, and applies them unless dry-run is in
effect. The comments in `example-config.yaml` explain every field, including the
`allow_empty` / `max_removals` / `max_removal_fraction` guardrails and the
optional `unsubscribed_report` that summarizes still-subscribed-in-ParishSoft
but unsubscribed-in-Constant-Contact members on a schedule.

## Run it

Always start with a dry run. It still reads ParishSoft and Constant Contact and
reports the changes it *would* make, but writes no contacts and sends no email:

```sh
pk-sync-ps-to-cc --config /opt/parishkit/config/pk-sync-ps-to-cc.yaml --dry-run
```

When the dry run looks right, set `common.dry_run: false` (or pass
`--no-dry-run`) to apply changes for real.

Keep ParishSoft, Constant Contact, and email credentials outside git.

## Constant Contact authorization

The sync needs two secret files, both referenced from your config:
`constant_contact.client_id_file` (your app's client metadata) and
`constant_contact.access_token_file` (the OAuth token).

The client metadata file holds your Constant Contact app client ID plus the API,
authorization, and token endpoints:

```json
{
  "client id": "constant-contact-app-client-id",
  "endpoints": {
    "api": "https://api.cc.email",
    "auth": "https://authz.constantcontact.com/oauth2/default/v1/device/authorize",
    "token": "https://authz.constantcontact.com/oauth2/default/v1/token"
  }
}
```

Create or refresh the token file with the manual device OAuth helper. It prints
an authorization URL, waits for you to approve access in a browser, then saves
the token:

```sh
PYTHONPATH=src scripts/smoke-tests/constant-contact-device-oauth.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json \
  --send
```

Validate the token with the read-only list smoke test:

```sh
PYTHONPATH=src scripts/smoke-tests/constant-contact-lists.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json \
  --send
```

The token includes a refresh token, so scheduled runs renew themselves
automatically. The smoke test also refreshes an expired token when the token
file has a refresh token and the client file has `endpoints.token` plus the
client ID. If a refresh fails or the token file is missing, rerun the device
OAuth helper above.
