"""An explicit configuration-bound confirmation for a new manual weekly report."""

from urllib.parse import urlencode
from uuid import uuid4

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.jobs.delivery_views import (
    _command_scope,
    _database_error,
    _principal,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .weekly_manual import request_manual_report


class ManualReportForm(forms.Form):
    """No caller-selected recipients, report data, task identifiers or schedule."""

    command_id = forms.UUIDField(widget=forms.HiddenInput)
    configuration_id = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        label=_(
            "Generate a new manual report, even if it repeats "
            "previously reported items."
        ),
    )


def _has_weekly_schedule(campaign_id):
    """The manual report is a weekly digest; SQL refuses one without a schedule.

    Checking first lets the page explain the missing prerequisite instead of
    showing the generic delivery refusal the database guard would produce.
    """
    return ScheduleDefinition.objects.filter(
        campaign_id=campaign_id,
        kind="weekly_digest",
        current_revision_id__isnull=False,
    ).exists()


def _blocked(request, campaign, reason, status):
    """Explain why no manual report can be queued, and where to fix it."""
    response = render(
        request,
        "stewardship/weekly-manual.html",
        {"form": None, "campaign": campaign, "blocker": reason},
        status=status,
    )
    response["Cache-Control"] = "no-store"
    return response


def _guard_refused(error):
    """Whether the database guard (not an outage) refused the manual request."""
    return isinstance(error, IntegrityError) and getattr(
        error.__cause__, "sqlstate", None
    ) in {"23514", "23505"}


@require_http_methods(["GET", "POST"])
def request_report(request, campaign_id):
    """Keep the current session lock through command commit and reject stale forms."""
    try:
        service = runtime()
        principal = _principal(request, service.store, activity=True)
        runtime_row = SystemConfiguration.objects.select_related(
            "current_campaign__active_configuration"
        ).get()
        # The route passes the current campaign's id (None when there is
        # none); recheck it against the row read under this request.
        if campaign_id is None or campaign_id != runtime_row.current_campaign_id:
            raise PermissionError("Manual reporting requires the current campaign.")
        if request.GET or (
            request.method == "POST"
            and (
                set(request.POST) - {"csrfmiddlewaretoken"}
                != {"command_id", "configuration_id", "acknowledge"}
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
            )
        ):
            return private_response("Invalid manual report request.\n", status=400)
        campaign = runtime_row.current_campaign
        if not _has_weekly_schedule(campaign_id):
            return _blocked(request, campaign, "no_schedule", 409)
        form = ManualReportForm(
            request.POST if request.method == "POST" else None,
            initial={
                "command_id": uuid4(),
                "configuration_id": runtime_row.active_configuration_id,
            },
        )
        if request.method == "POST" and form.is_valid():
            with _command_scope(request, service, principal):
                request_manual_report(
                    service.store,
                    principal.identity,
                    campaign_id,
                    command_id=form.cleaned_data["command_id"],
                    configuration_id=form.cleaned_data["configuration_id"],
                )
            # Back to Emailed reports, which links the report this request
            # produces (or its background task until then) (ADM-12.17).
            response = redirect(
                reverse("admin:emailed_reports")
                + "?"
                + urlencode({"requested": form.cleaned_data["command_id"]})
            )
        else:
            response = render(
                request,
                "stewardship/weekly-manual.html",
                {
                    "form": form,
                    "campaign": campaign,
                    "mode": runtime_row.mode,
                },
                status=400 if request.method == "POST" else 200,
            )
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except DatabaseError as error:
        if _guard_refused(error):
            # A digest already in progress or awaiting review, or a scope that
            # changed since the form opened: explain rather than show the
            # generic delivery refusal.
            return _blocked(request, None, "refused", 409)
        return _database_error(error)
    except (ConfigError, LimiterUnavailable, StorageInvariantError):
        return report_unavailable()
    except ValueError:
        return private_response("Manual report command is already bound.\n", status=409)
