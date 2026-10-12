"""Remembered reminder series: the ``campaign-series-v11`` schema (#469).

A ``schedule_series`` record remembers the repeat rule that produced some of
a campaign's reminders, so an edit can later offer "this reminder only",
"this and following" or "the whole series". It is an authoring record only:
each member is an ordinary ``reminder`` schedule with its own logical
identity and revisions, and nothing that plans, coalesces or sends mail
reads a series. The rule lives in its own section rather than in the
reminders' values, so editing a series never changes an unchanged
reminder's values, and so never replaces its revision or prepared work.

The schema is every rule of ``users-v10`` plus this optional section. A
users-form document holding the section is validated as this schema
(``configuration_schema.schema_for``); once present the section stays, even
empty, as ``content`` does, so a configuration's schema does not flip back
and forth. The users request formats build its candidates (``request_patch``).
"""

from uuid import UUID

from parishkit.stewardship.campaigns.recurrence import canonical_rule, expand
from parishkit.stewardship.schema_primitives import invalid

from . import users_schema

# Named beside the users form, which it is (users_schema.FORM_SCHEMAS).
SCHEMA = users_schema.SERIES_SCHEMA
SECTION = "schedule_series"
FIELDS = frozenset({"campaign_id", "rule", "schedules"})


def _canonical_uuid(value):
    """Whether ``value`` is a UUID in its canonical text form."""
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def validate_series_records(document):
    """Each series names reminders of its campaign that its rule gives.

    A record's values are exactly ``campaign_id`` (a campaign of the
    document), ``rule`` (in its canonical form, ``recurrence.canonical_rule``)
    and ``schedules``: the sorted, distinct IDs of one or more reminders of
    that campaign, each dated on a day the rule gives. A reminder belongs to
    at most one series. Send time and email are each reminder's own, so they
    may differ within a series. Since each series has a reminder and no
    reminder is shared, the campaign's schedule limit bounds the series too.
    """
    sections = document["sections"]
    campaigns = {row["id"] for row in sections.get("campaigns", [])}
    schedules = {row["id"]: row["values"] for row in sections.get("schedules", [])}
    claimed = set()
    for row in sections.get(SECTION, []):
        values = row["values"]
        if set(values) != FIELDS or values["campaign_id"] not in campaigns:
            invalid()
        rule = values["rule"]
        if type(rule) is not dict or canonical_rule(rule) != rule:
            invalid()
        members = values["schedules"]
        if (
            type(members) is not list
            or not members
            or not all(_canonical_uuid(member) for member in members)
            or members != sorted(set(members))
            or claimed & set(members)
        ):
            invalid()
        claimed.update(members)
        dates = set(expand(rule)["dates"])
        for member in members:
            schedule = schedules.get(member)
            if (
                schedule is None
                or schedule["kind"] != "reminder"
                or schedule["campaign_id"] != values["campaign_id"]
                or schedule["date"] not in dates
            ):
                invalid()


def validate_series_change(before, after):
    """A remembered series never moves to another campaign."""
    old = {
        row["id"]: row["values"]["campaign_id"]
        for row in before["sections"].get(SECTION, [])
    }
    for row in after["sections"].get(SECTION, []):
        campaign = row["values"]["campaign_id"]
        if old.get(row["id"], campaign) != campaign:
            invalid()


def validate_sections(document):
    """Validate every ``users-v10`` rule without the series, then the series."""
    users_schema.validate_sections(
        document
        | {
            "sections": {
                name: rows
                for name, rows in document["sections"].items()
                if name != SECTION
            }
        }
    )
    validate_series_records(document)


def upkeep(document, patch):
    """The series operations that keep ``document``'s series valid after ``patch``.

    This is what an ordinary schedule edit means for a series ("this
    reminder only"): a removed reminder leaves its series, and so does one
    whose new date its series' rule does not give; a series left with no
    reminder is removed. A changed time or email keeps the reminder in its
    series. A series the patch already names is the patch's own business.
    Returns ``schedule_series`` operations to append to the patch, in the
    document's order of series; none when nothing changes.
    """
    named = {item["id"] for item in patch if item["section"] == SECTION}
    removed, dates = set(), {}
    for item in patch:
        if item["section"] != "schedules":
            continue
        if item["operation"] == "remove":
            removed.add(item["id"])
        elif "date" in item["values"]:
            dates[item["id"]] = item["values"]["date"]
    current = {
        row["id"]: row["values"]["date"]
        for row in document["sections"].get("schedules", [])
    }
    result = []
    for row in document["sections"].get(SECTION, []):
        if row["id"] in named:
            continue
        values = row["values"]
        given = set(expand(values["rule"])["dates"])
        kept = [
            member
            for member in values["schedules"]
            if member not in removed and dates.get(member, current[member]) in given
        ]
        if kept == values["schedules"]:
            continue
        if kept:
            result.append(
                {
                    "operation": "update",
                    "section": SECTION,
                    "id": row["id"],
                    "values": {"schedules": kept},
                }
            )
        else:
            result.append({"operation": "remove", "section": SECTION, "id": row["id"]})
    return result
