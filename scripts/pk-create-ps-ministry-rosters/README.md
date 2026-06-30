# pk-create-ps-ministry-rosters

Build ministry roster tables from ParishSoft, generate an XLSX workbook, and
upload it into Google Drive as a native Google Sheet so leaders always have an
up-to-date roster without exporting anything by hand.

## What you need first

- A working ParishSoft API key.
- Google Cloud and Google Workspace set up for ParishKit, including a service
  account authorized for the Google Drive scope. See **Google Cloud and Google
  Workspace** in the [top-level README](../../README.md) for the one-time setup.
- A delegated Workspace user with **Editor** access to each target spreadsheet.
  This tool uses the broad Drive scope because it updates pre-existing
  spreadsheet file IDs in unattended runs; the narrower `drive.file` scope is
  not enough for that model.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-create-ps-ministry-rosters.yaml
```

Roster mappings live under `rosters.ministries` and `rosters.workgroups`. Each
entry replaces one configured Google Drive spreadsheet file with a newly
generated workbook; the source names must match your ParishSoft ministry and
workgroup names exactly. A ministry entry can also define `role_sheets` to write
extra, filtered rosters for specific roles (for example, just the chairs). Set
`common.timezone` to control the timezone shown in the "Last updated" timestamp
and the timestamped Drive file name; it defaults to
`America/Kentucky/Louisville`. The comments in `example-config.yaml` explain
every field.

## Run it

Start with a dry run to confirm the tool loads your config and targets the Drive
file IDs you expect, then run it live after reviewing those IDs:

```sh
pk-create-ps-ministry-rosters --config /opt/parishkit/config/pk-create-ps-ministry-rosters.yaml --dry-run
```

Keep ParishSoft and Google credentials outside git.

## Verify your Google credentials

Automated tests mock Google Drive uploads, so they do not prove your real
credentials work. Confirm them once with a Drive smoke test that uses the same
scope as the tool but reads only metadata:

```sh
scripts/smoke-tests/google-api.py \
  --service drive \
  --version v3 \
  --scope https://www.googleapis.com/auth/drive \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org \
  --drive-file-id example-spreadsheet-id \
  --send
```

A Drive `403` error on upload means the delegated Workspace user does not have
edit access to that spreadsheet file; share the file (or its shared drive) with
that user as an editor.
