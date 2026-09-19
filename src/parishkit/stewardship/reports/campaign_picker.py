"""Separately guarded campaign labels keep unrelated lifecycle races off reports."""

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .export_views import SAFE_FAILURES, _principal
from .read_admission import admit_report_read
from .workspace import ReportQuery
from .workspace_views import campaign_ids


@require_GET
def picker(request):
    """Guard every listed campaign without widening a single-report read's scope.

    Names remain campaign-owned data, not a reason to bypass read admission.
    Concurrent lifecycle changes may require retry here but cannot stop an
    unrelated selected report whose links and guard name only its own UUID.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        query = ReportQuery.parse(request.GET)
        identities = campaign_ids()
        if not identities:
            response = render(request, "stewardship/report-empty.html")
            response["Cache-Control"] = "no-store"
            return response

        def fresh(guard):
            """Current report authority covers only admitted retained campaigns."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Report access changed.")
            for identifier in identities:
                admit_report_read(identifier)

        def content():
            """Load labels only after every listed identity is guarded."""
            rows = (
                Campaign.objects.filter(pk__in=identities)
                .select_related("active_configuration")
                .order_by("-created_at", "id")
            )
            choices = [
                {
                    "name": row.active_configuration.name,
                    "url": query.url(row.pk, page=1),
                }
                for row in rows
            ]
            return iter(
                (
                    render_to_string(
                        "stewardship/report-campaigns.html",
                        {"campaigns": choices},
                        request=request,
                    ).encode(),
                )
            )

        return campaign_response(
            request, identities, authorize=fresh, open_content=content
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)
