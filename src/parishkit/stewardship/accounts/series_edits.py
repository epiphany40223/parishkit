"""Plan "this and following", "the whole series" and deleting a series (#469).

A remembered reminder series (``series_schema``) is an authoring record: its
members are ordinary reminders. Editing a series therefore means adding and
removing ordinary reminders, plus the bookkeeping of the series records, all
in one configuration change through ``schedule_changes.build_preview``.
``plan`` works that change out from the applied records alone, so the command
line (``admin_changes``) and Edit scheduled email (``schedule_entry_views``)
share it; it reads no database and changes nothing. ``rule_text`` says a rule
in plain words for the page and its review.

From a cut date (the chosen reminder's date, or for the whole series the
date of its first reminder that has not sent):

- the series keeps its reminders before the cut date, and its rule's end
  becomes the day before; a series left with none is replaced in place;
- a new series holds the new rule's reminders from the cut date: the given
  rule's dates, or without one the members' own dates;
- a date's unsent member whose date, time and email are unchanged stays as
  it is (same id, revision and prepared work); any other member from the cut
  date is removed and its date added again with the new values.

Deleting a series removes its unsent reminders and the series record; sent
reminders stay as ordinary reminders. Nothing here touches a sent reminder,
Family credentials or delivery, which never reads a series.
"""

from datetime import date, time, timedelta

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from parishkit.stewardship.campaigns.recurrence import canonical_rule, expand
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.presentation import parish_date

SCOPES = ("following", "series")
# A monthly rule's ordinal, in words (``recurrence.ORDINALS``).
ORDINAL_WORDS = {
    1: gettext_lazy("first"),
    2: gettext_lazy("second"),
    3: gettext_lazy("third"),
    4: gettext_lazy("fourth"),
    -1: gettext_lazy("last"),
}


class SeriesEditError(ValueError):
    """The series edit cannot be made; its message says why, in plain words.

    The message is the command line's (``admin_changes``); ``reason`` is a
    stable code the page maps to its own translated words
    (``schedule_entry_views``): ``not_member``, ``all_sent``,
    ``read_only_series``, ``read_only_following``, ``rule_start`` or
    ``no_dates``.
    """

    def __init__(self, message, reason=None):
        """Keep the message and the refusal's reason code."""
        super().__init__(message)
        self.reason = reason


def _time(value):
    """A wall-clock time for comparison, or the value itself if it is not one."""
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return value


def _same(values, wanted):
    """Whether a saved reminder already has the wanted date, time and email."""
    return (
        values["date"] == wanted["date"]
        and _time(values["time"]) == _time(wanted["time"])
        and values["template_version"] == wanted["template_version"]
    )


def _order(schedules):
    """A sort key for member ids: by date, then time, then id."""
    return lambda member: (
        schedules[member]["date"],
        str(_time(schedules[member]["time"])),
        member,
    )


def _names(days):
    """Weekday names joined in plain words: "Monday", "Monday and Thursday"."""
    names = [str(WEEKDAYS[day]) for day in days]
    if len(names) == 1:
        return names[0]
    return _("%(list)s and %(last)s") % {
        "list": ", ".join(names[:-1]),
        "last": names[-1],
    }


def rule_text(rule):
    """A canonical rule in plain, translated words, with its first and last dates.

    For example "Every Monday and Thursday, October 5, 2054 – November 13,
    2054", "Every day, ..." or "The last Friday of every month, ...". The
    dates are calendar dates, never shifted (UX conventions, "Schedules").
    """
    frequency = rule["frequency"]
    first, last = (date.fromisoformat(rule[name]) for name in ("start", "end"))
    dates = {"start": parish_date(first), "end": parish_date(last)}
    if frequency == "daily":
        return _("Every day, %(start)s – %(end)s") % dates
    if frequency == "weekly":
        return _("Every %(days)s, %(start)s – %(end)s") % (
            dates | {"days": _names(rule["weekdays"])}
        )
    monthly = rule["monthly"]
    if "day" in monthly:
        return _("Day %(day)s of every month, %(start)s – %(end)s") % (
            dates | {"day": monthly["day"]}
        )
    return _("The %(ordinal)s %(weekday)s of every month, %(start)s – %(end)s") % (
        dates
        | {
            "ordinal": ORDINAL_WORDS[monthly["ordinal"]],
            "weekday": WEEKDAYS[monthly["weekday"]],
        }
    )


def cut(series, schedules, read_only, *, scope, start=None):
    """The reminder a series edit is cut at, and its date (ISO).

    "This and following" (``scope="following"``) cuts at the member
    ``start``; "the whole series" at the series' first member that has not
    sent or been prepared (not in ``read_only``). Raises ``SeriesEditError``
    when there is no such member.
    """
    members = sorted(series["values"]["schedules"], key=_order(schedules))
    if scope == "following":
        if start not in members:
            raise SeriesEditError("That reminder is not in this series.", "not_member")
        anchor = start
    else:
        unsent = [member for member in members if member not in read_only]
        if not unsent:
            raise SeriesEditError(
                "Every reminder of this series has already sent.", "all_sent"
            )
        anchor = unsent[0]
    return anchor, schedules[anchor]["date"]


def plan(
    series,
    schedules,
    read_only,
    *,
    scope,
    start=None,
    rule=None,
    time_of_day=None,
    template_version=None,
    times=None,
):
    """Work out one series edit; raise ``SeriesEditError`` when it cannot be made.

    ``series`` is the applied ``schedule_series`` record, ``schedules`` maps
    each saved schedule id of its campaign to its values, and ``read_only``
    holds the ids that have run or have work prepared (the page's read-only
    statuses, ``schedule_table.PAST``). ``scope`` is ``"following"`` (from
    the member ``start``), ``"series"`` or ``"delete"``. ``rule`` is the new
    canonical rule, or None to keep the members' dates; ``time_of_day`` and
    ``template_version`` replace every new reminder's, and otherwise each
    takes its date's member's, or the cut reminder's. ``times`` maps a date
    (ISO) to the time a new reminder on it takes, ahead of ``time_of_day``:
    the page converts a browser time on each date on its own (#558).

    Returns ``{"remove", "add", "series", "changes"}``: the saved reminder
    ids to remove; the reminders to add, as ``{"date", "time",
    "template_version"}`` in date order; the series that holds the result,
    as ``{"id", "rule", "keep"}`` (``id`` the record to update, or None for a
    new one; ``keep`` the saved reminders it holds besides the added ones),
    or None; and the ``schedule_series`` operations on the existing record.
    When nothing would change, every part is empty.
    """
    identifier = series["id"]
    old_rule = series["values"]["rule"]
    members = sorted(series["values"]["schedules"], key=_order(schedules))
    if scope == "delete":
        return {
            "remove": [member for member in members if member not in read_only],
            "add": [],
            "series": None,
            "changes": [
                {"operation": "remove", "section": "schedule_series", "id": identifier}
            ],
        }
    anchor, cut_date = cut(series, schedules, read_only, scope=scope, start=start)
    before = [member for member in members if schedules[member]["date"] < cut_date]
    after = [member for member in members if member not in before]
    if set(after) & read_only:
        if scope == "series":
            # The whole series starts at its first unsent reminder, so a sent
            # one on the same date (an earlier time) is what blocks it.
            raise SeriesEditError(
                "A reminder of this series on the date of its first unsent "
                "reminder has already sent or is being prepared; edit "
                'this and following ("following") from a later reminder.',
                "read_only_series",
            )
        raise SeriesEditError(
            "A reminder of this series from this date on has already sent or "
            "is being prepared; start from a later reminder.",
            "read_only_following",
        )
    if rule is not None and before and rule["start"] < cut_date:
        raise SeriesEditError(
            f"The new repeat must start on or after {cut_date}, the first reminder "
            "it changes; the earlier reminders stay in this series.",
            "rule_start",
        )
    by_date = {}
    for member in after:
        by_date.setdefault(schedules[member]["date"], member)
    if rule is None:
        # Each member from the cut date keeps its own date.
        targets = [(schedules[member]["date"], member) for member in after]
        new_rule = canonical_rule(old_rule | {"start": cut_date})
    else:
        targets = [(day, by_date.get(day)) for day in expand(rule)["dates"]]
        new_rule = rule
    if not targets:
        raise SeriesEditError("The new repeat gives no dates.", "no_dates")
    keep, add = [], []
    for day, member in targets:
        source = schedules[member if member is not None else anchor]
        clock = (times or {}).get(day, time_of_day)
        wanted = {
            "date": day,
            "time": clock if clock is not None else source["time"],
            "template_version": template_version
            if template_version is not None
            else source["template_version"],
        }
        if (
            member is not None
            and member not in keep
            and _same(schedules[member], wanted)
        ):
            keep.append(member)
        else:
            add.append(wanted)
    remove = [member for member in after if member not in keep]
    # What the saved rule would be if the reminders alone were kept.
    baseline = canonical_rule(old_rule | {"start": cut_date}) if before else old_rule
    if not remove and not add and (rule is None or new_rule == baseline):
        # The same reminders under the same rule: nothing to change. (A given
        # rule that differs still goes on to rewrite the series record.)
        return {"remove": [], "add": [], "series": None, "changes": []}
    if before:
        # The series keeps its earlier reminders under a rule that ends the
        # day before the cut; the new series is a new record.
        last = (date.fromisoformat(cut_date) - timedelta(days=1)).isoformat()
        changes = [
            {
                "operation": "update",
                "section": "schedule_series",
                "id": identifier,
                "values": {
                    "rule": canonical_rule(old_rule | {"end": last}),
                    "schedules": sorted(before),
                },
            }
        ]
        target = None
    else:
        # Nothing stays behind, so the series itself takes the new rule.
        changes, target = [], identifier
    return {
        "remove": remove,
        "add": add,
        "series": {"id": target, "rule": new_rule, "keep": sorted(keep)},
        "changes": changes,
    }
