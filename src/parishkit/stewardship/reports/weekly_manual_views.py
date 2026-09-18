"""An explicit configuration-bound confirmation for a new manual weekly report."""

from uuid import uuid4

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.delivery_views import (
    _command_scope,
    _database_error,
    _principal,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.security import private_response

from .weekly_manual import request_manual_report


class ManualReportForm(forms.Form):
    """No caller-selected recipients, report data, task identifiers or schedule."""

    command_id = forms.UUIDField(widget=forms.HiddenInput)
    configuration_id = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge = forms.BooleanField(
        label=_(
            "Generate a new manual report, even if it repeats "
            "previously reported items."
        )
    )


@require_http_methods(["GET", "POST"])
def request_report(request, campaign_id):
    """Keep the current session lock through command commit and reject stale forms."""
    try:
        service = runtime()
        principal = _principal(request, service.store, activity=True)
        runtime_row = SystemConfiguration.objects.select_related(
            "current_campaign__active_configuration"
        ).get()
        if campaign_id != runtime_row.current_campaign_id:
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
        form = ManualReportForm(
            request.POST if request.method == "POST" else None,
            initial={
                "command_id": uuid4(),
                "configuration_id": runtime_row.active_configuration_id,
            },
        )
        if request.method == "POST" and form.is_valid():
            with _command_scope(request, service, principal):
                task = request_manual_report(
                    service.store,
                    principal.identity,
                    campaign_id,
                    command_id=form.cleaned_data["command_id"],
                    configuration_id=form.cleaned_data["configuration_id"],
                )
            response = redirect("admin:background_task_page", task_id=task.run_id)
        else:
            response = render(
                request,
                "stewardship/weekly-manual.html",
                {
                    "form": form,
                    "campaign": runtime_row.current_campaign,
                    "mode": runtime_row.mode,
                },
                status=400 if request.method == "POST" else 200,
            )
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except DatabaseError as error:
        return _database_error(error)
    except (ConfigError, LimiterUnavailable, StorageInvariantError):
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Manual report command is already bound.\n", status=409)
