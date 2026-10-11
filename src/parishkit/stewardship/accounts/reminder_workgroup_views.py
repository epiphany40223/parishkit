"""The campaign's Reminder WorkGroup setting (#861): who gets no Reminders.

A campaign may name one ParishSoft Family WorkGroup. Families in it get no
Reminders; everything else about them is unchanged (portal access, codes,
links, the invitation, receipts, confirmations and every count). Staff add
or remove Families in ParishSoft, so the name stays editable while the
campaign is live, like its artwork. The page is edit, review, apply: the
name becomes the campaign's ``reminder_workgroup`` value only through a
confirmed configuration request. An empty name turns the exclusion off.

Review, apply and the change's status happen on the page itself (#750), as
on Campaign settings (#532): the form is ``data-table-sync``, so an answer
fills the review region under it while the form keeps the reader's typing
and takes only its hidden version. A refusal keeps the version that typing
came from (the one posted, or the one a refused Apply was reviewed at), so
a page changed elsewhere is refused until it is reloaded and can never
quietly propose undoing that change.
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
    REMINDER_WORKGROUP_NAME,
    WORKGROUP_NAME_LIMIT,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.source.workgroups import current_evidence
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation
from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    receipt_base,
    requested_status,
    review_region,
    reviewed_base,
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


def _page(request, configuration, campaign, form, *, status=200, **region):
    """Show the form, its in-place review region and what the newest refresh found.

    ``region`` is ``review_region``'s ``review``, ``receipt``, ``refusal`` or
    ``message``; the step indicator follows it through edit, review and
    apply (#196), all on this page.
    """
    name, evidence = current_evidence(campaign.active_configuration.values)
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/reminder-workgroup.html",
        {
            "campaign": campaign,
            "form": form,
            "current": name,
            "evidence": evidence,
            **review_region("reminder_workgroup", form, **region),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, configuration, campaign, form):
    """Sign the reviewed name without changing configuration yet.

    A stale page raises ``UserFacingStale``, which the caller shows in the
    review region; other refusals are the page again with their summary.
    """
    if not form.is_valid():
        return _page(request, configuration, campaign, form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    before = campaign.active_configuration.values.get(REMINDER_WORKGROUP)
    after = form.cleaned_data["name"] or None
    if after == before:
        # Review only a change (#921): refused in place, nothing requested.
        return _page(
            request,
            configuration,
            campaign,
            form,
            status=400,
            message=_("The %(name)s has not changed.")
            % {"name": _(REMINDER_WORKGROUP_NAME)},
        )
    patch = workgroup_patch(campaign, after)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    review = {
        # Drawn inside settings-review.html, above its Apply.
        "template": "stewardship/reminder-workgroup-review.html",
        "notes": [],
        "before": before,
        "after": after,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT + str(campaign.pk),
        ),
    }
    return _page(request, configuration, campaign, form, review=review)


@require_http_methods(["GET", "HEAD", "POST"])
def reminder_workgroup(request, campaign_id):
    """Read, review and confirm the campaign's Reminder WorkGroup name.

    Review, apply and the change's status all happen on this page (#750). A
    stale page or an out-of-date preview is refused in place: the page again
    (status 409) with the explanation in its review region. The only query
    is ``request``, naming the change Apply just made.
    """
    try:
        service = runtime()
        # Reading a change's status (the page's own quiet refresh once it is
        # applied) is passive, as Change status is: it never renews idle time.
        actor = principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        action, refusal, digest = None, None, None
        if request.method == "POST":
            action = form_action(request.POST, preview_fields={"name", "base_digest"})
            if action == "confirm":
                try:
                    response = confirm(
                        request,
                        service,
                        actor,
                        salt=SALT + str(campaign_id),
                        current_scope=lambda service: (
                            editable_configuration(service),
                            None,
                        ),
                        in_place=True,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    # Drawn below with the form kept at the version the
                    # change was reviewed at, so a page changed elsewhere
                    # needs a reload before the next Review
                    # (admin_editing.reviewed_base).
                    refusal = error.refusal
                    digest = reviewed_base(request.POST, SALT + str(campaign_id))
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            receipt = (
                requested_status(request, service, actor, request.GET)
                if request.method != "POST"
                else None
            )
            if receipt is not None:
                # In place, the form keeps the reader's typing
                # (admin_editing.receipt_base); a full load uses the current
                # version, matching the current name it draws.
                digest = receipt_base(request, receipt)
            configuration = editable_configuration(service)
            campaign = _campaign(configuration, campaign_id)
            form = WorkGroupForm(
                request.POST if action == "preview" else None,
                initial={
                    "name": campaign.active_configuration.values.get(
                        REMINDER_WORKGROUP, ""
                    ),
                    "base_digest": configuration.active_configuration.digest
                    if digest is None
                    else digest,
                },
            )
            if refusal is not None:
                response = _page(
                    request, configuration, campaign, form, status=409, refusal=refusal
                )
            elif action == "preview":
                try:
                    response = _preview(
                        request, service, actor, configuration, campaign, form
                    )
                except UserFacingStale as error:
                    # The bound form keeps the version that was posted.
                    response = _page(
                        request,
                        configuration,
                        campaign,
                        form,
                        status=409,
                        refusal=error.refusal,
                    )
            else:
                response = _page(
                    request, configuration, campaign, form, receipt=receipt
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
