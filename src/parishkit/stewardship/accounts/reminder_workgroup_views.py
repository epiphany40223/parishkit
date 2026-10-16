"""The campaign's Reminder WorkGroup setting (#861): who gets no Reminders.

A campaign may name one ParishSoft Family WorkGroup. Families in it get no
Reminders; everything else about them is unchanged (portal access, codes,
links, the invitation, receipts, confirmations and every count). Staff add
or remove Families in ParishSoft, so the name stays editable while the
campaign is live, like its artwork. The page is edit, review, apply: the
name becomes the campaign's ``reminder_workgroup`` value only through a
confirmed configuration request. An empty name turns the exclusion off.

The form's Save button posts its Review without a page load and is answered
with a confirmation in the shared review dialog (UX conventions, "Review
dialog", #750, #1356): the name is the form's one field and the current one
is under "Now" on the page, so the dialog repeats neither and says in one
sentence what Apply does. The signed preview is posted back here by the
dialog's Apply, whose answer is the change's status, which the dialog
follows; once the change is applied it redraws the page's regions from the
page's own address (the form at the applied version and what the newest
refresh found). A Review refused for the name's value is the page again
with status 400, whose errors the page script marks on the form, and the
dialog stays shut. Any other refusal (a stale page, an out-of-date preview,
the unchanged safeguard) is a plain refusal shown in the dialog, while the
form keeps the reader's name at the version it came from, so a page changed
elsewhere is refused until it is reloaded and can never quietly propose
undoing that change.
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
from parishkit.stewardship.source.workgroups import (
    applied_evidence,
    current_evidence,
    snapshot_evidence,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    stale_page,
    with_default_code,
)

from . import admin_navigation
from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-reminder-workgroup-preview-v1:"


class WorkGroupForm(AdminForm):
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
    """Show the form and what the newest refresh found.

    A Review refused for the name's value (400) draws its errors' summary,
    which the page script places above the heading. The form is drawn at
    the applied version: a refused Review reaches here only after its posted
    version was found to be that one.
    """
    name, evidence = current_evidence(campaign.active_configuration.values)
    # What Reminders skip now (#1305): while a rename waits for a refresh,
    # the previous WorkGroup's Families, which the Now panel says.
    applied = applied_evidence(campaign.active_configuration.values)
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/reminder-workgroup.html",
        {
            "campaign": campaign,
            "form": form,
            "current": name,
            "evidence": evidence,
            # Review and the review dialog's Apply post here.
            "page_url": request.path,
            # Once a change is applied, the dialog redraws the page's
            # regions from its own address.
            "refresh_url": request.path,
            "errors": summary_errors(form) if status == 400 else [],
            "applied": applied,
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _preview(request, service, actor, configuration, campaign, form):
    """The review dialog's confirmation, the page refused (400), or a refusal.

    Signs the reviewed name without changing configuration yet. A stale page
    and an unchanged name raise their refusals, which the dialog shows.
    """
    if form.data.get("base_digest") != configuration.active_configuration.digest:
        raise stale_page()
    # The version check comes first: a refused page is drawn at the current
    # version (``_page``), which is then known to be the posted one.
    if not form.is_valid():
        return _page(request, configuration, campaign, form, status=400)
    review = review_change(
        service, actor, configuration, campaign, form.cleaned_data["name"]
    )
    if review is None:
        # Save is unavailable without a change (data-require-change, #921),
        # so only a request that bypasses the page script meets this.
        raise UserFacingError(
            _("The %(name)s has not changed.") % {"name": _(REMINDER_WORKGROUP_NAME)},
            fix=_("Nothing was changed."),
        )
    # The review says when the change applies (#1305): that depends on the
    # newest refresh's evidence, whatever the setting is now.
    return render(
        request,
        "stewardship/reminder-workgroup-review.html",
        {"review": review, "recorded": snapshot_evidence()},
    )


def review_change(service, actor, configuration, campaign, name):
    """The page's review of setting the WorkGroup to ``name``, or None.

    Request-free, so the page's Review and ``workgroup preview`` (ADM-11)
    build the same review and the same signed token (the page's salt, bound
    to the campaign). ``name`` is the form's cleaned value; empty turns the
    setting off. None means nothing would change. A configuration that moved
    on since ``configuration`` was read raises ``UserFacingStale``; a
    candidate the configuration refuses raises ``ConfigError``.
    """
    before = campaign.active_configuration.values.get(REMINDER_WORKGROUP)
    after = name or None
    if after == before:
        return None
    patch = workgroup_patch(campaign, after)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    return {
        # The dialog's confirmation says only whether the change sets a
        # WorkGroup or clears it: the name is the form's, and the current one
        # is under "Now". ``before`` and ``after`` are for ``workgroup
        # preview`` (ADM-11).
        "clears": after is None,
        "before": before,
        "after": after,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT + str(campaign.pk),
        ),
    }


@require_http_methods(["GET", "HEAD", "POST"])
def reminder_workgroup(request, campaign_id):
    """Read the campaign's Reminder WorkGroup name, confirm a change, apply it.

    GET draws the page (it takes no query: its address is all there is); a
    Review POST answers the review dialog's confirmation; Apply answers with
    the change's status. Refusals are raised for the dialog (see the module
    docstring).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        action = None
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
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            configuration = editable_configuration(service)
            campaign = _campaign(configuration, campaign_id)
            form = WorkGroupForm(
                request.POST if action == "preview" else None,
                initial={
                    "name": campaign.active_configuration.values.get(
                        REMINDER_WORKGROUP, ""
                    ),
                    "base_digest": configuration.active_configuration.digest,
                },
            )
            if action == "preview":
                response = _preview(
                    request, service, actor, configuration, campaign, form
                )
            else:
                response = _page(request, configuration, campaign, form)
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
