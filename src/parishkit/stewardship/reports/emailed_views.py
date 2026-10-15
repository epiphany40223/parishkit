"""Emailed reports: the past daily and weekly Admin digests in one place (ADM-12.17).

A digest page used to be reachable only from its email. This page lists the
current campaign's daily reports and, for Administrators, its weekly reports,
newest first, each linking its page (admin-portal spec, "Menu groups",
Emailed reports). Staff see the daily reports only, as the report pages
already allow. Send a weekly report now is an Administrator action at the top
of this page, above both lists, so a long list of daily reports never hides
it. It is always shown to an Administrator, and unavailable, with a hint and
a link to Dates and mail schedules, while the campaign has no Weekly Admin
digest schedule, which the report needs; the template's ``menu_link`` greys
that link with the menu's reason while the menu does (an archived campaign,
#1261). It returns here;
``?requested=<request>`` names the request just made, so the page can link
the report it produced, or its background task while the report is still
being prepared.

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
from .weekly_manual_views import _has_weekly_schedule
from .weekly_models import WeeklyDigestSnapshot, WeeklyManualRequest

# Each list shows this many of the newest reports: about two months of daily
# reports, more than one campaign's weekly reports.
SHOWN = 60


def _requested(value, campaign_id):
    """The current campaign's manual weekly request ``value``, or None.

    ``value`` is the request's key: a UUID, or the page's ``?requested=``
    text, which raises ValueError when malformed. Returns a dict with its
    background task and, once captured, the report it produced; a key that
    is not a request of ``campaign_id`` answers None.
    """
    manual = (
        WeeklyManualRequest.objects.filter(pk=UUID(str(value)), campaign_id=campaign_id)
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


def listing(campaign_id, *, administrator, requested=None):
    """The page's lists for ``campaign_id``, without a request (ADM-11).

    Shared by the page and ``pk-admin digest list``. ``daily`` is the newest
    ready daily reports; ``weekly`` (Administrators only, else empty) the
    newest weekly reports, each marked ``manual`` when an Administrator
    requested it; ``requested`` the manual request named by ``requested``
    (see ``_requested``), Administrators only; ``weekly_schedule`` whether
    Send a weekly report now is available. With no campaign every list is
    empty.
    """
    if campaign_id is None:
        return {"daily": [], "weekly": [], "requested": None, "weekly_schedule": False}
    ready = DailyDigestReady.objects.values_list("snapshot_id", flat=True)
    daily = list(
        DailyDigestSnapshot.objects.filter(campaign_id=campaign_id, pk__in=ready)
        .order_by("-observed_at", "-id")
        .values("pk", "through_date", "covered_dates", "observed_at")[:SHOWN]
    )
    if not administrator:
        return {
            "daily": daily,
            "weekly": [],
            "requested": None,
            "weekly_schedule": False,
        }
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
    return {
        "daily": daily,
        "weekly": weekly,
        "requested": None if requested is None else _requested(requested, campaign_id),
        # Send a weekly report now sends an extra Weekly Admin digest, so it
        # is unavailable until that schedule exists (its page refuses too).
        "weekly_schedule": _has_weekly_schedule(campaign_id),
    }


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
        lists = listing(
            campaign_id,
            administrator=administrator,
            requested=request.GET.get("requested"),
        )
        response = render(
            request,
            "stewardship/emailed-reports.html",
            {
                "campaign_id": campaign_id,
                "administrator": administrator,
                **lists,
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
