"""The refresh schedule's rules, exceptions and spacing (#632).

The Administrator describes the daily refresh schedule in layers, the way an
iCalendar recurrence set is built (see the background-processing spec,
"Schedule entries and rules"):

1. rules, "Full every N minutes from a start time to a last time";
2. single times, "Full at a time";
3. exceptions ("skips"): times, or from–to ranges (start included, end
   excluded), that remove times.

The result is the **daily times**, which the settings store as
``full_refresh_times`` beside the rules themselves (``refresh_rules``). The
scheduler reads only the list; these functions are what the settings page
and the command line use to derive and check it, and what the configuration
schema uses to check the rules' shape. Everything here is pure: wall times
only, no time zone, no database.

Quick rules ("Quick every ..." and "Quick at ...", quick updates) were
removed (#1147). A stored schedule may still have them, and its shape stays
valid (``valid_shape(stored=True)``), but they produce no time: the page,
the command line and the scheduler see only the full rules.
"""

from dataclasses import dataclass

from parishkit.stewardship.wall_clock import is_hhmm

from .cadence import MAX_DAILY_TIMES, refresh_settings

# The rule intervals the editor offers, in minutes.
STEPS = (15, 30, 60, 120, 180, 240, 360, 480, 720)
# Any two daily times must be at least this many minutes apart.
SPACING = 15
DAY = 24 * 60
# The rule kinds a new or changed schedule may use, and those a stored one
# may still hold (quick rules, removed by #1147).
KINDS = ("full",)
STORED_KINDS = ("full", "quick")
# The closed set of ``refresh_rules`` fields.
FIELDS = frozenset({"rules", "skips", "skip_around_family_emails"})
SINGLE_RULE = frozenset({"kind", "at"})
RANGE_RULE = frozenset({"kind", "every", "from", "to"})
SINGLE_SKIP = frozenset({"at"})
RANGE_SKIP = frozenset({"from", "to"})
# The one cap on rows of each kind for a schedule being authored or changed
# (#936), shared by the settings page's editor and the configuration check
# behind the command line and imports (valid_shape). The editor posts every
# row's fields with the page's other settings, and the whole request must
# stay within the server's DATA_UPLOAD_MAX_NUMBER_FIELDS (500), or it is
# refused before any form reads it: 64 rules and 16 skips post at most
# 64 * 6 + 16 * 4 + 12 = 460 editor names (refresh_schedule_forms
# .posted_names; a test pins the sum). One cap means any schedule saved
# since can be shown and saved back from the page. More rules never matter:
# "every" rules give any number of times. A converted (upgraded) schedule
# always fits: it has at most MAX_LEGACY_FULL_TIMES "Full at" rows and no
# skips.
MAX_RULES = 64
MAX_SKIPS = 16
# The bound before #936: a stored, carried or historical document may hold
# up to this many rows of each kind and still reads, so a version accepted
# then never makes the configuration unloadable (``valid_shape(stored=True)``).
MAX_STORED_ROWS = MAX_DAILY_TIMES


def minutes(value):
    """Minutes after midnight of a canonical ``HH:MM`` time."""
    return int(value[:2]) * 60 + int(value[3:])


def clock(value):
    """The canonical ``HH:MM`` time ``value`` minutes after midnight."""
    return f"{value // 60:02d}:{value % 60:02d}"


def _rule_ok(rule, kinds=KINDS):
    """Whether one rule row has a closed, canonical shape of one of ``kinds``."""
    if type(rule) is not dict or rule.get("kind") not in kinds:
        return False
    if rule.keys() == SINGLE_RULE:
        return is_hhmm(rule["at"])
    return (
        rule.keys() == RANGE_RULE
        and type(rule["every"]) is int
        and rule["every"] in STEPS
        and is_hhmm(rule["from"])
        and is_hhmm(rule["to"])
        # A rule never crosses midnight; one whose last time equals its
        # start gives that one time.
        and rule["to"] >= rule["from"]
    )


def _skip_ok(skip):
    """Whether one skip row has a closed, canonical shape."""
    if type(skip) is not dict:
        return False
    if skip.keys() == SINGLE_SKIP:
        return is_hhmm(skip["at"])
    return (
        skip.keys() == RANGE_SKIP
        and is_hhmm(skip["from"])
        and is_hhmm(skip["to"])
        # The end is excluded, so it must be after the start.
        and skip["to"] > skip["from"]
    )


def valid_shape(value, *, stored=False):
    """Whether ``value`` is a ``refresh_rules`` document of the closed shape.

    ``rules`` (at least one) and ``skips`` are lists of rows of the closed
    forms above; ``skip_around_family_emails`` is a boolean. A schedule
    being authored or changed has at most ``MAX_RULES`` rules and
    ``MAX_SKIPS`` skips, all of them full rules; a ``stored`` one (already
    accepted, read back or carried unchanged) keeps the older
    ``MAX_STORED_ROWS`` bound and may still hold quick rules. Whether the
    rules produce the stored lists is ``check_schedule``'s question.
    """
    most_rules, most_skips = (
        (MAX_STORED_ROWS, MAX_STORED_ROWS) if stored else (MAX_RULES, MAX_SKIPS)
    )
    kinds = STORED_KINDS if stored else KINDS
    return (
        type(value) is dict
        and value.keys() == FIELDS
        and type(value["rules"]) is list
        and 1 <= len(value["rules"]) <= most_rules
        and all(_rule_ok(rule, kinds) for rule in value["rules"])
        and type(value["skips"]) is list
        and len(value["skips"]) <= most_skips
        and all(_skip_ok(skip) for skip in value["skips"])
        and type(value["skip_around_family_emails"]) is bool
    )


@dataclass(frozen=True)
class DailyTimes:
    """The daily times the rules produce.

    ``full`` is a sorted ``HH:MM`` tuple. ``unmatched_skips`` are the
    indexes of skips that matched no time.
    """

    full: tuple
    unmatched_skips: tuple = ()


def _skipped(skip, value):
    """Whether the skip row removes the time ``value`` (minutes)."""
    if "at" in skip:
        return value == minutes(skip["at"])
    return minutes(skip["from"]) <= value < minutes(skip["to"])


def _times(rules, kind):
    """The times (minutes) the rules of ``kind`` give, before any skip."""
    found = set()
    for rule in rules:
        if rule["kind"] != kind:
            continue
        if "at" in rule:
            found.add(minutes(rule["at"]))
        else:
            found.update(
                range(minutes(rule["from"]), minutes(rule["to"]) + 1, rule["every"])
            )
    return found


def daily_times(refresh_rules):
    """Apply the full rules, single times and skips, in order.

    ``refresh_rules`` must have the closed shape (``valid_shape``); anything
    else raises ``ValueError``. Stored schedules are read under their older
    row bound, so the scheduler, preview and page never fail on one. A
    stored quick rule (#1147) produces no time and is otherwise ignored.
    """
    if not valid_shape(refresh_rules, stored=True):
        raise ValueError("Refresh rules require the closed rule shape.")
    full = _times(refresh_rules["rules"], "full")
    # Each skip is matched against the times before any skip, so a skip
    # that overlaps or repeats another is not reported as matching nothing.
    generated = set(full)
    unmatched = []
    for index, skip in enumerate(refresh_rules["skips"]):
        removed = {value for value in generated if _skipped(skip, value)}
        if not removed:
            unmatched.append(index)
        full -= removed
    return DailyTimes(tuple(clock(value) for value in sorted(full)), tuple(unmatched))


def full_rules(refresh_rules):
    """``refresh_rules`` without its stored quick rules (#1147).

    What the settings page and ``integration show`` present, and what a
    save writes back, so a schedule saved again drops its quick rules. A
    skip that matched only quick times goes with them: kept, it would match
    nothing and block the next save ("This matches no refresh"). A skip
    that matched nothing before stays, as it was. A schedule with no quick
    rule is returned as it is.
    """
    rules = refresh_rules["rules"]
    if all(rule["kind"] == "full" for rule in rules):
        return refresh_rules
    full, quick = _times(rules, "full"), _times(rules, "quick")
    return refresh_rules | {
        "rules": [rule for rule in rules if rule["kind"] == "full"],
        "skips": [
            skip
            for skip in refresh_rules["skips"]
            if any(_skipped(skip, value) for value in full)
            or not any(_skipped(skip, value) for value in quick)
        ],
    }


def spacing_conflicts(times):
    """Pairs of daily times less than ``SPACING`` minutes apart, as ``HH:MM``.

    Measured on the wall clock around midnight: a kept 23:50 and 00:00 are
    ten minutes apart. Each pair is (earlier, later) in the order the clock
    meets them, so a pair across midnight is ("23:50", "00:00").
    """
    values = sorted({minutes(value) for value in times})
    pairs = list(zip(values, values[1:], strict=False))
    if len(values) > 1:
        pairs.append((values[-1], values[0] + DAY))
    return tuple((clock(a), clock(b % DAY)) for a, b in pairs if b - a < SPACING)


@dataclass(frozen=True)
class Problem:
    """One reason a schedule cannot be saved, with the times it names.

    ``code`` is one of "no_full", "too_many", "too_close", "off_quarter",
    "unmatched_skip" or "rules_mismatch"; ``times``
    are the ``HH:MM`` times involved, and ``index`` the skip row, if any.
    """

    code: str
    times: tuple = ()
    index: int | None = None


# The settings ``stored_settings`` writes and ``check_schedule`` compares.
_STORED = frozenset(
    {
        "full_refresh",
        "nightly_time",
        "full_refresh_times",
        "delta_refresh",
        "quick_refresh_times",
    }
)


def stored_settings(refresh_rules):
    """The stored schedule settings the rules produce (for the page and CLI).

    ``full_refresh`` is "daily"; ``nightly_time`` is the earliest full time;
    ``delta_refresh`` is "off", as the configuration schema still requires
    of a schedule saved with its rules, with no quick list (quick updates
    were removed, #1147). Raises ``ValueError`` when the rules leave no
    full time.
    """
    result = daily_times(refresh_rules)
    if not result.full:
        raise ValueError("A refresh schedule needs at least one full refresh.")
    settings = {
        "full_refresh": "daily",
        "nightly_time": result.full[0],
        "full_refresh_times": list(result.full),
        "delta_refresh": "off",
        "refresh_rules": refresh_rules,
    }
    return settings


def check_schedule(settings, *, kept=()):
    """Every reason the schedule in ``settings`` cannot be saved; empty if none.

    The one validator the settings form and the command line share (#632).
    ``settings`` are the ParishSoft integration's schedule settings with
    ``refresh_rules`` of the closed shape (``ValueError`` otherwise).
    ``kept`` are full times already stored off the quarter hour, which an
    Administrator may keep but not add. The stored lists
    must be exactly what the rules produce; a stored document is never
    re-derived.
    """
    rules = settings.get("refresh_rules")
    result = daily_times(rules)
    problems = []
    if not result.full:
        problems.append(Problem("no_full"))
    if len(result.full) > MAX_DAILY_TIMES:
        problems.append(Problem("too_many"))
    for pair in spacing_conflicts(result.full):
        problems.append(Problem("too_close", pair))
    kept = set(kept)
    for value in result.full:
        if minutes(value) % SPACING and value not in kept:
            problems.append(Problem("off_quarter", (value,)))
    for index in result.unmatched_skips:
        problems.append(Problem("unmatched_skip", index=index))
    if result.full and {
        name: value for name, value in settings.items() if name in _STORED
    } != {
        name: value for name, value in stored_settings(rules).items() if name in _STORED
    }:
        problems.append(Problem("rules_mismatch"))
    return problems


# The rule an existing hourly or quarter-hour refresh is shown as: every
# UTC hour or quarter hour becomes every local hour or quarter hour of the
# day, the same instants in any zone whose offset is a whole hour.
_EVERY = {
    "hourly": {"every": 60, "from": "00:00", "to": "23:00"},
    "quarter_hour": {"every": 15, "from": "00:00", "to": "23:45"},
}


def converted_rules(settings):
    """The ``refresh_rules`` the settings page shows for stored ``settings``.

    A schedule saved with its rules is shown as saved, less any quick rule
    (``full_rules``; quick updates were removed, #1147). An existing
    schedule (no ``refresh_rules``, every schedule before #632) is converted
    by the mapping in the background-processing spec's "Stored schedule and
    upgrade", after ``cadence.refresh_settings`` fills its defaults: one
    "Full at" row per listed full time (kept off-quarter-hour times
    included, no rule inferred), or a full rule every 60 or 15 minutes
    through the day; no skips; and skipping around Family emails off, since
    such a schedule never skips. Its stored quick setting is ignored. Pure:
    the result is never stored by this function.
    """
    stored = settings.get("refresh_rules")
    if stored is not None:
        return full_rules(stored)
    schedule = refresh_settings(settings)
    if schedule["frequency"] == "daily":
        rules = [
            {"kind": "full", "at": value} for value in schedule["full_refresh_times"]
        ]
    else:
        rules = [{"kind": "full", **_EVERY[schedule["frequency"]]}]
    return {"rules": rules, "skips": [], "skip_around_family_emails": False}
