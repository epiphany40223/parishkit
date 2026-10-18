"""The Campaign comparison report page (#1018, slices 3 and 5).

Administrators and Staff (``CAMPAIGN_REPORT``) compare the current campaign
with the imported past campaigns at the current campaign's last complete
day: headline tiles, the day-by-day, final-results and pledge-distribution
charts (``comparison_charts``), the pledge distribution tables (current
campaign included, since this page is inside the Admin portal; each
imported year's chart and table in a closed disclosure, #1161), every
number and the data notes (also closed by default). Its download form
(slice 6) posts the day shown to ``comparison_export_views``, which applies
the small-count protection before anything leaves the portal.

**Compare on day** moves the comparison day N with the one query parameter
the page takes, ``day=<integer>`` from 1 to the current comparison day (a
non-identifying value, as the reports spec's shared behavior allows). The
control is an in-place form (``ui-v1.js``) whose answer is this page again;
every figure that depends on N is in one region, ``comparison-report``. A
``day`` that is not such a number, or is past the comparison day, is
refused: the page answers 400 with the current comparison day's figures
and the error at the field, as a form refusal does. Any other parameter is
refused with the plain report-options error.

The view follows the other reports: admission, then the read under the
campaign's read guard (``campaign_response``), ``Cache-Control: no-store``,
and one ``campaign_comparison_viewed`` audit event started and then
succeeded or failed, with the comparison day as ``count`` and no figure.
"""

import re
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.presentation import number
from parishkit.stewardship.web.refusals import with_default_code
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.stat_tiles import shown

from .comparison import distribution_rows, table_rows, tiles
from .comparison_charts import day_charts, distribution_chart, final_charts
from .comparison_reads import DayOutOfRange, read_comparison
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .read_admission import admit_report_read

# A day as the Compare on day field sends it: a whole number without a sign
# or leading zero. Four digits are far more than any campaign has days.
DAY = re.compile(r"[1-9][0-9]{0,3}")
DAY_FIELD = "compare-day"


def day_error(latest):
    """The field error of a refused day, naming the days that can be chosen."""
    if latest is None:
        return "No day can be compared until day 1 of this campaign has ended."
    if latest == 1:
        return "Enter 1: only day 1 of this campaign has ended."
    return f"Enter a whole number from 1 to {number(latest)}."


def requested_day(query):
    """The ``day`` a query asks for: (day, refused?).

    Raises ``ValueError`` for a query with any other parameter or with more
    than one ``day``; a ``day`` that is not a whole number from 1 is
    (None, True), so the page can show the error at its field.
    """
    if not query:
        return None, False
    if set(query) != {"day"} or len(query.getlist("day")) != 1:
        raise ValueError("Invalid report options.")
    value = query["day"]
    if not DAY.fullmatch(value):
        return None, True
    return int(value), False


def audit(principal, campaign_id, outcome, day=None):
    """Record the view's outcome and comparison day, never a figure."""
    # An audit append takes no row locks, so it need not join the writers'
    # work order (as the Participation page's audit).
    context = {"outcome": outcome}
    if day is not None:
        context["count"] = day
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.CAMPAIGN_COMPARISON_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            subject_id=campaign_id,
            context=context,
        )


def headline_tiles(figures):
    """The page's headline figures as stat tiles (ui-conventions, #1540).

    ``figures`` are ``comparison.tiles()``: display text the PDF summary and
    the report emails share, so each tile takes its value as that text
    (``shown``). The average pledge's median so far comes first, then the
    last-year and average lines, each with its trend in words. A "—" value
    keeps its reason for the toggletip.
    """
    return [
        shown(
            figure["key"],
            figure["label"],
            figure["value"],
            unknown_reason=figure["reason"] or "",
            details=(
                _("Median so far: %(median)s") % {"median": figure["median"]}
                if figure["median"]
                else "",
                *figure["lines"],
            ),
        )
        for figure in figures
    ]


def page_context(comparison, principal, *, refused=False, mutable=True):
    """The template's context: display text only, formatted in Python.

    ``refused`` marks a page answering a ``day`` it could not show: the
    field shows the error and the page the current comparison day.
    ``mutable`` is whether the campaign admits a new export now; the
    download form is unavailable, with the reason, while it does not.
    """
    from parishkit.stewardship.archive.workbook import BAND_EDGES

    from .comparison import data_notes, years_label

    error = day_error(comparison.latest_day) if refused else None
    shared_notes, year_notes = data_notes(comparison.imported)
    return {
        "comparison": comparison,
        "day_error": error,
        # The base template's error summary, linking to the field.
        # Without a complete day there is no field to link to.
        "errors": [
            {
                "field_id": DAY_FIELD if comparison.latest_day else None,
                "message": error,
            }
        ]
        if error
        else [],
        "day_charts": day_charts(comparison),
        "final_charts": final_charts(comparison),
        "tiles": headline_tiles(tiles(comparison)),
        "rows": table_rows(comparison),
        "distributions": distributions(comparison, BAND_EDGES),
        # The data notes, the shared ones once and each year's own (#1161).
        "shared_notes": shared_notes,
        "year_notes": year_notes,
        # The years the tiles' averages cover: those with a value on day N
        # (a partial year after its last day is left out).
        "average_years": years_label(
            [year for year in comparison.imported if year.at_day is not None]
        ),
        "can_import": allows(principal, Capability.CONFIGURE)
        and allows(principal, Capability.FINANCIAL_DETAIL),
        # The download form (slice 6): a fresh one-time key per page, so a
        # reused form is the shared 409. Its files hold days, not action
        # times, so it asks for no time zone (#1161).
        "mutable": mutable,
        "request_key": uuid4(),
    }


def distributions(comparison, edges):
    """Each year's pledge-band table entry with its chart, current year first.

    The page shows one entry at a time, chosen by the section's year
    selector (#1221), so an entry carries its own chart
    (``distribution_chart``, None when it has no bands to draw), whether it
    is the current campaign's, and a ``key`` for its element ids and its
    selector option ("current", or the imported year). Empty without
    Financial stewardship.
    """
    if not comparison.financial:
        return []
    years = {
        year.label: year
        for year in (comparison.current, *comparison.imported)
        if year is not None
    }
    result = []
    for entry in distribution_rows(comparison, edges):
        year = years[entry["year"]]
        result.append(
            entry
            | {
                "chart": distribution_chart(year, edges),
                "current": year.current,
                "key": "current" if year.current else str(year.year),
            }
        )
    return result


@require_GET
def report(request, campaign_id):
    """Read and render the comparison under the campaign's read guard."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        try:
            day, refused = requested_day(request.GET)
        except ValueError:
            return private_response("Invalid report options.\n", status=400)
        audit(principal, campaign_id, Outcome.STARTED)
        finalized, shown = False, {"day": None, "refused": refused}

        def finish(completed):
            """Audit completion once, after the read transaction closes."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            outcome = Outcome.SUCCEEDED if completed else Outcome.FAILED
            if completed and shown["refused"]:
                # The page was shown, but not for the day asked for.
                outcome = Outcome.DENIED
            try:
                audit(principal, campaign_id, outcome, shown["day"])
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Fresh admission inside the guard: a role change ends the read."""
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Report access changed.")
            admit_report_read(campaign_id)
            holder["guard"] = guard

        holder = {}

        def content():
            """Only detached display values reach the template."""
            comparison = None
            if not shown["refused"]:
                try:
                    comparison = read_comparison(holder["guard"], campaign_id, day=day)
                except DayOutOfRange:
                    shown["refused"] = True
            if comparison is None:
                comparison = read_comparison(holder["guard"], campaign_id)
            shown["day"] = comparison.day
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            payload = render_to_string(
                "stewardship/campaign-comparison.html",
                page_context(
                    comparison, principal, refused=shown["refused"], mutable=mutable
                ),
                request=request,
            ).encode()
            return iter((payload,))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        if handed_off and shown["refused"]:
            # The content is rendered when the response is made, so the
            # refusal is known before any byte is sent.
            response.status_code = 400
            # The page is this view's own refusal, so the security boundary
            # keeps it instead of replacing it with the bare error text.
            response.stewardship_safe_error = True
            # The refusal log's code for this page's own refusal (#1444).
            with_default_code(response)
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, FactUnavailable, StorageInvariantError):
        return report_unavailable()
    except ValueError as error:
        # A value the read could not shape is a defect every retry
        # reproduces: record it for operators before the 503 page.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)
