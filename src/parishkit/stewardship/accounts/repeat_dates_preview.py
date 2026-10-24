"""The repeat rules' live date lists, drawn by the shared schedule preview (#2092).

New scheduled email's Sends Repeating and the date-change review's New
schedule pane (Sends Repeating) list, as the Admin types, every date their
repeat rule gives and why any of them will not be added (admin-portal "New
and Edit scheduled email"). The page script works out those dates and their
reasons (``repeatDates`` in ui-v1.js), since they depend on fields not yet
posted: the pane's proposed campaign dates and the rows being edited. It
posts them here, and this view draws them through the one schedule preview
component (``occurrence_preview.build`` and ``components/occurrences.html``;
ui-conventions "Schedule previews"), so the list reads like every other
preview: grouped by the browser's days, the send time as a browser-local
instant, a date not added struck through with its reason in words, and the
shared daylight-saving note.

The answer is read-only: nothing is checked against saved schedules or
saved, and no request or audit entry is recorded. The server still checks
every posted reminder on Review, as it checks any reminder.
"""

from datetime import date

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render
from django.utils import timezone
from django.utils.dates import MONTHS
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.occurrence_preview import Occurrence, build, wall_shift
from parishkit.stewardship.web.dates import UnknownZone, browser_instant, browser_zone
from parishkit.stewardship.web.refusals import unexpected_fields

from .admin_editing import error_response, principal
from .authentication import runtime
from .content_forms import EMAIL_LABELS
from .schedule_changes import NO_BROWSER_ZONE
from .schedule_entry_views import ERRORS, REPEAT_LIMIT, SCHEDULE_LIMIT, _recheck

# Why the page script will not add a date, by the code it posts (the
# ``reason`` of repeatDates' items, ui-v1.js). The words are the server's,
# so the list has one wording. "batch" names the server's REPEAT_LIMIT;
# "room" the page's own limit of schedules (its form set's maximum, posted
# as ``limit``), which is what the page checked, at most the server's.
REASONS = {
    "outside": _("not added: outside the campaign dates"),
    "passed": _("not added: its time has passed"),
    "initial": _("not added: not after the initial invitation"),
    "taken": _("not added: another email is already scheduled then"),
    "batch": _("not added: over the limit of %(count)d reminders at a time"),
    "room": _("not added: over the limit of %(count)d schedules"),
}
SKIPPED = _("%(month)s %(year)d is skipped: it has no day %(day)d.")
# A rule spans at most a year (recurrence.RULE_ERRORS "span"), so it gives
# at most 366 dates and skips at most 13 months; anything longer is not
# from the page.
MOST_DATES = 400
MOST_MONTHS = 24
FIELDS = {"csrfmiddlewaretoken", "zone", "campaign_zone", "time", "date", "reason"}
FIELDS |= {"skipped", "day", "limit"}
MULTIPLE = {"date", "reason", "skipped"}


def preview(dates, *, zone, campaign_zone, clock, now, limit=SCHEDULE_LIMIT):
    """The schedule preview's context for a repeat rule's dates.

    ``dates`` is ``(date, reason code)`` pairs in the rule's order, each a
    browser-local calendar day with "" for a date that will be added.
    ``clock`` is the send time (``HH:MM``) in the browser's ``zone``, or
    None while it does not read: the lines then name only their day. Each
    send is converted on its own, as the server converts each posted
    reminder (``schedule_local.to_campaign``), and its daylight-saving shift
    is judged on the campaign-local wall time in ``campaign_zone``, as the
    review judges the saved reminder, so the list and the review agree. An
    unusable ``zone`` gives the unavailable state. ``limit`` is the limit
    of schedules the "room" reason names.
    """
    try:
        browser_zone(zone)
    except UnknownZone:
        return build(
            [], schedule_zone=campaign_zone, now=now, unavailable=NO_BROWSER_ZONE
        )
    schedule = browser_zone(campaign_zone)
    label = str(EMAIL_LABELS["reminder"])
    lines = []
    for day, code in dates:
        at = shift = None
        if clock is not None:
            at = browser_instant(day.isoformat(), clock, zone)
            local = at.astimezone(schedule)
            shift = wall_shift(
                local.date(), local.strftime("%H:%M:%S"), at, campaign_zone
            )
        count = REPEAT_LIMIT if code == "batch" else limit
        reason = str(REASONS[code]) % {"count": count} if code else None
        lines.append(
            Occurrence(
                day,
                at,
                label,
                kind="reminder",
                struck=reason is not None,
                reason=reason,
                shift=None if reason else shift,
            )
        )
    return build(lines, shown_zone=zone, schedule_zone=campaign_zone, now=now)


def skipped_months(months, day):
    """One line per month the monthly rule skips, for under the list.

    ``months`` are ``YYYY-MM`` texts and ``day`` the rule's day of the
    month. A skipped month is no send, so it is not one of the preview's
    lines; the feature names it after them.
    """
    found = []
    for month in months:
        year, number = (int(part) for part in month.split("-"))
        date(year, number, 1)  # Refuses a month that is not one.
        found.append(str(SKIPPED % {"month": MONTHS[number], "year": year, "day": day}))
    return found


def _read(post):
    """The posted dates, reasons, months and times, or ``ValueError``.

    Only the page script's own fields, each once except the lists, and the
    lists' lengths agree.
    """
    if not set(post) <= FIELDS or any(
        len(values) != 1 and name not in MULTIPLE for name, values in post.lists()
    ):
        raise unexpected_fields(post, allowed=FIELDS, multiple=MULTIPLE)
    days, codes = post.getlist("date"), post.getlist("reason")
    months = post.getlist("skipped")
    if (
        len(days) != len(codes)
        or len(days) > MOST_DATES
        or len(months) > MOST_MONTHS
        or not set(codes) <= {"", *REASONS}
    ):
        raise ValueError("The repeat dates could not be read.")
    # browser_instant refuses a time in any other form than the time
    # entry's canonical one (HH:MM, or HH:MM:SS for a saved time).
    clock = post.get("time", "") or None
    day = int(post.get("day") or 0) if months else 0
    limit = int(post.get("limit") or SCHEDULE_LIMIT)
    if not 1 <= limit <= SCHEDULE_LIMIT:
        raise ValueError("The limit of schedules could not be read.")
    if months and not 1 <= day <= 31:
        raise ValueError("The day of the month could not be read.")
    return {
        "dates": [
            (date.fromisoformat(text), code)
            for text, code in zip(days, codes, strict=True)
        ],
        "months": months,
        "day": day,
        "limit": limit,
        "clock": clock,
        "zone": post.get("zone", ""),
        # The pane posts the zone being proposed; it must be a real one.
        "campaign_zone": browser_zone(post.get("campaign_zone", "")).key,
    }


TEMPLATE = "stewardship/repeat-dates-preview.html"


def fragment(post, *, now):
    """The list fragment's context for the page script's ``post``.

    The view's whole answer once it has admitted the request; the browser
    tests' fixture server answers with it too, so they draw the list the
    view draws.
    """
    posted = _read(post)
    return {
        "preview": preview(
            posted["dates"],
            zone=posted["zone"],
            campaign_zone=posted["campaign_zone"],
            clock=posted["clock"],
            now=now,
            limit=posted["limit"],
        ),
        "skipped": skipped_months(posted["months"], posted["day"]),
    }


@require_POST
def repeat_dates_preview(request, campaign_id):
    """A repeat rule's live date list (read only), drawn by the shared preview.

    The page script posts the rule's dates with why any is not added, the
    send time and both zones; the answer is the list's fragment, which it
    draws in place under the list's heading. It needs the pages' own
    Configure capability and renews no idle time.
    """
    try:
        service = runtime()
        principal(request, service, read_only=True)
        if request.GET:
            raise unexpected_fields(request.GET, allowed=set())
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        context = fragment(request.POST, now=timezone.now())
        response = render(request, TEMPLATE, context)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)
