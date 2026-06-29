# pk-validate-gcalendar-reservations

Review pending invitations on Google Calendar resource calendars (such as parish
rooms) and accept or decline them automatically based on rules you set —
including declining double-bookings.

## What you need first

- Google Cloud and Google Workspace set up for ParishKit, including a service
  account authorized for the Google Calendar scope. See **Google Cloud and
  Google Workspace** in the [top-level README](../../README.md) for the one-time
  setup.
- A delegated Workspace user with access to the resource calendars and
  permission to respond to their invitations.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-validate-gcalendar-reservations.yaml
```

Under `calendars`, list each calendar to monitor and the creator email domains
whose reservations are acceptable. A pending invitation from a domain *not* in
`acceptable_domains` is declined. A pending invitation from an accepted domain is
accepted unless it conflicts with an existing event on a calendar that has
`check_conflicts` enabled. Set `common.timezone` for the default local timezone;
a `calendars.timezone` value overrides it for this tool. The comments in
`example-config.yaml` explain every field.

## Run it

Start with a dry run, which computes the accept/decline decisions and reports
them without changing any invitation responses:

```sh
pk-validate-gcalendar-reservations --config /opt/parishkit/config/pk-validate-gcalendar-reservations.yaml --dry-run
```

When the dry run looks right, set `common.dry_run: false` (or pass
`--no-dry-run`) to apply the responses for real.

Store Google credential files outside git, such as under
`/opt/parishkit/credentials/`.

## Verify your Google credentials

Automated tests mock Google Calendar, so they do not prove your real credentials
work. Confirm them once with a read-only smoke test:

```sh
scripts/smoke-tests/google-api.py \
  --service calendar \
  --version v3 \
  --scope https://www.googleapis.com/auth/calendar.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org \
  --calendar-id room@example.org \
  --send
```
