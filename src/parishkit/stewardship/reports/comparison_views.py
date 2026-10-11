"""The Campaign comparison report page (#1018, slice 3).

Administrators and Staff (``CAMPAIGN_REPORT``) compare the current campaign
with the imported past campaigns at the current campaign's last complete
day: headline tiles, the pledge distribution (current campaign included,
since this page is inside the Admin portal), every number and the data
notes. Charts (slice 5), the Compare on day control (slice 5) and downloads
(slice 6) come later, so the page takes no query parameter yet.

The view follows the other reports: admission, then the read under the
campaign's read guard (``campaign_response``), ``Cache-Control: no-store``,
and one ``campaign_comparison_viewed`` audit event started and then
succeeded or failed, with the comparison day as ``count`` and no figure.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .comparison import distribution_rows, table_rows, tiles
from .comparison_reads import read_comparison
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .read_admission import admit_report_read


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


def page_context(comparison, principal):
    """The template's context: display text only, formatted in Python."""
    from parishkit.stewardship.archive.workbook import BAND_EDGES

    from .comparison import years_label

    return {
        "comparison": comparison,
        "tiles": tiles(comparison),
        "rows": table_rows(comparison),
        "distributions": distribution_rows(comparison, BAND_EDGES)
        if comparison.financial
        else [],
        # The years the tiles' averages cover: those with a value on day N
        # (a partial year after its last day is left out).
        "average_years": years_label(
            [year for year in comparison.imported if year.at_day is not None]
        ),
        "can_import": allows(principal, Capability.CONFIGURE)
        and allows(principal, Capability.FINANCIAL_DETAIL),
    }


@require_GET
def report(request, campaign_id):
    """Read and render the comparison under the campaign's read guard."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        if request.GET:
            # Compare on day (``day=``) arrives with the charts (slice 5).
            return private_response("Invalid report options.\n", status=400)
        audit(principal, campaign_id, Outcome.STARTED)
        finalized, shown = False, {"day": None}

        def finish(completed):
            """Audit completion once, after the read transaction closes."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    shown["day"],
                )
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
            comparison = read_comparison(holder["guard"], campaign_id)
            shown["day"] = comparison.day
            payload = render_to_string(
                "stewardship/campaign-comparison.html",
                page_context(comparison, principal),
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
