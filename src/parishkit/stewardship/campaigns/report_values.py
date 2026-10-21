"""Validation of a report email's stored values (report-emails spec, #1786).

A report email is one ``report`` schedule record; its values are described
in the spec's "Report values" and "Stored form". ``report_values`` checks one
record's values and returns them unchanged; ``validate_reports`` checks the
rules that span a campaign's reports (distinct names, at most 20).
``repeat_bounds`` turns a repeat rule's campaign-relative bounds into send
dates, which the schedule evaluator (``schedule_evaluation.ReportPlan``)
and the database's slot check (``stewardship_report_slot_due_v1``) share.

Nothing calls these for a stored configuration yet: the conversion (spec,
delivery plan item 4) admits ``report`` records into the campaign
configuration's validation together with its new schema name.
"""

from datetime import UTC, date, datetime, time, timedelta

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.schema_primitives import invalid, text, typed
from parishkit.stewardship.web.content import (
    prepare_content,
    validate_admin_digest_content,
)

from .configuration import skip_dates

KEYS = frozenset(
    {
        "campaign_id",
        "kind",
        "name",
        "subject",
        "intro_html",
        "intro_text",
        "recipients",
        "schedule",
        "as_of",
        "blocks",
        "content_required",
        "skip_dates",
        "paused_ranges",
        "slot_basis",
    }
)
NAME_LIMIT = 80
REPORT_LIMIT = 20
RECIPIENT_LIMIT = 50
ADDRESS_LIMIT = 25
BLOCK_LIMIT = 6
PAUSE_LIMIT = 100
GROUPS = ("administrators", "staff")
AS_OF = ("previous_day_end", "send_time")
SLOT_BASES = ("send_date", "report_date")
FREQUENCIES = ("daily", "weekly")
FROM_BOUNDS = ("campaign_start", "day_after_campaign_start")
UNTIL_BOUNDS = (
    "campaign_end",
    "day_after_campaign_end",
    "first_send_after_campaign_end",
    "archive",
)


def _date(value):
    """A canonical ISO date other than the last one, else invalid."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        invalid()
    # The first and last dates leave no room for the day before or after.
    if parsed.isoformat() != value or parsed in (date.min, date.max):
        invalid()
    return parsed


def _time(value):
    """A canonical ``HH:MM:SS`` wall-clock time, else invalid."""
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError):
        invalid()
    if parsed.tzinfo or parsed.isoformat(timespec="seconds") != value:
        invalid()
    return parsed


def instant(value):
    """A UTC instant in Python's ISO form (``…+00:00``), else invalid.

    Pause intervals are opened and closed at Apply, by the database's clock,
    so they keep its microseconds; one spelling per instant keeps two equal
    documents equal.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        invalid()
    if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
        invalid()
    return parsed.astimezone(UTC)


def name_key(name):
    """The form two report names are compared in: case-folded, spaces collapsed."""
    return " ".join(name.split()).casefold()


def _recipients(entries):
    """Validate the recipient list: users, the two groups and outside addresses."""
    if type(entries) is not list or not 1 <= len(entries) <= RECIPIENT_LIMIT:
        invalid()
    seen, addresses = set(), 0
    for entry in entries:
        if type(entry) is not dict or len(entry) != 1:
            invalid()
        ((kind, value),) = entry.items()
        if kind == "user":
            typed(value, "uuid")
        elif kind == "group":
            if value not in GROUPS:
                invalid()
        elif kind == "address":
            # One valid address, stored as the login policy compares them.
            addresses += 1
            typed(value, "email")
            if normalized_email(value) != value:
                invalid()
        else:
            invalid()
        if (kind, value) in seen:
            invalid()
        seen.add((kind, value))
    if addresses > ADDRESS_LIMIT:
        invalid()


def _schedule(schedule):
    """Validate a one-time or repeating schedule (spec, "Stored form")."""
    if type(schedule) is not dict:
        invalid()
    if schedule.get("type") == "once":
        if set(schedule) != {"type", "date", "time"}:
            invalid()
        _date(schedule["date"])
    elif schedule.get("type") == "repeat":
        frequency = schedule.get("frequency")
        keys = {"type", "frequency", "from", "until", "time"}
        if frequency == "weekly":
            keys.add("weekdays")
            weekdays = schedule.get("weekdays")
            if (
                type(weekdays) is not list
                or not weekdays
                or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
                or weekdays != sorted(set(weekdays))
            ):
                invalid()
        elif frequency not in FREQUENCIES:
            invalid()
        if set(schedule) != keys:
            invalid()
        first, last = schedule["from"], schedule["until"]
        chosen_first = None if first in FROM_BOUNDS else _date(first)
        chosen_last = None if last in UNTIL_BOUNDS else _date(last)
        if chosen_first and chosen_last and chosen_last < chosen_first:
            invalid()
    else:
        invalid()
    _time(schedule["time"])


def _blocks(entries):
    """Validate the ordered block list against the block registry.

    Each block at most once (decision 10), at its registered version, with
    only the options it declares, each checked by the block itself; a block
    placed after the button is last.
    """
    # The registry draws report content, so it is imported only when a report
    # is validated rather than whenever the campaign configuration loads.
    from parishkit.stewardship.reports.report_blocks import REGISTRY
    from parishkit.stewardship.reports.report_blocks.registry import AFTER_BUTTON

    if type(entries) is not list or not 1 <= len(entries) <= BLOCK_LIMIT:
        invalid()
    keys = []
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"block", "version", "options"}:
            invalid()
        block = REGISTRY.get(entry["block"]) if type(entry["block"]) is str else None
        if (
            block is None
            or type(entry["version"]) is not int
            or entry["version"] != block.version
            or type(entry["options"]) is not dict
            or not set(entry["options"]) <= set(block.options)
        ):
            invalid()
        try:
            block.check(entry["options"])
        except ValueError:
            invalid()
        keys.append(block.key)
    if len(set(keys)) != len(keys) or any(
        REGISTRY[key].placement == AFTER_BUTTON for key in keys[:-1]
    ):
        invalid()


def _pauses(ranges):
    """Validate the pause intervals: ascending, disjoint, only the last open."""
    if type(ranges) is not list or len(ranges) > PAUSE_LIMIT:
        invalid()
    previous = None
    for index, entry in enumerate(ranges):
        if type(entry) is not dict or set(entry) != {"start", "end"}:
            invalid()
        start = instant(entry["start"])
        end = None if entry["end"] is None else instant(entry["end"])
        if (
            (end is not None and end <= start)
            or (end is None and index != len(ranges) - 1)
            or (previous is not None and start < previous)
        ):
            invalid()
        previous = end


def _form(values):
    """Every key present, of a ``report`` record of a campaign."""
    if type(values) is not dict or set(values) != KEYS or values["kind"] != "report":
        invalid()
    typed(values["campaign_id"], "uuid")


def _content(values):
    """The subject and introduction: only the digests' placeholders, no files.

    The introduction is checked as the sanitizer would store it, as the
    digest templates are, so a later sanitizer change cannot invalidate a
    saved report.
    """
    if values["subject"] != "":
        text(values["subject"], 254)
    html, plain = values["intro_html"], values["intro_text"]
    if type(html) is not str or type(plain) is not str:
        invalid()
    try:
        validate_admin_digest_content(
            values["subject"], prepare_content(html, text=plain).html, plain
        )
    except ValueError:
        invalid()


def _choice(value, allowed):
    """Refuse ``value`` unless it is one of ``allowed``."""
    if value not in allowed:
        invalid()


def _flag(value):
    """Refuse anything but ``True`` or ``False``."""
    if type(value) is not bool:
        invalid()


def _sends(values):
    """What the report's own actions keep: slot basis, skips and pauses."""
    _choice(values["slot_basis"], SLOT_BASES)
    skip_dates(values["skip_dates"])
    _pauses(values["paused_ranges"])


# The parts of a report's values, each checked on its own (``refused_parts``
# names the ones a document gets wrong), in the order ``report_values``
# checks them. "form" comes first: the others read keys it guarantees.
PARTS = (
    ("form", _form),
    ("name", lambda values: text(values["name"], NAME_LIMIT)),
    ("content", _content),
    ("recipients", lambda values: _recipients(values["recipients"])),
    ("schedule", lambda values: _schedule(values["schedule"])),
    ("as_of", lambda values: _choice(values["as_of"], AS_OF)),
    ("blocks", lambda values: _blocks(values["blocks"])),
    ("content_required", lambda values: _flag(values["content_required"])),
    ("sends", _sends),
)


def report_values(values):
    """Validate one ``report`` record's values; return them unchanged.

    Every key is present (spec, "Stored form"), and each part (``PARTS``)
    is valid: the subject and introduction accept only the placeholders
    today's digests accept and no hosted files.
    """
    for _part, check in PARTS:
        check(values)
    return values


def refused_parts(values):
    """The names of the parts of ``values`` that ``report_values`` refuses.

    Empty when the values are valid. A document whose form is wrong (a key
    missing or extra) is ``["form"]`` alone, since the other parts cannot
    be read then. ``report-email preview`` names the field and problem of
    each refused part from these (``report_email_editor.value_problems``).
    """
    found = []
    for part, check in PARTS:
        try:
            check(values)
        except (ValueError, TypeError, KeyError):
            # A malformed document can fail a part with a TypeError (a
            # list where a string belongs) as well as a refusal.
            if part == "form":
                return ["form"]
            found.append(part)
    return found


def schedule_fields(values):
    """Validate only what a report's sends depend on; return the values.

    The schedule, slot basis, skip dates and pause intervals. The schedule
    evaluator checks just these, so a later change to the block registry or
    the content rules (checked in full on save) can never stop a stored
    report's sends from being planned.
    """
    if type(values) is not dict or values.get("kind") != "report":
        invalid()
    try:
        _schedule(values["schedule"])
        if values["slot_basis"] not in SLOT_BASES:
            invalid()
        skip_dates(values["skip_dates"])
        _pauses(values["paused_ranges"])
    except KeyError:
        invalid()
    return values


def validate_reports(records):
    """Check the rules that span one campaign's reports: names and the limit.

    ``records`` are the values of every ``report`` record of the campaign
    that is not removed.
    """
    records = list(records)
    if len(records) > REPORT_LIMIT:
        invalid()
    names = [name_key(values["name"]) for values in records]
    if len(set(names)) != len(names):
        invalid()


def repeat_bounds(schedule, start, end):
    """A repeat rule's first and last send dates; the last is None until archive.

    ``start`` and ``end`` are the campaign's first and last days. The bounds
    name send dates whatever the report's slot basis. "The first send after
    the campaign's last day" is the next day for a daily rule and the next
    chosen weekday, one to seven days later, for a weekly one.
    """
    first = {
        "campaign_start": start,
        "day_after_campaign_start": start + timedelta(days=1),
    }.get(schedule["from"])
    first = first if first is not None else date.fromisoformat(schedule["from"])
    until = schedule["until"]
    if until == "archive":
        return first, None
    if until == "campaign_end":
        return first, end
    if until == "day_after_campaign_end":
        return first, end + timedelta(days=1)
    if until == "first_send_after_campaign_end":
        if schedule["frequency"] == "daily":
            return first, end + timedelta(days=1)
        distance = min((day - end.weekday()) % 7 or 7 for day in schedule["weekdays"])
        return first, end + timedelta(days=distance)
    return first, date.fromisoformat(until)
