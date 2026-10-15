"""Mail schedule send times in the browser's time zone (#558, #878).

A saved schedule keeps wall-clock values in the campaign's time zone (its
``date``, ``time`` and ``weekday``; ``configuration.schedule_values``), and
the scheduler resolves them to instants. New and Edit scheduled email and the
date-change review (``schedule_forms.ScheduleSet`` with ``browser_times``)
show and take those times in the browser's time zone instead, as every Admin
date and time is (the global presentation rules):

- :func:`instant` is the UTC moment a saved schedule's values stand for. The
  page carries it, and the page script fills the fields in the browser's zone.
- :func:`to_campaign` turns what was typed in the browser's zone back into
  campaign-local values through the same moment, so the server's own rules
  (inside the campaign dates, after the initial invitation, never two
  mailings at once) compare the moments the Admin chose. An Edit page
  posted with the date and time it showed keeps the saved values exactly
  (:func:`shown`), so an unchanged save never moves a send where the two
  zones change their clocks at different instants.

A digest has no date, so its time (and a weekly digest's weekday) is
converted at its next send from now: the zones' offset on that day decides
it. Where the two zones change their clocks on different dates, a digest
keeps its campaign wall-clock time between those dates, as it always has.
The page posts back the moment it drew a digest from (``drawn``), so a page
drawn on one side of such a change and posted on the other is still read as
unchanged when its fields are as drawn.

A dated send typed in the browser's zone whose moment falls in the hour the
campaign's clocks repeat when they fall back is refused (:class:`RepeatedHour`):
a schedule keeps only a campaign wall-clock time, which the scheduler reads
as the first of the two, so it would send an hour early. A moment can never
fall in the hour the campaign's clocks skip, since every real moment has a
campaign wall-clock time; a time the browser's own clocks skip reads forward.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.web.dates import browser_instant, browser_zone


def _next(today, weekday):
    """``today``, or the first day from ``today`` on with ``weekday`` (Monday 0)."""
    if weekday is None:
        return today
    return today + timedelta(days=(weekday - today.weekday()) % 7)


# How far from now a posted digest moment may be: a weekly digest's next send
# is up to a week ahead, and a page may sit open for a while before it posts.
DRAWN_WINDOW = timedelta(days=14)


class RepeatedHour(ValueError):
    """A typed send moment the campaign's clocks show twice (fall back)."""


def _drawn(values, campaign_zone, now, drawn):
    """The digest moment a page drew, when ``drawn`` really is one of its sends.

    ``drawn`` is posted text, so it is used only when it parses as an aware
    instant within :data:`DRAWN_WINDOW` of ``now`` that the saved digest
    sends at (its campaign wall-clock time, and its weekday for a weekly
    digest, resolved as the scheduler does). Anything else is None, and the
    caller falls back to the next send from ``now``.
    """
    if not drawn or values.get("date") or now is None:
        return None
    try:
        moment = datetime.fromisoformat(drawn)
    except (TypeError, ValueError):
        return None
    if moment.utcoffset() is None or abs(moment - now) > DRAWN_WINDOW:
        return None
    moment = moment.astimezone(UTC)
    day = moment.astimezone(ZoneInfo(campaign_zone)).date()
    weekday = values.get("weekday")
    if weekday is not None and day.weekday() != weekday:
        return None
    clock = time.fromisoformat(values["time"])
    if resolve_local(datetime.combine(day, clock), campaign_zone) != moment:
        return None
    return moment


def instant(values, campaign_zone, now, drawn=None):
    """The UTC moment a saved schedule's campaign-local ``values`` stand for.

    A dated schedule's own date; a digest's next send day from ``now`` in the
    campaign's zone (today for a daily digest), or the moment the page drew
    it from (``drawn``, see :func:`_drawn`). Resolved as the scheduler
    resolves it (``resolve_local``).
    """
    kept = _drawn(values, campaign_zone, now, drawn)
    if kept is not None:
        return kept
    if values.get("date"):
        day = date.fromisoformat(values["date"])
    else:
        today = now.astimezone(ZoneInfo(campaign_zone)).date()
        day = _next(today, values.get("weekday"))
    clock = time.fromisoformat(values["time"])
    return resolve_local(datetime.combine(day, clock), campaign_zone)


def in_browser(values, zone, campaign_zone, now, drawn=None):
    """A saved schedule's send time of day in the browser's ``zone``."""
    moment = instant(values, campaign_zone, now, drawn)
    return moment.astimezone(browser_zone(zone)).time()


def shown(values, day, clock, weekday, *, zone, campaign_zone, now, drawn=None):
    """Whether typed ``day``, ``clock`` and ``weekday`` are what Edit showed.

    The Edit page fills its fields from :func:`instant` in the browser's
    ``zone``; this compares the posted fields with that same conversion of
    the saved ``values`` (only server-side values, nothing posted besides
    the fields). Around a fall-back hour the browser's wall-clock time can
    occur twice, and reading it again would take the first occurrence, so
    an unchanged save of the second one would move the send by an hour.
    ``drawn`` is the moment the page drew a digest from (:func:`instant`).
    """
    moment = instant(values, campaign_zone, now, drawn).astimezone(browser_zone(zone))
    has_weekday = values.get("weekday") is not None
    return (
        (day is not None) == bool(values.get("date"))
        and (day is None or day == moment.date())
        and clock == moment.time()
        and (weekday is not None) == has_weekday
        and (weekday is None or weekday == moment.weekday())
    )


def to_campaign(
    day,
    clock,
    weekday,
    *,
    zone,
    campaign_zone,
    now,
    saved=None,
    saved_zone=None,
    drawn=None,
):
    """Convert a send time typed in the browser's ``zone`` to campaign-local.

    ``day`` (a ``date`` or None), ``clock`` (a ``time``) and ``weekday``
    (0-6 or None) are the page's cleaned fields. Returns the same three in
    the campaign's zone: the date only when one was typed, the weekday only
    when one was chosen. A digest is converted at its next send day in the
    browser's zone (see the module docstring). Raises ``UnknownZone`` for a
    blank or unknown ``zone``. A time that occurs twice or not at all in the
    browser's zone reads as ``browser_instant`` reads it: the first of two,
    and a skipped one forward (2:30 AM becomes 3:30 AM daylight time).

    ``saved`` is the edited schedule's campaign-local values (None on New).
    When the typed values are what the Edit page showed for them
    (:func:`shown`), those saved values are returned unchanged.
    ``saved_zone`` is the zone those saved values are in, when it is not
    ``campaign_zone``: the date-change review may propose a new campaign
    zone, and the page showed the saved row in the saved one. An unchanged
    row then keeps its campaign wall-clock time in the new zone (as a time
    zone change always has); a changed row converts into ``campaign_zone``.
    ``drawn`` is the moment the page drew a saved digest from.

    Raises :class:`RepeatedHour` for a dated send whose moment is the second
    of the two the campaign's clocks show when they fall back (see the
    module docstring); the first of the two is saved exactly.
    """
    if saved is not None and shown(
        saved,
        day,
        clock,
        weekday,
        zone=zone,
        campaign_zone=saved_zone or campaign_zone,
        now=now,
        drawn=drawn,
    ):
        return (
            date.fromisoformat(saved["date"]) if saved.get("date") else None,
            time.fromisoformat(saved["time"]),
            saved.get("weekday"),
        )
    reference = day or _next(now.astimezone(browser_zone(zone)).date(), weekday)
    moment = browser_instant(
        reference.isoformat(), clock.isoformat(timespec="seconds"), zone
    ).astimezone(ZoneInfo(campaign_zone))
    # The scheduler reads a campaign wall-clock time as its first moment; a
    # dated send at the second (the repeated hour) would go an hour early.
    # (Compared in UTC: PEP 495 makes an aware datetime inside a repeated
    # hour unequal to every instant in another zone, even its own.)
    first = resolve_local(moment.replace(tzinfo=None), campaign_zone)
    if day and first != moment.astimezone(UTC):
        raise RepeatedHour("The send time falls in a repeated hour.")
    return (
        moment.date() if day else None,
        moment.time(),
        None if weekday is None else moment.weekday(),
    )
