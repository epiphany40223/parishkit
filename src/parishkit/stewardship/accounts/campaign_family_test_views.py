"""Preview, confirm and passively follow chosen-Family Testing sends."""

from django import forms
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import filters

from .admin_editing import form_action
from .authentication import runtime
from .campaign_family_test import (
    SALT,
    parse_family_duids,
    prepare,
    recent_tickets,
    request_tests,
)
from .integration_views import ERRORS, _checked
from .setup_views import error_response

TICKET_LABELS = {
    "queued": _("Awaiting the general worker"),
    "prepared": _("Prepared for the mail worker"),
    "cancelled": _("Cancelled before preparation"),
    "failed": _("Preparation failed"),
}
MESSAGE_LABELS = {
    "pending": _("Waiting for the mail worker"),
    "retry_wait": _("Waiting to retry"),
    "submitting": _("Submitting to provider"),
    "delivered": _("Provider accepted the test"),
    "permanent_failure": _("Not sent"),
    "delivery_unknown": _("Delivery uncertain — it may have arrived"),
    "cancelled": _("Cancelled before submission"),
}
REASON_LABELS = {
    "eligible": _("Eligible"),
    "unknown": _("Not a Family in this campaign"),
    "ineligible": _("Not currently eligible for the campaign"),
    "undeliverable": _("No deliverable email address"),
    "stale_source": _("Waiting for source reconciliation"),
}


class FamilyTestForm(forms.Form):
    """Only DUIDs are typed; recipients, content and credentials are server-owned."""

    families = forms.CharField(
        max_length=400,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Family DUIDs, one per line (at most ten)"),
    )

    def clean_families(self):
        """Report malformed, repeated or too many DUIDs as a field error."""
        try:
            return parse_family_duids(self.cleaned_data["families"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None


class FamilyTestConfirmForm(forms.Form):
    """Confirmation carries the signed review and an explicit acknowledgement."""

    preview = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    acknowledge = forms.BooleanField(
        label=_(
            "I understand that these real Families' names and codes will be sent "
            "to the Testing recipient."
        )
    )


def _action(request):
    """Reject hidden, repeated and cross-action fields before reading any value."""
    filters(request.GET, allowed=set())
    if request.FILES:
        raise ValueError("Invalid Family test fields.")
    return form_action(
        request.POST, preview_fields={"families"}, confirm_fields={"acknowledge"}
    )


def _label(item):
    """Describe a ticket by its message once one exists, never by stale intent."""
    if item["message_state"] is not None:
        return MESSAGE_LABELS.get(item["message_state"], _("Unknown status"))
    if item["state"] == "prepared":
        # Testing cleanup deleted the message; the ticket alone is retained.
        return _("Removed by Testing cleanup or finished")
    return TICKET_LABELS[item["state"]]


def _page(request, service, campaign_id, revision_id, *, duids=(), form=None):
    """Render current eligibility and status; a preview is intent, never a send."""
    preview = prepare(request, service, campaign_id, revision_id, duids)
    confirm = None
    if duids and preview.epoch_id is not None:
        confirm = FamilyTestConfirmForm(
            initial={"preview": signing.dumps(preview.binding(), salt=SALT)}
        )
    response = render(
        request,
        "stewardship/campaign-mail-families.html",
        {
            "campaign": preview.campaign,
            "subject": preview.template.subject,
            "testing_recipient": preview.testing_recipient,
            "epoch_ready": preview.epoch_id is not None,
            "held": preview.held,
            "available": preview.available,
            "form": form or FamilyTestForm(),
            "families": [
                {
                    "duid": choice.duid,
                    "eligible": choice.eligible,
                    "label": REASON_LABELS[choice.reason],
                }
                for choice in preview.families
            ],
            "confirm": confirm,
            # The signed review is issued for any reviewed list; confirmation
            # rechecks eligibility and the allowance, so only the button hides.
            "sendable": not preview.held
            and all(choice.eligible for choice in preview.families)
            and len(preview.families) <= preview.available,
            "items": [
                item | {"label": _label(item)} for item in recent_tickets(campaign_id)
            ],
            "sample_url": reverse(
                "admin:campaign_mail", args=[campaign_id, revision_id]
            ),
        },
        status=200 if form is None or form.is_valid() else 400,
    )
    return _checked(request, service, response)


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_mail_families(request, campaign_id, revision_id):
    """GET and preview never send; confirm records one reviewed request per Family."""
    try:
        service = runtime()
        if request.method != "POST":
            filters(request.GET, allowed=set())
            return _page(request, service, campaign_id, revision_id)
        if _action(request) == "preview":
            form = FamilyTestForm(request.POST)
            duids = form.cleaned_data["families"] if form.is_valid() else ()
            return _page(
                request, service, campaign_id, revision_id, duids=duids, form=form
            )
        form = FamilyTestConfirmForm(request.POST)
        if not form.is_valid():
            raise ValueError("Confirm the reviewed Families and the acknowledgement.")
        request_tests(
            request,
            service,
            campaign_id,
            revision_id,
            preview_token=form.cleaned_data["preview"],
            acknowledge=form.cleaned_data["acknowledge"],
        )
        return _checked(
            request,
            service,
            HttpResponseRedirect(
                reverse("admin:campaign_mail_families", args=[campaign_id, revision_id])
            ),
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Family test configuration is unavailable."))
    except ERRORS as error:
        return error_response(error)
