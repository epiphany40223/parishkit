"""Strict campaign/schedule configuration v3, independent of runtime lifecycle.

References are opaque version identities, not uploaded content or provider IDs
validated for existence. Readiness later resolves those references against the
source/content services. Drafts may omit financial periods and schedules while
being edited; a supplied financial period must nevertheless be internally valid.
"""

from datetime import date, datetime, time

from parishkit.stewardship.accounts.configuration_schema import (
    _invalid,
    _text,
    _timezone_names,
    _typed,
)

from .domain import EnabledModules
from .intervals import campaign_interval, financial_period_end, resolve_local

SCHEMA = "campaign-foundation-v3"
SECTIONS = frozenset({"campaigns", "schedules"})


def _date(value):
    """Require canonical date-only ISO serialization, including no basic ISO form."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed == date.max:
            _invalid()
        return parsed
    except (TypeError, ValueError):
        _invalid()


def _duids(values):
    """Require deterministic sets of positive source IDs without bool/int aliasing."""
    if (
        type(values) is not list
        or any(type(item) is not int or not 1 <= item <= 2**63 - 1 for item in values)
        or values != sorted(set(values))
    ):
        _invalid()


def campaign_values(values):
    """Validate the full draft structure and return its canonical UTC interval."""
    if set(values) != {
        "name",
        "year_label",
        "timezone",
        "start_date",
        "end_date",
        "modules",
        "ministry_duids",
        "financial",
        "share_options",
        "content_versions",
        "additional_information",
    }:
        _invalid()
    _text(values["name"])
    if values["year_label"] is not None:
        _text(values["year_label"], 64)
    _text(values["timezone"])
    if values["timezone"] not in _timezone_names():
        _invalid()
    try:
        modules = EnabledModules.from_list(values["modules"]).to_list()
        if modules != values["modules"]:
            _invalid()
        interval = campaign_interval(
            _date(values["start_date"]), _date(values["end_date"]), values["timezone"]
        )
    except ValueError:
        _invalid()
    _duids(values["ministry_duids"])
    if "ministry" not in modules and values["ministry_duids"]:
        _invalid()
    if type(values["additional_information"]) is not bool:
        _invalid()
    financial = values["financial"]
    if financial is not None:
        if (
            "financial" not in modules
            or type(financial) is not dict
            or set(financial)
            != {
                "start",
                "end",
                "comparison_start",
                "comparison_end",
                "fund_duids",
                "comparison_fund_duids",
                "overlap_confirmed",
            }
        ):
            _invalid()
        for prefix in ("", "comparison_"):
            start, end = (
                _date(financial[prefix + "start"]),
                _date(financial[prefix + "end"]),
            )
            try:
                if financial_period_end(start) != end:
                    _invalid()
            except (ValueError, OverflowError):
                _invalid()
            _duids(financial[prefix + "fund_duids"])
        if type(financial["overlap_confirmed"]) is not bool:
            _invalid()
        if (
            financial["start"] <= values["end_date"]
            and financial["end"] >= values["start_date"]
            and not financial["overlap_confirmed"]
        ):
            _invalid()
    options = values["share_options"]
    if type(options) is not list or len(options) > 100:
        _invalid()
    seen = set()
    for option in options:
        if type(option) is not dict or set(option) != {"id", "label", "free_text"}:
            _invalid()
        _typed(option["id"], "uuid")
        _text(option["label"], 1024)
        if option["id"] in seen or type(option["free_text"]) is not bool:
            _invalid()
        seen.add(option["id"])
    if "financial" not in modules and options:
        _invalid()
    content = values["content_versions"]
    if type(content) is not dict or not set(content) <= {
        "welcome",
        "census",
        "ministry",
        "financial",
        "additional",
        "review",
        "thank_you",
    }:
        _invalid()
    for reference in content.values():
        _typed(reference, "uuid")
    return interval


def schedule_values(values, campaign):
    """Validate one schedule in its campaign zone; return one-time UTC due or None."""
    if set(values) != {
        "campaign_id",
        "kind",
        "date",
        "time",
        "weekday",
        "subject",
        "template_version",
    }:
        _invalid()
    _typed(values["campaign_id"], "uuid")
    _typed(values["template_version"], "uuid")
    _text(values["subject"], 254)
    kind = values["kind"]
    if type(kind) is not str or kind not in {
        "initial",
        "reminder",
        "daily_digest",
        "weekly_digest",
    }:
        _invalid()
    try:
        local_time = time.fromisoformat(values["time"])
        if (
            local_time.tzinfo
            or local_time.isoformat(timespec="seconds") != values["time"]
        ):
            _invalid()
    except (TypeError, ValueError):
        _invalid()
    if kind == "weekly_digest":
        if type(values["weekday"]) is not int or not 0 <= values["weekday"] <= 6:
            _invalid()
    elif values["weekday"] is not None:
        _invalid()
    if kind in {"initial", "reminder"}:
        due = resolve_local(
            datetime.combine(_date(values["date"]), local_time), campaign["timezone"]
        )
        if not campaign_values(campaign).contains(due):
            _invalid()
        return due
    if values["date"] is not None:
        _invalid()
    return None


def validate_campaign_sections(document):
    """Validate cross-record ownership, distinct names, and chronological Family
    mail.
    """
    sections = document["sections"]
    campaigns = {row["id"]: row["values"] for row in sections.get("campaigns", [])}
    names = set()
    for values in campaigns.values():
        campaign_values(values)
        name = values["name"].casefold()
        if name in names:
            _invalid()
        names.add(name)
    mail = {}
    digests = set()
    for row in sections.get("schedules", []):
        values = row["values"]
        identifier = values.get("campaign_id")
        if type(identifier) is not str or identifier not in campaigns:
            _invalid()
        due = schedule_values(values, campaigns[identifier])
        if due is not None:
            mail.setdefault(identifier, []).append((due, values["kind"]))
        else:
            key = (identifier, values["kind"])
            if key in digests:
                _invalid()
            digests.add(key)
    for schedules in mail.values():
        initial = [due for due, kind in schedules if kind == "initial"]
        if (
            len(initial) != 1
            or any(due <= initial[0] for due, kind in schedules if kind == "reminder")
            or len({due for due, _ in schedules}) != len(schedules)
        ):
            _invalid()


def validate_campaign_change(before, after):
    """Never delete campaign identity or move/retype an existing logical schedule."""
    old, new = before["sections"], after["sections"]
    prior = {row["id"] for row in old.get("campaigns", [])}
    if not prior <= {row["id"] for row in new.get("campaigns", [])}:
        _invalid()
    definitions = {row["id"]: row["values"] for row in old.get("schedules", [])}
    for row in new.get("schedules", []):
        previous = definitions.get(row["id"])
        if previous is not None and any(
            row["values"][field] != previous[field] for field in ("campaign_id", "kind")
        ):
            _invalid()
