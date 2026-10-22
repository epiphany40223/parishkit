"""Campaign calendar: every send of the campaign's mail schedules, by date (#469).

A page under Dates and mail schedules that changes nothing itself. It lists
each send the current schedule revisions make, in sending order: the
Initial invitation, each reminder, and every day or week of the Daily and
Weekly Admin digests, expanded with the scheduler's own evaluation
(``SchedulePlan.page``). A report email's passed times show only the sends
it made, never the rule's (Administrator, R43; ``report_calendar_sends``).
The table of sends is the page's text equivalent;
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
# Every send a report made is listed (``report_sends`` keeps a page's worth
# by default); a report sends at most daily, so this bounds nothing real.
REPORT_SENDS_LISTED = 10_000
# A send a report made, by its Past sends result (``report_email_list``),
# as the calendar's status.
REPORT_RESULTS = {
    "sent": "sent",
    "failed": "report_failed",
    "uncertain": "uncertain",
    "nothing": "nothing",
    "nobody": "nobody",
    "sending": "sending",
}


def report_calendar_sends(configuration, campaign_id, campaign, now, position):
    """Every report email send of the campaign, as calendar rows (#1786, R43).

    ``configuration`` is the applied system configuration, ``campaign`` the
    campaign row. A time that has passed shows only what a report actually
    sent (Administrator, R43): the sends Past sends lists
    (``report_email_list.report_sends``, in the configuration's mode), each
    at the time it was made with its result, muted and not linked. The
    rule's sends are listed only after ``now``, expanded by the reports' own
    evaluator with skips and pauses set aside so a skipped or paused send is
    still listed, muted ("skipped", "paused"); a send still to come links to
    its report's Edit page naming the send (``?send=``), which ticks that
    send's box for Skip (or Send) selected sends. An open-ended report is
    listed until ``REPORT_HORIZON`` after the campaign's last day.
    ``position`` is where the reports follow the Family mail in sending
    order. A catch-up the scheduler still owes is one more row
    (``owes_catch_up``).
    """
    from parishkit.stewardship.reports.report_email_list import (
        plan,
        report_records,
        report_sends,
    )

    values = campaign.active_configuration.values
    end = date.fromisoformat(values["end_date"]) + REPORT_HORIZON
    through = resolve_local(datetime.combine(end, time()), values["timezone"])
    records = report_records(configuration, campaign_id)
    made = report_sends(
        campaign_id,
        configuration.mode,
        [record["id"] for record in records],
        limit=REPORT_SENDS_LISTED,
    )
    rows = []
    for offset, record in enumerate(records):
        report = record["values"]
        link = reverse("admin:report_email_edit", args=[record["id"]])
        row = ScheduleRow(
            id=record["id"],
            label=report["name"],
            kind="report",
            subject=report["subject"],
            status="upcoming",
            position=position + offset,
            email=str(_("Report email")),
            link=link,
        )
        sends = made.get(record["id"], [])
        rows.extend(
            replace(row, status=REPORT_RESULTS[send["result"]], due=send["made_at"])
            for send in sends
        )
        if owes_catch_up(record, campaign, configuration.mode, now, sends):
            rows.append(replace(row, status="missed", due=now))
        free = plan(report | {"skip_dates": [], "paused_ranges": []}, values)
        pauses = [
            (
                datetime.fromisoformat(entry["start"]),
                entry["end"] and datetime.fromisoformat(entry["end"]),
            )
            for entry in report["paused_ranges"]
        ]
        for slot in free.page(through=through, limit=1000).slots:
            # A passed time shows only the sends made, never the rule's.
            if slot.due_at <= now:
                continue
            paused = any(
                start <= slot.due_at and (stop is None or slot.due_at < stop)
                for start, stop in pauses
            )
            rows.append(
                replace(
                    row,
                    status="skipped"
                    if slot.key in report["skip_dates"]
                    else "paused"
                    if paused
                    else "upcoming",
                    due=slot.due_at,
                    slot=slot.key,
                )
            )
    return rows


def owes_catch_up(record, campaign, mode, now, sends):
    """Whether the scheduler owes ``record`` a send for a time already passed.

    A report created after its From date (or one whose send is late) has
    past slots of its rule, not skipped or paused, that nothing covers; the
    scheduler coalesces them into one send as soon as it runs (report-emails
    spec, "Catch-up, recovery and holds"). The check is the scheduler's own
    (``report_email_ownership.covered_slots``), paged through every past
    slot as the scheduler pages them. While a send of the report is being
    made (``sends``' "sending") that send is the catch-up, already listed.

    It mirrors the scheduler's uncovered-slot half only, not its ``DUE``
    query (a pending occurrence with no preparation, or a replaced
    revision's sends to coalesce): that needs the Testing rehearsal epoch,
    which only the scheduler's credential scope gives, and such a send is
    taken by the scheduler's next pass.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
    from parishkit.stewardship.reports.report_email_list import plan
    from parishkit.stewardship.reports.report_email_ownership import covered_slots

    if any(send["result"] == "sending" for send in sends):
        return False
    rule = plan(record["values"], campaign.active_configuration.values)
    page = rule.page(through=now, limit=1000)
    # A rule with no past slot owes nothing: no further query.
    if not page.slots and page.exhausted:
        return False
    definition = ScheduleDefinition.objects.filter(pk=record["id"]).first()
    if definition is None or definition.current_revision_id is None:
        return False
    cycle = campaign.production_cycle if mode == "production" else 0
    while True:
        keys = {slot.key for slot in page.slots}
        if keys - covered_slots(definition, mode, cycle, keys):
            return True
        if page.exhausted:
            return False
        page = rule.page(through=now, after=page.cursor, limit=1000)


def is_past(send, now):
    """Whether ``send``'s time has passed and it is no longer sending.

    A report's owed catch-up ("missed", listed at ``now``) is still to come,
    so the default Upcoming view keeps it (R43); a Family send that passed
    without sending ("missed" too) is past.
    """
    if send.kind == "report" and send.status == "missed":
        return False
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
                    configuration, campaign.pk, campaign, now, len(family)
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
