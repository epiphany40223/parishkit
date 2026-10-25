"""The Campaign calendar's read-only operational events (#1808, slice 2).

Beside the sends, the calendar draws what else happens on the server that a
send can run into, all read in the page's one read-only snapshot and none of
it stored anew:

- **ParishSoft refreshes**: the saved refresh schedule's daily times
  (``refresh_status.refresh_schedule``, the times the refresh scheduler
  ticks at), each resolved on a day in the campaign's zone through the
  shared daylight-saving resolver, as the scheduler resolves them. Only the
  days around a send are expanded (the page shows a day's refreshes only on
  a day that holds a send), so a dense schedule never lists thousands of
  times. A schedule that refreshes every hour or quarter hour (a setting
  older than the daily times) is drawn as nothing: it would fill every day.
- **Backups**: the backups that finished (``BackupRun``, the record System
  health's Backups panel reads), from when each started to when it
  completed. The backup schedule itself is a cron entry on the server that
  the application cannot read, so a backup still to come is never drawn.
- **Go-live milestones**: for each Production activation of the campaign,
  when go-live started (the request's creation) and when Production was
  activated.

Refreshes and backups recur daily, so the page draws them only on days that
also hold a send; milestones always show. In Production, each reminder still
to come whose two-hour preparation window holds a refresh
(``schedule_production.refresh_in_lead_window``, the scheduler's own
warning; only a reminder is prepared ahead) and each send made while a
backup ran gets a note in words. When the schedule skips refreshes around
Family emails, the non-nightly times inside a send's window
(``send_windows.email_windows``, the scheduler's own) are not drawn.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.source.send_windows import (
    MINIMUM_ESTIMATE,
    Email,
    email_windows,
    window_cause,
)

# The most finished backups listed: a campaign's months of twice-daily
# backups and requested ones stay far below it.
BACKUPS_LISTED = 2_000
# A day either side of the sends and campaign dates: a browser east or west
# of the parish places some times on the neighbouring day.
MARGIN = timedelta(days=1)
# The notes a send can carry, in words.
NOTE_REFRESH = _("A ParishSoft refresh falls in the 2 hours before it")
NOTE_BACKUP = _("Sent during a backup")
# A go-live milestone's words, by kind.
MILESTONES = {
    "go_live": _("Go-live started"),
    "activated": _("Production activated"),
}


@dataclass(frozen=True)
class CalendarEvent:
    """One operational event: its kind, when it starts and, for a backup, ends.

    ``kind`` is ``refresh``, ``backup`` or ``milestone``; a milestone's
    ``label`` is its words. ``daily`` events (refreshes and backups) are
    drawn only on a day that holds a send.
    """

    kind: str
    start: datetime
    end: datetime | None = None
    label: str = ""

    @property
    def daily(self):
        """Whether the event recurs daily, so the page draws it beside sends only."""
        return self.kind != "milestone"


def refresh_times(schedule):
    """The daily ``HH:MM`` refresh times of ``schedule``, or none.

    ``schedule`` is ``refresh_status.refresh_schedule``'s (None when
    ParishSoft is not configured). An hourly or quarter-hour schedule lists
    no times: it would fill every day.
    """
    if schedule is None or schedule["frequency"] != "daily":
        return ()
    return tuple(sorted({schedule["nightly_time"], *schedule["full_refresh_times"]}))


def skip_windows(sends, now):
    """The Family email windows the scheduler would skip refreshes in.

    One window set per Family send still to come (not a report email), from
    the scheduler's own ``email_windows`` with the minimum send estimate:
    the calendar cannot know how long a send will take.
    """
    estimates = {"initial": MINIMUM_ESTIMATE, "reminder": MINIMUM_ESTIMATE}
    return [
        window
        for send in sends
        if send.kind in estimates and send.due > now
        for window in email_windows(
            Email(send.kind, send.due), now=now, estimates=estimates
        )
    ]


def refresh_events(schedule, sends, windows=()):
    """The refreshes of ``schedule`` on the parish-local days around ``sends``.

    Each daily time is resolved on each parish-local day that holds a send,
    and the day either side, so whichever day the browser places a send on,
    its refreshes are listed. A time other than the nightly one that falls
    in one of ``windows`` is skipped by the scheduler, so it is left out.
    """
    walls = refresh_times(schedule)
    if not walls:
        return []
    zone_name = schedule["timezone"]
    zone = ZoneInfo(zone_name)
    days = {
        send.due.astimezone(zone).date() + offset
        for send in sends
        for offset in (-MARGIN, timedelta(), MARGIN)
    }
    events = [
        (
            wall,
            resolve_local(datetime.combine(day, time.fromisoformat(wall)), zone_name),
        )
        for day in sorted(days)
        for wall in walls
    ]
    return [
        CalendarEvent("refresh", at)
        for wall, at in events
        if wall == schedule["nightly_time"] or window_cause(windows, at) is None
    ]


def backup_events(start, end):
    """The backups that ran between ``start`` and ``end``, oldest first.

    Only the start and completion times are read: the web login may read no
    other column a backup's sizes, digests or key would need here.
    """
    from parishkit.stewardship.jobs.backup_models import BackupRun

    rows = (
        BackupRun.objects.filter(started_at__lt=end, completed_at__gte=start)
        .order_by("started_at", "id")
        .values_list("started_at", "completed_at")[:BACKUPS_LISTED]
    )
    return [CalendarEvent("backup", started, completed) for started, completed in rows]


def milestone_events(campaign_id):
    """The campaign's go-live milestones: each activation's start and activation."""
    from parishkit.stewardship.campaigns.production_models import (
        ProductionTransitionRequest,
    )

    return milestones(
        ProductionTransitionRequest.objects.filter(
            campaign_id=campaign_id, state="activated"
        )
        .order_by("created_at", "id")
        .values_list("created_at", "activated_at")
    )


def milestones(rows):
    """Each activation's ``(started, activated)`` instants as two milestones."""
    return [
        CalendarEvent("milestone", at, label=str(MILESTONES[kind]))
        for started, activated in rows
        for kind, at in (("go_live", started), ("activated", activated))
    ]


def send_notes(send, schedule, backups, now, production=True):
    """The notes in words for one send of the calendar (#1808).

    A reminder still to come whose two hours of preparation hold a refresh
    says so, from the scheduler's own check, in Production only: that is
    where mail is prepared ahead, and an Initial invitation never is. When
    the schedule skips refreshes around Family emails, only the nightly
    time counts (the scheduler never skips it). A send whose time falls
    while a finished backup ran says that.
    """
    from parishkit.stewardship.campaigns.schedule_production import (
        refresh_in_lead_window,
    )

    notes = []
    walls = refresh_times(schedule)
    if walls and schedule.get("skips_around_emails"):
        walls = (schedule["nightly_time"],)
    if (
        production
        and walls
        and send.kind == "reminder"
        and send.due > now
        and refresh_in_lead_window(walls, schedule["timezone"], [send.due])
    ):
        notes.append(str(NOTE_REFRESH))
    if any(event.start <= send.due <= event.end for event in backups):
        notes.append(str(NOTE_BACKUP))
    return notes


def calendar_events(configuration, campaign, sends, now):
    """The calendar's operational events and each send's notes (#1808).

    ``configuration`` is the applied system configuration, ``campaign`` the
    current campaign and ``sends`` the calendar's sends. Returns ``(events,
    items)``: the events by time, and ``(send, notes)`` for each send in
    the order given. Backups are read from a day before the campaign's
    first day (or first send) to a day after its last day (or last send).
    """
    from parishkit.stewardship.source.refresh_status import refresh_schedule

    schedule = refresh_schedule(configuration)
    dates = campaign.active_configuration
    first = min([dates.starts_at, *(send.due for send in sends)]) - MARGIN
    last = max([dates.ends_at, *(send.due for send in sends)]) + MARGIN
    backups = backup_events(first, last)
    # The scheduler skips refreshes around Family emails only in Production,
    # with the current campaign live and delivery not paused.
    production = configuration.mode == "production"
    windows = []
    if (
        production
        and schedule is not None
        and schedule.get("skips_around_emails")
        and campaign.state == "active"
        and not campaign.delivery_paused
    ):
        windows = skip_windows(sends, now)
    events = sorted(
        refresh_events(schedule, sends, windows)
        + backups
        + milestone_events(campaign.pk),
        key=lambda event: event.start,
    )
    items = [
        (send, send_notes(send, schedule, backups, now, production)) for send in sends
    ]
    return events, items
