"""Repeating reminder rules as campaign-local calendar dates (#469).

A repeat rule is only an authoring aid: each date it produces becomes an
ordinary reminder schedule, so planning, coalescing and delivery never read
it. ``expand`` is the server's copy of the page script's
``window.ParishRecurrence.expand``; the shared table
``tests/stewardship/fixtures/recurrence_cases.json`` pins both, so keep the
two in step. The command line's ``repeat`` change entry
(``admin_changes``) expands its rule here.

A rule is a mapping:

- ``frequency``: ``"daily"``, ``"weekly"`` or ``"monthly"``;
- ``start`` and ``end``: inclusive ISO dates, at most a year apart;
- ``weekdays`` (weekly only): days of the week, Monday 0 to Sunday 6;
- ``monthly`` (monthly only): ``{"day": 1–31}``, or
  ``{"ordinal": 1–4 or -1 (last), "weekday": 0–6}``, never both.

The dates are civil days in the campaign's time zone; the send time is a
wall-clock time added per date, so a daylight-saving change never moves it.
A month without the chosen day (the 31st in September) is skipped and named
in ``missing``, never moved to another day.
"""

import calendar
from datetime import date, timedelta

# The longest range one rule may cover, in days (end minus start).
SPAN_DAYS = 366
FREQUENCIES = ("daily", "weekly", "monthly")
ORDINALS = (1, 2, 3, 4, -1)


def _day(value):
    """A canonical ISO date, or None for anything else."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.isoformat() == value else None


def _index(value, low, high):
    """Whether ``value`` is an int (not a bool) from ``low`` to ``high``."""
    return type(value) is int and low <= value <= high


def expand(rule):
    """``{"dates": [...], "missing": ["YYYY-MM", ...]}`` or ``{"error": code}``.

    The error codes, in the order checked, are ``dates`` (a missing or
    impossible date), ``order``, ``span``, ``weekdays``, ``monthly`` and
    ``frequency``; the page script maps each to its message.
    """
    rule = rule if isinstance(rule, dict) else {}
    start, end = _day(rule.get("start")), _day(rule.get("end"))
    if start is None or end is None:
        return {"error": "dates"}
    if end < start:
        return {"error": "order"}
    if (end - start).days >= SPAN_DAYS:
        return {"error": "span"}
    frequency = rule.get("frequency")
    weekdays = rule.get("weekdays")
    monthly = rule.get("monthly")
    monthly = monthly if isinstance(monthly, dict) else {}
    # Each member is checked before it is used, and only for the frequency
    # that uses it, so no shape of input can raise (a list or object among
    # the weekdays is refused, not hashed).
    if frequency == "weekly" and not (
        isinstance(weekdays, list)
        and weekdays
        and all(_index(value, 0, 6) for value in weekdays)
    ):
        return {"error": "weekdays"}
    by_day = _index(monthly.get("day"), 1, 31)
    by_weekday = (
        type(monthly.get("ordinal")) is int
        and monthly["ordinal"] in ORDINALS
        and _index(monthly.get("weekday"), 0, 6)
    )
    # A day of the month and a weekday of the month together are ambiguous,
    # so both expanders refuse them rather than each preferring one.
    mixed = "day" in monthly and ("ordinal" in monthly or "weekday" in monthly)
    if frequency == "monthly" and (mixed or not (by_day or by_weekday)):
        return {"error": "monthly"}
    if frequency not in FREQUENCIES:
        return {"error": "frequency"}
    chosen = set(weekdays) if frequency == "weekly" else set()
    dates, missing = [], []
    day = start
    while day <= end:
        last = calendar.monthrange(day.year, day.month)[1]
        if frequency == "daily":
            dates.append(day)
        elif frequency == "weekly":
            if day.weekday() in chosen:
                dates.append(day)
        elif by_day:
            if day.day == monthly["day"]:
                dates.append(day)
            # The month's last day in range stands for a missing chosen day.
            elif day.day == last and monthly["day"] > last:
                missing.append(day.isoformat()[:7])
        elif day.weekday() == monthly["weekday"] and (
            day.day + 7 > last
            if monthly["ordinal"] == -1
            else (day.day + 6) // 7 == monthly["ordinal"]
        ):
            dates.append(day)
        day += timedelta(days=1)
    return {"dates": [value.isoformat() for value in dates], "missing": missing}


def canonical_rule(rule):
    """The stored form of a valid rule, or None.

    Only the keys its frequency uses are kept, weekdays are sorted and
    distinct, and every value has its exact type, so two rules that mean the
    same thing compare equal.
    """
    if not isinstance(rule, dict) or "error" in expand(rule):
        return None
    # expand has checked every member this keeps.
    frequency = rule["frequency"]
    result = {"frequency": frequency, "start": rule["start"], "end": rule["end"]}
    if frequency == "weekly":
        result["weekdays"] = sorted(set(rule["weekdays"]))
    elif frequency == "monthly":
        monthly = rule["monthly"]
        result["monthly"] = (
            {"day": monthly["day"]}
            if "day" in monthly
            else {"ordinal": monthly["ordinal"], "weekday": monthly["weekday"]}
        )
    return result
