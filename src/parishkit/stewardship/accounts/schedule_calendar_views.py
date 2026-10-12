"""Campaign calendar: every send of the campaign's mail schedules, by date (#469).

A read-only page under Dates and mail schedules. It lists each send the
current schedule revisions make, in sending order: the Initial invitation,
each reminder, and every day or week of the Daily and Weekly Admin digests,
expanded with the scheduler's own evaluation (``SchedulePlan.page``). The
table of sends is the page's text equivalent; the page script
(``calendar-v1.js``) draws the same sends as month grids, bucketed by the
browser's local day, since every Admin time is browser-local.

Each send is a ``schedule_table.ScheduleRow`` with its own send time, so the
calendar shows the same names, emails, statuses and Edit links as the
scheduled emails table and cannot describe a schedule differently. Status
reads the same counts-only work summary, never Family data. Nothing here
changes anything: Edit opens the existing Edit scheduled email page.
"""

from dataclasses import replace
from datetime import UTC, datetime

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import filters

from .admin_editing import error_response, principal
from .authentication import runtime
from .content_views import _records
from .schedule_entry_views import ERRORS, _recheck
from .schedule_forms import schedule_order
from .schedule_preview import work_summary
from .schedule_reads import campaign_schedules, schedule_state
from .schedule_table import DIGESTS, schedule_rows
from .schedule_views import campaign_email_names

# ``SchedulePlan.page`` examines at most this many dates a call; a digest's
# sends are read page by page to its last one, however long the campaign.
PAGE_DATES = 1000
# The month grids' weekday headings, Sunday first as parish calendars are:
# the short name shown and the full name a screen reader reads.
WEEK = (
    (_("Sun"), _("Sunday")),
    (_("Mon"), _("Monday")),
    (_("Tue"), _("Tuesday")),
    (_("Wed"), _("Wednesday")),
    (_("Thu"), _("Thursday")),
    (_("Fri"), _("Friday")),
    (_("Sat"), _("Saturday")),
)


def calendar_sends(previous, campaign, summary, now, names=None):
    """Every send of the saved schedules, in sending order.

    ``previous``, ``campaign``, ``summary``, ``now`` and ``names`` are
    ``schedule_table.schedule_rows``'s. A one-time schedule is its own row; a
    digest becomes one row per send, each a copy of the digest's row with that
    send's time in ``due``, so it keeps the digest's name, email, status and
    Edit link. A digest send whose time has passed is "passed" instead: muted
    and not linked, like a schedule that has run, since editing the digest
    changes only its later sends. Sends at the same moment keep the table's
    sending order.
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
        sends.extend(
            replace(
                row,
                due=slot.due_at,
                next_due=None,
                status="passed" if slot.due_at <= now else row.status,
            )
            for slot in digest_slots(record["values"], campaign)
        )
    return sorted(sends, key=lambda send: (send.due, send.position))


def digest_slots(values, campaign):
    """Every send of one digest, read with the scheduler's own evaluation.

    Pages through ``SchedulePlan.page`` until it is exhausted, so no send is
    left out. It ends: a weekly digest stops at the campaign's close and a
    daily one the day after it.
    """
    plan = SchedulePlan.from_values(values, campaign)
    slots, cursor = [], None
    while True:
        page = plan.page(
            through=datetime.max.replace(tzinfo=UTC), after=cursor, limit=PAGE_DATES
        )
        slots.extend(page.slots)
        if page.exhausted:
            return slots
        cursor = page.cursor


@require_http_methods(["GET", "HEAD"])
def schedule_calendar(request, campaign_id):
    """Show the current campaign's sends by date, read-only (Administrator).

    It reads the applied configuration in one read-only snapshot, as the
    scheduled emails list does, and rechecks access after rendering so a
    revocation committed meanwhile is not hidden.
    """
    try:
        service = runtime()
        principal(request, service)
        filters(request.GET, allowed=set())
        with read_transaction():
            state, campaign, _editable = schedule_state(service, campaign_id)
            configuration = state[0]
            values = campaign.active_configuration.values
            sends = calendar_sends(
                campaign_schedules(configuration, campaign.pk),
                values,
                work_summary(campaign.pk),
                timezone.now(),
                campaign_email_names(_records(configuration, campaign.pk)),
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
                },
            )
        _recheck(request, service)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
