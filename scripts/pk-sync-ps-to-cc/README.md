# pk-sync-ps-to-cc

Synchronize ParishSoft contacts to Constant Contact lists.

This wrapper delegates to the installed `pk-sync-ps-to-cc` command.

## Usage

```sh
pk-sync-ps-to-cc --config example-config.yaml --dry-run
```

Mappings live in YAML under `sync.lists`. The command resolves desired
Constant Contact list membership from ParishSoft member workgroups, filters
contacts that have unsubscribed in Constant Contact, computes an action list,
and writes through the shared Constant Contact client unless `--dry-run` or
`common.dry_run` is enabled.

Keep ParishSoft, Constant Contact, and email credentials outside git.

## Constant Contact Authorization

The sync needs a Constant Contact client metadata file and an OAuth token file.
The YAML config names these as `constant_contact.client_id_file` and
`constant_contact.access_token_file`.

The client metadata file is a local secret JSON file. It must include the app
client ID plus Constant Contact API, authorization, and token endpoints:

```json
{
  "client id": "constant-contact-app-client-id",
  "endpoints": {
    "api": "https://api.cc.email",
    "auth": "constant-contact-device-authorization-endpoint",
    "token": "constant-contact-token-endpoint"
  }
}
```

Create or refresh the token file from the repository root with the manual
device OAuth helper:

```sh
PYTHONPATH=src scripts/smoke-tests/constant-contact-device-oauth.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json \
  --send
```

The helper prints a Constant Contact authorization URL, waits for you to finish
authorization in a browser, and saves the resulting token JSON to
`--access-token-file`.

Validate the token with the read-only list smoke test:

```sh
PYTHONPATH=src scripts/smoke-tests/constant-contact-lists.py \
  --client-id-file /opt/parishkit/credentials/constant-contact-client.json \
  --access-token-file /opt/parishkit/credentials/constant-contact-token.json \
  --send
```

The smoke test refreshes expired tokens when the token file contains a refresh
token and the client file contains `endpoints.token` plus the app client ID. If
refresh fails or no token file exists, rerun the manual device OAuth helper.

## Credential Smoke Test

Automated tests mock Constant Contact writes. After creating and validating the
token file, run `pk-sync-ps-to-cc` with `--dry-run` first. Dry-run still reads
Constant Contact and ParishSoft but does not write contacts or send
notifications.
