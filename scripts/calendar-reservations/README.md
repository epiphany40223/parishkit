# parishkit-calendar-reservations

Check Google Calendar reservations for configured conflicts and domains.

This wrapper delegates to the installed `parishkit-calendar-reservations`
command.

## Usage

```sh
parishkit-calendar-reservations --config example-config.yaml --dry-run
```

The command reads monitored calendars and acceptable creator domains from YAML.
Pending calendar invitations from other domains are declined. Pending invitations
from accepted domains are accepted unless they conflict with an existing event
on a calendar where `check_conflicts` is enabled.

Store Google credential files outside git, such as under
`/opt/parishkit/credentials/`.

## Google Credential Smoke Test

Automated tests mock Google Calendar. To verify real credentials, run a
read-only smoke test manually:

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
