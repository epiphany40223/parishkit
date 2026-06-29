# pk-create-ps-ministry-rosters

Build ministry roster tables from ParishSoft and write them into Google Sheets,
so leaders always have an up-to-date roster without exporting anything by hand.

## What you need first

- A working ParishSoft API key.
- Google Cloud and Google Workspace set up for ParishKit, including a service
  account authorized for the Google Sheets scope. See **Google Cloud and Google
  Workspace** in the [top-level README](../../README.md) for the one-time setup.
- A delegated Workspace user with **Editor** access to each target spreadsheet.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-create-ps-ministry-rosters.yaml
```

Roster mappings live under `rosters.ministries` and `rosters.workgroups`. Each
entry writes one roster table to a Google Sheet; the source names must match
your ParishSoft ministry and workgroup names exactly. A ministry entry can also
define `role_sheets` to write extra, filtered rosters for specific roles (for
example, just the chairs). Set `common.timezone` to control the timezone shown
in the "Last updated" timestamp and the spreadsheet title; it defaults to
`America/Kentucky/Louisville`. The comments in `example-config.yaml` explain
every field.

## Run it

Start with a dry run to confirm the tool loads your config and targets the
spreadsheets you expect, then run it live after reviewing the spreadsheet IDs:

```sh
pk-create-ps-ministry-rosters --config /opt/parishkit/config/pk-create-ps-ministry-rosters.yaml --dry-run
```

Keep ParishSoft and Google credentials outside git.

## Verify your Google credentials

Automated tests mock Google Sheets writes, so they do not prove your real
credentials work. Confirm them once with a read-only Sheets smoke test:

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

A Sheets `403` error on a write means the delegated Workspace user does not have
edit access to that spreadsheet; share the spreadsheet (or its shared drive)
with that user as an editor.
