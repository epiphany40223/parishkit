"""Emailed reports: the past daily and weekly Admin digests in one place (ADM-12.17).

A digest page used to be reachable only from its email. This page lists the
current campaign's daily reports and, for Administrators, its weekly reports,
newest first, each linking its page (admin-portal spec, "Menu groups",
Emailed reports). Staff see the daily reports only, as the report pages
already allow. Send a weekly report now is an Administrator action on this
page and returns here; ``?requested=<request>`` names the request just made,
so the page can link the report it produced, or its background task while
the report is still being prepared.

The list shows only identities and dates. Each report page still admits its
own campaign and checks the reader's access when it is opened.
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_GET

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .digest_models import DailyDigestReady, DailyDigestSnapshot
from .export_views import _principal
from .weekly_models import WeeklyDigestSnapshot, WeeklyManualRequest

# Each list shows this many of the newest reports: about two months of daily
# reports, more than one campaign's weekly reports.
SHOWN = 60


def _requested(request, campaign_id):
    """The weekly report request named by ``?requested=``, or None.

    Only a request for the current campaign is shown. Returns a dict with its
    background task and, once captured, the report it produced. Any other
    query string is refused by the caller.
    """
    value = request.GET.get("requested")
    if value is None:
        return None
    manual = (
        WeeklyManualRequest.objects.filter(pk=UUID(value), campaign_id=campaign_id)
        .values("pk", "task_id")
        .first()
    )
    if manual is None:
        return None
    report = (
        WeeklyDigestSnapshot.objects.filter(preparation_id=manual["pk"])
        .values_list("pk", flat=True)
        .first()
    )
    return {"task_id": manual["task_id"], "report_id": report}


@require_GET
def emailed_reports(request):
    """List the current campaign's emailed reports the reader may open."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if (
            set(request.GET) - {"requested"}
            or len(request.GET.getlist("requested")) > 1
        ):
            raise ValueError("Unknown emailed reports option.")
        administrator = "administrator" in principal.roles
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        ready = DailyDigestReady.objects.values_list("snapshot_id", flat=True)
        daily = (
            list(
                DailyDigestSnapshot.objects.filter(
                    campaign_id=campaign_id, pk__in=ready
                )
                .order_by("-observed_at", "-id")
                .values("pk", "through_date", "covered_dates", "observed_at")[:SHOWN]
            )
            if campaign_id
            else []
        )
        weekly = []
        if administrator and campaign_id:
            manual = set(
                WeeklyManualRequest.objects.filter(campaign_id=campaign_id).values_list(
                    "pk", flat=True
                )
            )
            weekly = [
                row | {"manual": row["preparation_id"] in manual}
                for row in WeeklyDigestSnapshot.objects.filter(campaign_id=campaign_id)
                .order_by("-observed_at", "-id")
                .values("pk", "preparation_id", "observed_at")[:SHOWN]
            ]
        requested = (
            _requested(request, campaign_id) if administrator and campaign_id else None
        )
        response = render(
            request,
            "stewardship/emailed-reports.html",
            {
                "campaign_id": campaign_id,
                "administrator": administrator,
                "daily": daily,
                "weekly": weekly,
                "requested": requested,
                "shown": SHOWN,
            },
        )
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except ValueError:
        return private_response("Invalid emailed reports request.\n", status=400)
    except (ConfigError, DatabaseError, LimiterUnavailable):
        return report_unavailable()
