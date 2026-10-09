"""The refresh schedule's rules, exceptions, coverage and spacing (#632).

The Administrator describes the daily refresh schedule in layers, the way an
iCalendar recurrence set is built (see the background-processing spec,
"Schedule entries and rules"):

1. rules, "Full or Quick every N minutes from a start time to a last time";
2. single times, "Full or Quick at a time";
3. exceptions ("skips"): times, or from–to ranges (start included, end
   excluded), that remove any time of either kind;
4. precedence: a time that is both Full and Quick is Full;
5. coverage: a quick time a rule generated is left out when a full time falls
   at or after it and less than one rule step after it, or less than 15
   minutes before it, on the same day (no wrap past midnight).

The result is the **daily times**, which the settings store as
``full_refresh_times`` and ``quick_refresh_times`` beside the rules
themselves (``refresh_rules``). The scheduler reads only the two lists; these
functions are what the settings page and the command line use to derive and
check them, and what the configuration schema uses to check the rules' shape.
Everything here is pure: wall times only, no time zone, no database.
"""

from dataclasses import dataclass

from .cadence import MAX_DAILY_TIMES, canonical_time, refresh_settings

# The rule intervals the editor offers, in minutes.
STEPS = (15, 30, 60, 120, 180, 240, 360, 480, 720)
# Any two daily times must be at least this many minutes apart.
SPACING = 15
DAY = 24 * 60
KINDS = ("full", "quick")
# The closed set of ``refresh_rules`` fields.
FIELDS = frozenset({"rules", "skips", "skip_around_family_emails"})
SINGLE_RULE = frozenset({"kind", "at"})
RANGE_RULE = frozenset({"kind", "every", "from", "to"})
SINGLE_SKIP = frozenset({"at"})
RANGE_SKIP = frozenset({"from", "to"})
# A bound on rows of each kind, so a stored document stays small: more rules
# or skips than daily times could never all matter.
MAX_ROWS = MAX_DAILY_TIMES


def minutes(value):
    """Minutes after midnight of a canonical ``HH:MM`` time."""
    return int(value[:2]) * 60 + int(value[3:])


def clock(value):
    """The canonical ``HH:MM`` time ``value`` minutes after midnight."""
    return f"{value // 60:02d}:{value % 60:02d}"


def _rule_ok(rule):
    """Whether one rule row has a closed, canonical shape."""
    if type(rule) is not dict or rule.get("kind") not in KINDS:
        return False
    if rule.keys() == SINGLE_RULE:
        return canonical_time(rule["at"])
    return (
        rule.keys() == RANGE_RULE
        and type(rule["every"]) is int
        and rule["every"] in STEPS
        and canonical_time(rule["from"])
        and canonical_time(rule["to"])
        # A rule never crosses midnight; one whose last time equals its
        # start gives that one time.
        and rule["to"] >= rule["from"]
    )


def _skip_ok(skip):
    """Whether one skip row has a closed, canonical shape."""
    if type(skip) is not dict:
        return False
    if skip.keys() == SINGLE_SKIP:
        return canonical_time(skip["at"])
    return (
        skip.keys() == RANGE_SKIP
        and canonical_time(skip["from"])
        and canonical_time(skip["to"])
        # The end is excluded, so it must be after the start.
        and skip["to"] > skip["from"]
    )


def valid_shape(value):
    """Whether ``value`` is a ``refresh_rules`` document of the closed shape.

    ``rules`` (at least one) and ``skips`` are lists of rows of the closed
    forms above; ``skip_around_family_emails`` is a boolean. Whether the
    rules produce the stored lists is ``check_schedule``'s question.
    """
    return (
        type(value) is dict
        and value.keys() == FIELDS
        and type(value["rules"]) is list
        and 1 <= len(value["rules"]) <= MAX_ROWS
        and all(_rule_ok(rule) for rule in value["rules"])
        and type(value["skips"]) is list
        and len(value["skips"]) <= MAX_ROWS
        and all(_skip_ok(skip) for skip in value["skips"])
        and type(value["skip_around_family_emails"]) is bool
    )


@dataclass(frozen=True)
class DailyTimes:
    """The daily times the rules produce, and why others are left out.

    ``full`` and ``quick`` are sorted ``HH:MM`` tuples. ``covered`` pairs each
    quick time that coverage left out with the full time that covers it.
    ``unmatched_skips`` are the indexes of skips that matched no time.
    ``replaced`` are the quick times precedence dropped because a full time
    falls at the same time (the preview shows each as covered by it).
    """

    full: tuple
    quick: tuple
    covered: tuple = ()
    unmatched_skips: tuple = ()
    replaced: tuple = ()


def _skipped(skip, value):
    """Whether the skip row removes the time ``value`` (minutes)."""
    if "at" in skip:
        return value == minutes(skip["at"])
    return minutes(skip["from"]) <= value < minutes(skip["to"])


def _covering(value, step, full):
    """The full time (minutes) that covers a rule's quick time, or None.

    A full time at or after it and less than one rule step after it, or less
    than ``SPACING`` minutes before it, on the same day: the earliest such.
    """
    found = [
        f for f in full if value <= f < value + step or value - SPACING < f < value
    ]
    return min(found, default=None)


def daily_times(refresh_rules):
    """Apply the rules, single times, skips, precedence and coverage, in order.

    ``refresh_rules`` must have the closed shape (``valid_shape``); anything
    else raises ``ValueError``.
    """
    if not valid_shape(refresh_rules):
        raise ValueError("Refresh rules require the closed rule shape.")
    full, quick = set(), set()
    # The largest step of any range rule that generated each quick time; a
    # quick time an Administrator listed on its own is never covered.
    steps, single_quick = {}, set()
    for rule in refresh_rules["rules"]:
        if "at" in rule:
            generated = [minutes(rule["at"])]
        else:
            generated = range(
                minutes(rule["from"]), minutes(rule["to"]) + 1, rule["every"]
            )
        for value in generated:
            if rule["kind"] == "full":
                full.add(value)
            else:
                quick.add(value)
                if "at" in rule:
                    single_quick.add(value)
                else:
                    steps[value] = max(steps.get(value, 0), rule["every"])
    # Each skip is matched against the times before any skip, so a skip
    # that overlaps or repeats another is not reported as matching nothing.
    generated = full | quick
    unmatched = []
    for index, skip in enumerate(refresh_rules["skips"]):
        removed = {value for value in generated if _skipped(skip, value)}
        if not removed:
            unmatched.append(index)
        full -= removed
        quick -= removed
    replaced = quick & full
    quick -= full
    covered = []
    for value in sorted(quick):
        if value in single_quick or value not in steps:
            continue
        by = _covering(value, steps[value], full)
        if by is not None:
            covered.append((clock(value), clock(by)))
    quick -= {minutes(value) for value, _ in covered}
    return DailyTimes(
        tuple(clock(value) for value in sorted(full)),
        tuple(clock(value) for value in sorted(quick)),
        tuple(covered),
        tuple(unmatched),
        tuple(clock(value) for value in sorted(replaced)),
    )


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
    ``delta_refresh`` is "times" with ``quick_refresh_times`` when there are
    quick times, else "off" with no quick list. Raises ``ValueError`` when
    the rules leave no full time.
    """
    result = daily_times(refresh_rules)
    if not result.full:
        raise ValueError("A refresh schedule needs at least one full refresh.")
    settings = {
        "full_refresh": "daily",
        "nightly_time": result.full[0],
        "full_refresh_times": list(result.full),
        "delta_refresh": "times" if result.quick else "off",
        "refresh_rules": refresh_rules,
    }
    if result.quick:
        settings["quick_refresh_times"] = list(result.quick)
    return settings


def check_schedule(settings, *, kept=()):
    """Every reason the schedule in ``settings`` cannot be saved; empty if none.

    The one validator the settings form and the command line share (#632).
    ``settings`` are the ParishSoft integration's schedule settings with
    ``refresh_rules`` of the closed shape (``ValueError`` otherwise).
    ``kept`` are full times already stored off the quarter hour, which an
    Administrator may keep as full times but not add; they excuse only a
    full time, never a quick time at the same wall time. The stored lists
    must be exactly what the rules produce; a stored document is never
    re-derived.
    """
    rules = settings.get("refresh_rules")
    result = daily_times(rules)
    problems = []
    if not result.full:
        problems.append(Problem("no_full"))
    if len(result.full) + len(result.quick) > MAX_DAILY_TIMES:
        problems.append(Problem("too_many"))
    for pair in spacing_conflicts(result.full + result.quick):
        problems.append(Problem("too_close", pair))
    kept = set(kept)
    for value in sorted(set(result.full + result.quick)):
        if minutes(value) % SPACING and not (value in kept and value in result.full):
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

    A schedule saved with its rules is shown as saved. An existing schedule
    (no ``refresh_rules``, every schedule before #632) is converted by the
    mapping in the background-processing spec's "Stored schedule and
    upgrade", after ``cadence.refresh_settings`` fills its defaults: one
    "Full at" row per listed full time (kept off-quarter-hour times
    included, no rule inferred), or a full rule every 60 or 15 minutes
    through the day; a quick rule every 15 or 60 minutes through the day, or
    none; no skips; and skipping around Family emails off, since such a
    schedule never skips.

    The result runs exactly today's times: precedence (``daily_times``)
    drops only a quick time equal to a full time, as the old scheduler
    joined the two. Coverage would also drop a rule's quick time near a
    full time (one the old scheduler runs, e.g. 08:00 beside an 08:30 full
    time with hourly quick updates), so each such time gets its own "Quick
    at" row, which coverage never removes; a time too close to a full time
    then shows as a spacing problem rather than silently disappearing.
    Pure: the result is never stored by this function.
    """
    stored = settings.get("refresh_rules")
    if stored is not None:
        return stored
    schedule = refresh_settings(settings)
    if schedule["frequency"] == "daily":
        rules = [
            {"kind": "full", "at": value} for value in schedule["full_refresh_times"]
        ]
    else:
        rules = [{"kind": "full", **_EVERY[schedule["frequency"]]}]
    if schedule["delta_refresh"] in _EVERY:
        rules.append({"kind": "quick", **_EVERY[schedule["delta_refresh"]]})
    converted = {"rules": rules, "skips": [], "skip_around_family_emails": False}
    # Keep every quick time coverage would remove: the old scheduler runs it.
    rules.extend(
        {"kind": "quick", "at": value} for value, _ in daily_times(converted).covered
    )
    return converted
