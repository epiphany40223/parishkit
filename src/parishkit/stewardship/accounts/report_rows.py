"""The converted Daily and Weekly reports, as the digest editors show them (#1786).

Until the Report emails page's editor ships (report-emails spec, delivery
plan items 5 and 6), Dates and mail schedules, its date-change review and the
schedule commands keep editing the two converted reports as they edited the
digests: their send time, the Weekly's weekday, the Daily's Compared with past
campaigns block and their skipped sends. They do so through this adapter, so
the editors themselves are unchanged:

- ``digest_view`` reads one report record as the digest-shaped values the
  editors know (kind, time, weekday, comparison, subject, skip dates), and
  ``view_rows`` does so for a list of records, keeping each report's own
  values beside its view (``row["report"]``) for what only it can answer,
  such as its next send;
- ``translate`` turns an editor's change rows back into report values:
  every change to a report record is the report's own values with only
  those fields changed, so nothing an editor cannot see is lost.

A report not shaped as one of the converted two (none exists before the
Report emails page can make one) is not shown to these editors.
"""

from copy import deepcopy

DAILY, WEEKLY = "daily_digest", "weekly_digest"
COMPARED = "compared_with_past_campaigns"


def digest_kind(values):
    """The digest a report is shaped as (``daily_digest``/``weekly_digest``), or None.

    The converted Daily repeats every day keyed by the day it reports on;
    the converted Weekly repeats on one weekday.
    """
    if not isinstance(values, dict) or values.get("kind") != "report":
        return None
    schedule = values.get("schedule") or {}
    if schedule.get("type") != "repeat":
        return None
    if (
        schedule.get("frequency") == "daily"
        and values.get("slot_basis") == "report_date"
    ):
        return DAILY
    if schedule.get("frequency") == "weekly" and len(schedule.get("weekdays", ())) == 1:
        return WEEKLY
    return None


def digest_view(values):
    """One report's values as the digest editors show them, or None.

    It has no email template: a report's subject and introduction are its
    own values, which these editors do not change.
    """
    kind = digest_kind(values)
    if kind is None:
        return None
    schedule = values["schedule"]
    view = {
        "campaign_id": values["campaign_id"],
        "kind": kind,
        "date": None,
        "time": schedule["time"],
        "weekday": schedule["weekdays"][0] if kind == WEEKLY else None,
        "subject": values["subject"] or values["name"],
        "template_version": None,
    }
    if kind == DAILY and any(entry["block"] == COMPARED for entry in values["blocks"]):
        view["comparison"] = True
    if values["skip_dates"]:
        view["skip_dates"] = list(values["skip_dates"])
    return view


def planned(values):
    """The values whose sends a digest editor plans: a report's for its view.

    A converted report's digest view (``digest_view``) has no email
    template, so it cannot be planned as a digest; its sends are those of
    the conversion table's values for its time, weekday and comparison
    (``reports_conversion.converted_values``), skip dates included. Other
    values are returned as they are.
    """
    if values.get("kind") not in (DAILY, WEEKLY) or values.get("template_version"):
        return values
    from .reports_conversion import converted_values

    return converted_values(values, None)


def view_rows(rows):
    """Records for the digest editors: each converted report as its digest view.

    A report keeps its own values under ``"report"``; a report shaped as
    neither digest is left out; every other record is returned as it is.
    """
    result = []
    for row in rows:
        values = row["values"]
        if values.get("kind") != "report":
            result.append(row)
            continue
        view = digest_view(values)
        if view is not None:
            result.append({"id": row["id"], "values": view, "report": values})
    return result


def report_values(original, view):
    """The report's values with the editable digest fields of ``view`` applied.

    ``view`` is a complete digest row, as the editors post one: an absent
    ``comparison`` means the Daily's comparison block is off.
    """
    values = deepcopy(original)
    kind = digest_kind(original)
    values["schedule"]["time"] = view["time"]
    if kind == WEEKLY:
        values["schedule"]["weekdays"] = [view["weekday"]]
    if kind == DAILY:
        blocks = [entry for entry in values["blocks"] if entry["block"] != COMPARED]
        if view.get("comparison"):
            # Compared with past campaigns is placed after the button, last.
            blocks.append({"block": COMPARED, "version": 1, "options": {}})
        values["blocks"] = blocks
    values["skip_dates"] = list(view.get("skip_dates", ()))
    return values


def translate(document, patch):
    """An editor's change rows, with each change to a report in report values.

    A row that updates a report record names digest fields (a full digest
    row, or only its skip dates); it becomes an update with the report's
    complete schedule, blocks and skip dates. Removals and every other row
    are unchanged. Returns a new list.
    """
    reports = {
        row["id"]: row["values"]
        for row in document["sections"].get("schedules", [])
        if row["values"].get("kind") == "report"
    }
    result = []
    for item in patch:
        original = reports.get(item.get("id"))
        if (
            item.get("section") != "schedules"
            or original is None
            or item.get("operation") != "update"
            or item["values"].get("kind", original["kind"]) == "report"
        ):
            result.append(item)
            continue
        # A complete row (it names its kind) replaces the view; a partial
        # one, such as a skip's, changes only what it names.
        view = (
            item["values"]
            if "kind" in item["values"]
            else digest_view(original) | item["values"]
        )
        values = report_values(original, view)
        result.append(
            item
            | {
                "values": {
                    name: values[name]
                    for name in ("schedule", "blocks", "skip_dates")
                    if values[name] != original[name]
                }
                or {"skip_dates": values["skip_dates"]}
            }
        )
    return result
