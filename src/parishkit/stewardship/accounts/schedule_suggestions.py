"""Dates and mail schedules' suggested starting schedules (#1404 PR 6).

While the campaign wizard applies and the campaign has no schedule saved,
Dates and mail schedules proposes a starting set (admin-portal spec, "Create
the campaign", the steps page by page): the Initial invitation on the start
date at 9:00 AM and one Reminder seven days before the end date at 9:00 AM,
in the campaign's time zone. No Weekly or Daily report is suggested: report
emails replaced the digests, and a digest schedule can no longer be added
(#1786). The page lists them as the table lists saved schedules, marked "Not
saved yet" (``rows``), and its **Add these schedules and continue** saves
them directly (``schedule_entry_views.schedule_suggested``), which works the
set out again here (``entries``) rather than trusting posted values.

Everything here is a pure function of the campaign's applied values and its
saved emails, so the page and the save cannot propose different sets and
the rules are unit-testable without a database.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .schedule_table import schedule_rows

# Family mail sends at 9:00 AM.
SEND_TIME = "09:00"
# The Reminder is sent this many days before the campaign's end date.
REMINDER_LEAD = timedelta(days=7)


def _first_email(records, kind):
    """The first saved email of mail type ``kind``, or None.

    ``records`` are the campaign's content records in the applied
    document's order (``content_views._records``), the order Create the
    campaign added the default text in.
    """
    return next(
        (
            row
            for row in records
            if row["values"]["kind"] == "email" and row["values"]["slot"] == kind
        ),
        None,
    )


def entries(campaign, records, now):
    """The suggested schedules, as New scheduled email's entries, or [].

    ``campaign`` is the campaign's applied values, ``records`` its
    content records and ``now`` the current instant. Each entry names its
    mail type, campaign-local send date (or weekday) and time, and the
    email it sends. A mail type with no saved email is left out; without an
    Initial invitation email nothing is suggested, since a Reminder needs
    one. The Reminder is also left out when its day would not fall after the
    start date (a campaign of eight days or fewer), where it would break
    "every reminder after the initial invitation". Nothing is suggested
    once the Initial invitation's send time has passed: direct save has no
    review dialog, so a past time would send at once, unannounced, and the
    Reminder needs an Initial invitation before it (it is later still, so is
    never the only one passed).
    """
    initial = _first_email(records, "initial")
    if initial is None:
        return []
    start = date.fromisoformat(campaign["start_date"])
    zone = ZoneInfo(campaign["timezone"])
    if datetime.combine(start, time.fromisoformat(SEND_TIME), zone) <= now:
        return []
    remind = date.fromisoformat(campaign["end_date"]) - REMINDER_LEAD
    result = [
        {
            "kind": "initial",
            "date": start.isoformat(),
            "time": SEND_TIME,
            "template_version": initial["id"],
        }
    ]
    reminder = _first_email(records, "reminder")
    if reminder is not None and remind > start:
        result.append(
            {
                "kind": "reminder",
                "date": remind.isoformat(),
                "time": SEND_TIME,
                "template_version": reminder["id"],
            }
        )
    return result


def rows(campaign, records, campaign_id, now, names):
    """The suggested schedules as the scheduled emails table's rows.

    Each is described exactly as a saved schedule would be
    (``schedule_table.schedule_rows``), with its send time resolved in the
    campaign's zone for the page to show in the browser's, and marked
    "Not saved yet" (status ``suggested``). ``names`` maps an email's id
    to its readable name, as on the table.
    """
    subjects = {row["id"]: row["values"].get("subject", "") for row in records}
    suggested = [
        {
            "id": f"suggested-{entry['kind']}",
            "values": {
                "campaign_id": str(campaign_id),
                "kind": entry["kind"],
                "date": entry.get("date"),
                "time": f"{entry['time']}:00",
                "weekday": entry.get("weekday"),
                "template_version": entry["template_version"],
                "subject": subjects[entry["template_version"]],
            },
        }
        for entry in entries(campaign, records, now)
    ]
    return [
        replace(row, status="suggested")
        for row in schedule_rows(suggested, campaign, {}, now, names)
    ]
