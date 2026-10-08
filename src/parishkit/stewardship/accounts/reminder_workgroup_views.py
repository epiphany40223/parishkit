"""The campaign's Reminder WorkGroup setting (#861): who gets no Reminders.

A campaign may name one ParishSoft Family WorkGroup. Families in it get no
Reminders; everything else about them is unchanged (portal access, codes,
links, the invitation, receipts, confirmations and every count). Staff add
or remove Families in ParishSoft, so the name stays editable while the
campaign is live, like its artwork. The page is edit, review, apply: the
name becomes the campaign's ``reminder_workgroup`` value only through a
confirmed configuration request. An empty name turns the exclusion off.
"""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import (
    REMINDER_WORKGROUP,
    WORKGROUP_NAME_LIMIT,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.source.workgroups import current_evidence
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from . import admin_navigation
from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-reminder-workgroup-preview-v1:"


class WorkGroupForm(forms.Form):
    """The WorkGroup name, trimmed; empty turns the setting off."""

    name = forms.CharField(
        label=_("ParishSoft Family WorkGroup"),
        max_length=WORKGROUP_NAME_LIMIT,
        required=False,
        help_text=_(
            "Type the name of a Family WorkGroup as ParishSoft shows it, for "
            "example “Active: Stewardship 2027”; capital letters and spaces "
            "at the ends do not matter. Families in that WorkGroup get no "
            "Reminder emails. They still get the invitation, receipts and "
            "confirmations, keep their code and link, and count in every "
            "report. Leave it empty to send Reminders to every eligible "
            "Family that has not responded."
        ),
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )


def _campaign(configuration, campaign_id):
    """Only the current, unarchived campaign's setting is editable."""
    if campaign_id != configuration.current_campaign_id:
        raise LookupError("Only the current campaign's setting can be changed.")
    campaign = (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=campaign_id)
        .first()
    )
    if campaign is None or campaign.state == "archived":
        raise LookupError("Campaign is unavailable.")
    return campaign


def workgroup_patch(campaign, name):
    """A campaign update setting the WorkGroup name, or clearing it when empty."""
    return [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {REMINDER_WORKGROUP: name or None},
        }
    ]


def _page(request, configuration, campaign, form, *, status=200):
    """Show the current name, what the newest refresh found, and the form."""
    name, evidence = current_evidence(campaign.active_configuration.values)
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/reminder-workgroup.html",
        {
            "campaign": campaign,
            "form": form,
            "current": name,
            "evidence": evidence,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, configuration, campaign, form):
    """Sign the reviewed name without changing configuration yet."""
    if not form.is_valid():
        return _page(request, configuration, campaign, form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the setting before changing it.")
    before = campaign.active_configuration.values.get(REMINDER_WORKGROUP)
    after = form.cleaned_data["name"] or None
    admin_navigation.place(request, flow="change", step="review")
    if after == before:
        return render(
            request,
            "stewardship/reminder-workgroup-preview.html",
            {"unchanged": True, "campaign": campaign},
        )
    patch = workgroup_patch(campaign, after)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())
    return render(
        request,
        "stewardship/reminder-workgroup-preview.html",
        {
            "campaign": campaign,
            "before": before,
            "after": after,
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + str(campaign.pk),
            ),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def reminder_workgroup(request, campaign_id):
    """Read, preview and confirm the campaign's Reminder WorkGroup name."""
    try:
        service = runtime()
        actor = principal(request, service)
        if request.method == "POST":
            action = form_action(request.POST, preview_fields={"name", "base_digest"})
            if action == "confirm":
                response = confirm(
                    request,
                    service,
                    actor,
                    salt=SALT + str(campaign_id),
                    current_scope=lambda service: (
                        editable_configuration(service),
                        None,
                    ),
                )
                response["Cache-Control"] = "no-store"
                return response
        else:
            filters(request.GET, allowed=set())
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            configuration = editable_configuration(service)
            campaign = _campaign(configuration, campaign_id)
            form = WorkGroupForm(
                request.POST if request.method == "POST" else None,
                initial={
                    "name": campaign.active_configuration.values.get(
                        REMINDER_WORKGROUP, ""
                    ),
                    "base_digest": configuration.active_configuration.digest,
                },
            )
            response = (
                _preview(request, service, actor, configuration, campaign, form)
                if request.method == "POST"
                else _page(request, configuration, campaign, form)
            )
        # Recheck access after the observation ends, so a read-only snapshot
        # cannot hide a revocation committed while the page rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
