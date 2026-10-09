"""Mail schedule send times in the browser's time zone (#558, #878).

A saved schedule keeps wall-clock values in the campaign's time zone (its
``date``, ``time`` and ``weekday``; ``configuration.schedule_values``), and
the scheduler resolves them to instants. New and Edit scheduled email show and
take those times in the browser's time zone instead, as every Admin date and
time is (the global presentation rules):

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
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.web.dates import browser_instant, browser_zone


def _next(today, weekday):
    """``today``, or the first day from ``today`` on with ``weekday`` (Monday 0)."""
    if weekday is None:
        return today
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def instant(values, campaign_zone, now):
    """The UTC moment a saved schedule's campaign-local ``values`` stand for.

    A dated schedule's own date; a digest's next send day from ``now`` in the
    campaign's zone (today for a daily digest). Resolved as the scheduler
    resolves it (``resolve_local``).
    """
    if values.get("date"):
        day = date.fromisoformat(values["date"])
    else:
        today = now.astimezone(ZoneInfo(campaign_zone)).date()
        day = _next(today, values.get("weekday"))
    clock = time.fromisoformat(values["time"])
    return resolve_local(datetime.combine(day, clock), campaign_zone)


def in_browser(values, zone, campaign_zone, now):
    """A saved schedule's send time of day in the browser's ``zone``."""
    moment = instant(values, campaign_zone, now)
    return moment.astimezone(browser_zone(zone)).time()


def shown(values, day, clock, weekday, *, zone, campaign_zone, now):
    """Whether typed ``day``, ``clock`` and ``weekday`` are what Edit showed.

    The Edit page fills its fields from :func:`instant` in the browser's
    ``zone``; this compares the posted fields with that same conversion of
    the saved ``values`` (only server-side values, nothing posted besides
    the fields). Around a fall-back hour the browser's wall-clock time can
    occur twice, and reading it again would take the first occurrence, so
    an unchanged save of the second one would move the send by an hour.
    """
    moment = instant(values, campaign_zone, now).astimezone(browser_zone(zone))
    has_weekday = values.get("weekday") is not None
    return (
        (day is not None) == bool(values.get("date"))
        and (day is None or day == moment.date())
        and clock == moment.time()
        and (weekday is not None) == has_weekday
        and (weekday is None or weekday == moment.weekday())
    )


def to_campaign(day, clock, weekday, *, zone, campaign_zone, now, saved=None):
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
    """
    if saved is not None and shown(
        saved, day, clock, weekday, zone=zone, campaign_zone=campaign_zone, now=now
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
    return (
        moment.date() if day else None,
        moment.time(),
        None if weekday is None else moment.weekday(),
    )
