# pk-create-ps-ministry-rosters

Create ministry rosters from ParishSoft data and write them to Google Sheets.

This wrapper delegates to the installed `pk-create-ps-ministry-rosters`
command.

## Usage

```sh
pk-create-ps-ministry-rosters --config example-config.yaml --dry-run
```

Roster mappings live in YAML under `rosters.ministries` and
`rosters.workgroups`. Each target writes a table to Google
Sheets. Ministry targets can also define `role_sheets` that write a filtered
roster for specific ParishSoft ministry roles.

Set `common.timezone` to the IANA timezone used for the visible "Last updated"
timestamp and the Google spreadsheet title. It defaults to
`America/Kentucky/Louisville`.

Keep ParishSoft and Google credentials outside git.

## Google Credential Smoke Test

Automated tests mock Google Sheets writes. To verify real credentials, run a
read-only Sheets smoke test manually:

```sh
scripts/smoke-tests/google-api.py \
  --service sheets \
  --version v4 \
  --scope https://www.googleapis.com/auth/spreadsheets.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org \
  --spreadsheet-id example-spreadsheet-id \
  --send
```
