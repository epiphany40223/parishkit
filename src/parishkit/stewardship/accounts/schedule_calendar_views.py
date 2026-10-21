"""Campaign calendar: every send of the campaign's mail schedules, by date (#469).

A page under Dates and mail schedules that changes nothing itself. It lists
each send the current schedule revisions make, in sending order: the
Initial invitation, each reminder, and every day or week of the Daily and
Weekly Admin digests, expanded with the scheduler's own evaluation
(``SchedulePlan.page``). The table of sends is the page's text equivalent;
the page script (``calendar-v1.js``) draws the same sends as a month grid,
one month at a time with Previous and Next (#1656), bucketed by the
browser's local day, since every Admin time is browser-local. The address's
``?month=`` names the month shown; only its form is checked here, since the
months the script draws follow the browser's zone.

Each send is a ``schedule_table.ScheduleRow`` with its own send time, so the
calendar shows the same names, emails, statuses and Edit links as the
scheduled emails table and cannot describe a schedule differently. Status
reads the same counts-only work summary, never Family data. Nothing here
changes anything: Edit opens the existing Edit scheduled email page (with
the month shown, so it returns there; its Delete is Edit's, #1656), and each
day's Add (#1521) the existing New scheduled email page with that send date
(``schedule_entry_views.added_on``), whose review, Apply and audit are New's.
Move… on a movable send (``ScheduleRow.movable``; a drag only fills in its
date, #1521) posts the new date to that send's Edit page
(``schedule_entry_views.MOVE``), so its review, Apply and audit are Edit's
too: the page only draws the review dialog Edit's answer opens in, and
Apply redraws the list of sends and the table in place.

The table, Every email by date, has its own view of the sends (#1656): Show
(``?show=``) leaves out the past ones by default, and its When and Schedule
headings sort it (``?sort=``, ``SORTING``), each refreshed in place. The grid
never follows them: the page also lists every send, in sending order, for
the script to draw from.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting, whole_table

from .admin_editing import error_response, principal
from .authentication import runtime
from .content_views import _records
from .digest_skips import digest_sends, skipped
from .schedule_entry_views import ERRORS, _recheck, year_month
from .schedule_forms import schedule_order
from .schedule_preview import work_summary
from .schedule_reads import campaign_schedules, schedule_state
from .schedule_table import DIGESTS, ScheduleRow, schedule_rows
from .schedule_views import campaign_email_names

# The address parameter naming the month shown (``2054-11``, #1656).
MONTH = "month"
# The table's Show choice (#1656): upcoming emails only (the default), or
# every email, past ones included.
SHOW = "show"
UPCOMING, ALL = "upcoming", "all"
# When sorts by send time (newest first on its first choice, as every time
# column does); Schedule by the schedules' sending order, each schedule's
# sends by time. Sending order is the default.
SORTING = Sorting.by_column(
    {
        "when": lambda send: (send.due, send.position),
        "schedule": lambda send: (send.position, send.due),
    },
    default="when",
    descending_first=("when",),
)
# The month grids' weekday headings, Sunday first as parish calendars are:
# the short name shown and the shared full name a screen reader reads.
WEEK = tuple(
    zip(
        (_("Sun"), _("Mon"), _("Tue"), _("Wed"), _("Thu"), _("Fri"), _("Sat")),
        WEEKDAYS[6:] + WEEKDAYS[:6],
        strict=True,
    )
)


def calendar_sends(previous, campaign, summary, now, names=None):
    """Every send of the saved schedules, in sending order.

    ``previous``, ``campaign``, ``summary``, ``now`` and ``names`` are
    ``schedule_table.schedule_rows``'s. A one-time schedule is its own row; a
    digest becomes one row per send, each a copy of the digest's row with that
    send's time in ``due``, so it keeps the digest's name, email, status and
    Edit link. A digest send whose time has passed is "passed" instead: muted
    and not linked, like a schedule that has run, since editing the digest
    changes only its later sends; and one the digest skips (#1658) is
    "skipped", muted and not linked too. A digest send still to come names
    its slot (``ScheduleRow.slot``), so its Edit link opens Edit for that
    send (``?send=``) and Delete can offer This email only. Sends at the
    same moment keep the table's sending order.
    """
    previous = sorted(previous, key=schedule_order)
    sends = []
    for record, row in zip(
        previous,
        schedule_rows(previous, campaign, summary, now, names),
        strict=True,
    ):
        if row.kind not in DIGESTS:
            sends.append(row)
            continue
        skips = skipped(record["values"])
        sends.extend(
            replace(
                row,
                due=slot.due_at,
                next_due=None,
                status="skipped"
                if slot.key in skips
                else "passed"
                if slot.due_at <= now
                else row.status,
                slot=slot.key,
            )
            for slot in digest_sends(record["values"], campaign)
        )
    return sorted(sends, key=lambda send: (send.due, send.position))


# Report email sends are listed up to this long after the campaign's last
# day: a report may repeat until the campaign is archived, an open end.
REPORT_HORIZON = timedelta(days=62)


def report_calendar_sends(configuration, campaign_id, campaign, now, position):
    """Every report email send of the campaign, as calendar rows (#1786).

    Expanded by the reports' own evaluator, skips and pauses set aside so a
    skipped or paused send is still listed, muted ("skipped", "paused"),
    as is a passed one; a send still to come links to its report's Edit
    page naming the send (``?send=``), which ticks that send's box for Skip
    (or Send) selected sends. An
    open-ended report is listed until ``REPORT_HORIZON`` after the
    campaign's last day. ``position`` is where the reports follow the
    Family mail in sending order.
    """
    from parishkit.stewardship.reports.report_email_list import plan, report_records

    end = date.fromisoformat(campaign["end_date"]) + REPORT_HORIZON
    through = resolve_local(datetime.combine(end, time()), campaign["timezone"])
    rows = []
    for offset, record in enumerate(report_records(configuration, campaign_id)):
        values = record["values"]
        free = plan(values | {"skip_dates": [], "paused_ranges": []}, campaign)
        pauses = [
            (
                datetime.fromisoformat(entry["start"]),
                entry["end"] and datetime.fromisoformat(entry["end"]),
            )
            for entry in values["paused_ranges"]
        ]
        link = reverse("admin:report_email_edit", args=[record["id"]])
        for slot in free.page(through=through, limit=1000).slots:
            paused = any(
                start <= slot.due_at and (stop is None or slot.due_at < stop)
                for start, stop in pauses
            )
            rows.append(
                ScheduleRow(
                    id=record["id"],
                    label=values["name"],
                    kind="report",
                    subject=values["subject"],
                    status="skipped"
                    if slot.key in values["skip_dates"]
                    else "paused"
                    if paused
                    else "passed"
                    if slot.due_at <= now
                    else "upcoming",
                    position=position + offset,
                    email=str(_("Report email")),
                    due=slot.due_at,
                    slot=slot.key,
                    link=link,
                )
            )
    return rows


def is_past(send, now):
    """Whether ``send``'s time has passed and it is no longer sending."""
    return send.due <= now and send.status not in ("preparing", "sending")


def sends_view(sends, parameters, now, path):
    """Every email by date's table, Show choice and Show links (#1656).

    ``parameters`` are the address's checked ones (``filters``): ``show``
    (``upcoming`` by default, or ``all``) and the table's ``sort``; any other
    value is refused with ValueError, as an unknown address. ``path`` is the
    page's own address, which each Show link names with that choice and the
    sort shown. Every heading keeps the Show choice.
    """
    show = parameters.get(SHOW, UPCOMING)
    if show not in (UPCOMING, ALL):
        raise ValueError("Unknown filter input.")
    sort = SORTING.parse(parameters)
    shown = sends if show == ALL else [s for s in sends if not is_past(s, now)]
    links = {
        key: f"{path}?{urlencode({SHOW: key, 'sort': sort})}" for key in (UPCOMING, ALL)
    }
    return {
        "table": whole_table(shown, sorting=SORTING, sort=sort, carry=[(SHOW, show)]),
        "show": show,
        "show_links": links,
    }


@require_http_methods(["GET", "HEAD"])
def schedule_calendar(request, campaign_id):
    """Show the current campaign's sends by date (Administrator).

    It reads the applied configuration in one read-only snapshot, as the
    scheduled emails list does, and rechecks access after rendering so a
    revocation committed meanwhile is not hidden. ``?month=`` (#1656) is
    the page script's; a malformed one is refused as an unknown address, as
    is a Show or sort the table does not offer (``sends_view``).
    """
    try:
        service = runtime()
        principal(request, service)
        parameters = filters(request.GET, allowed={MONTH, SHOW, "sort"})
        year_month(parameters, MONTH)
        now = timezone.now()
        with read_transaction():
            state, campaign, _editable = schedule_state(service, campaign_id, page=True)
            configuration = state[0]
            values = campaign.active_configuration.values
            family = campaign_schedules(configuration, campaign.pk)
            sends = calendar_sends(
                family,
                values,
                work_summary(campaign.pk),
                now,
                campaign_email_names(_records(configuration, campaign.pk)),
            )
            # Report emails' sends, named by each report (#1786).
            sends = sorted(
                sends
                + report_calendar_sends(
                    configuration, campaign.pk, values, now, len(family)
                ),
                key=lambda send: (send.due, send.position),
            )
            response = render(
                request,
                "stewardship/schedule-calendar.html",
                {
                    "campaign": campaign,
                    "values": values,
                    "sends": sends,
                    "weekdays": WEEK,
                    "list_url": reverse("admin:schedule_settings"),
                    # Each day's Add (#1521) opens New with ?date=<day>.
                    "add_url": reverse("admin:schedule_new"),
                    # Move… (#1521) posts to Edit at this configuration;
                    # its review's Apply confirms on the list, as Edit's.
                    "base_digest": configuration.active_configuration.digest,
                    "apply_url": reverse("admin:schedule_settings"),
                }
                | sends_view(sends, parameters, now, request.path),
            )
        _recheck(request, service)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
