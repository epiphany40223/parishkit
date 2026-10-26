# ParishKit Sync module — Calendar reservations

Audits Google Calendar room and resource reservations. Overview:
[Sync spec](../spec.md).

## Behavior

Checks and reports are unchanged from
[`pk-validate-gcalendar-reservations`](../../pk-validate-gcalendar-reservations/spec.md),
which remains the reference.

- Consumes no ParishSoft data.
- Credential slot: a Google service account with a delegated subject that can
  read the calendars and update attendee responses on them (the full
  Calendar scope the CLI tool uses), because the module accepts and declines
  pending reservations, not just reads them.

## What changes from the CLI tool

- **Config:** calendar records (`CalendarRef`, chosen with a picker that shows
  the current calendar or resource name) with the conflict-check flag; module
  settings for acceptable domains. The timezone comes from core settings.
- **Dry run** becomes Preview.

Parity target: a real parish config with 18 calendars.
