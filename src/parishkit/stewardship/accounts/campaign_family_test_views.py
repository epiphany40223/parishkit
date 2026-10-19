"""Preview, confirm and passively follow chosen-Family Testing sends."""

import json

from django import forms
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.jobs.delivery_metadata import STATE_LABELS
from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    unexpected_fields,
    with_default_code,
)

from . import admin_navigation
from .admin_editing import form_action, summary_errors
from .authentication import runtime
from .campaign_family_test import (
    SALT,
    duid_list_rules,
    parse_family_duids,
    prepare,
    recent_tickets,
    request_tests,
)
from .content_forms import EMAIL_LABELS
from .integration_views import ERRORS, _checked
from .setup_views import error_response

# A session key, never a query parameter: a one-time "requested"
# confirmation after a successful send.
SENT_KEY = "pk_family_test_sent"

TICKET_LABELS = {
    "queued": _("Queued — it will be sent shortly"),
    "prepared": _("Prepared for the mail worker"),
    "cancelled": _("Cancelled before preparation"),
    "failed": _("Preparation failed"),
}
# A test email is an ordinary outbox message, listed on Sent mail too,
# so it reads in the same plain words there and here (#678).
MESSAGE_LABELS = STATE_LABELS
REASON_LABELS = {
    "eligible": _("Eligible"),
    "unknown": _("Not a Family in this campaign"),
    "ineligible": _("Not currently eligible for the campaign"),
    "undeliverable": _("No deliverable email address"),
    "stale_source": _("Waiting for source reconciliation"),
}


class FamilyTestForm(AdminForm):
    """Only DUIDs are typed; recipients, content and credentials are server-owned."""

    families = forms.CharField(
        max_length=400,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                # The complete gate's hint while the box is empty (#563).
                "data-missing-hint": _("Enter at least one Family DUID to check."),
                # The page reads a typed list as parse_family_duids does, and
                # Check waits, with its words, for one it accepts (#1577).
                "data-duid-list": json.dumps(duid_list_rules()),
            }
        ),
        label=_("Family DUIDs, one per line (at most ten)"),
    )

    def clean_families(self):
        """Report malformed, repeated or too many DUIDs as a field error."""
        try:
            return parse_family_duids(self.cleaned_data["families"])
        except ValueError as error:
            raise forms.ValidationError(str(error), code="invalid_duids") from None


class FamilyTestConfirmForm(AdminForm):
    """Confirmation carries the signed review and an explicit acknowledgement."""

    preview = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    acknowledge = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        label=_(
            "I understand that these real Families' names and codes will be sent "
            "to the Testing recipient."
        ),
    )


def _action(request):
    """Reject hidden, repeated and cross-action fields before reading any value."""
    filters(request.GET, allowed=set())
    if request.FILES:
        raise unexpected_fields(request.FILES, allowed=())
    return form_action(
        request.POST, preview_fields={"families"}, confirm_fields={"acknowledge"}
    )


def _label(item):
    """Describe a ticket by its message once one exists, never by stale intent."""
    if item["behind_unknown"]:
        # Held until the Admin settles the other email; it still counts
        # toward the ten-test limit and Testing cleanup until then.
        return _(
            "Waiting: another email to this Family is marked “Not sure it "
            "arrived”. Resolve it on Sent mail; until then this test waits."
        )
    if item["message_state"] is not None:
        return MESSAGE_LABELS.get(item["message_state"], _("Unknown status"))
    if item["state"] == "prepared":
        # Testing cleanup deleted the message; the ticket alone is retained.
        return _("Removed by Testing cleanup or finished")
    return TICKET_LABELS[item["state"]]


def _pending(item):
    """Whether a recent test is still on its way, so the list should refresh."""
    if item["message_state"] is not None:
        return item["message_state"] in {"pending", "retry_wait", "submitting"}
    return item["state"] == "queued"


def _unavailable(preview):
    """Why the reviewed Families cannot be sent tests now, or None if they can.

    The confirmation rechecks every one of these on the server. Naming the
    first that applies lets the page show Send unavailable with its reason
    instead of leaving it out (#563).
    """
    if preview.epoch_id is None:
        return _(
            "Test codes for this campaign are not ready yet, so no Family test "
            "can be sent."
        )
    if preview.held:
        return _(
            "Campaign work is in progress. When it finishes, check these "
            "Families again to send."
        )
    if not all(choice.eligible for choice in preview.families):
        return _(
            "A chosen Family cannot be sent a test (see its status above). "
            "Remove it from the list and check the Families again."
        )
    if len(preview.families) > preview.available:
        return ngettext(
            "Only %(count)s more Family test may be requested now. Choose fewer "
            "Families, or wait for earlier tests to finish.",
            "Only %(count)s more Family tests may be requested now. Choose fewer "
            "Families, or wait for earlier tests to finish.",
            preview.available,
        ) % {"count": preview.available}
    return None


def _page(request, service, campaign_id, revision_id, *, duids=(), form=None):
    """Render current eligibility and status; a preview is intent, never a send."""
    preview = prepare(request, service, campaign_id, revision_id, duids)
    confirm = None
    if duids and preview.epoch_id is not None:
        confirm = FamilyTestConfirmForm(
            initial={"preview": signing.dumps(preview.binding(), salt=SALT)}
        )
    unavailable = _unavailable(preview) if duids else None
    items = [item | {"label": _label(item)} for item in recent_tickets(campaign_id)]
    # The Recent Family tests region follows itself (live-status-v1.js) while
    # a test is on its way. The script swaps only that region and never
    # reloads the page, so a POST response or a shown review is never re-sent.
    refresh = any(_pending(item) for item in items)
    # The "requested" notice stays while its tests are still on their way.
    sent = None
    if not duids:
        sent = (
            request.session.get(SENT_KEY)
            if refresh
            else request.session.pop(SENT_KEY, None)
        )
    # Choose, review, then send and follow (#196): a shown review is the
    # review step, and the page after a send follows it. The trail runs
    # through the email's editor and its fictional sample test.
    slot = preview.template.slot
    admin_navigation.place(
        request,
        arguments={"kind": "email", "slot": slot},
        labels={"content_revision": EMAIL_LABELS.get(slot, slot)},
        flow="family_test",
        step="review" if duids else "send" if sent else "choose",
    )
    refused = form is not None and not form.is_valid()
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
                    "name": choice.name,
                    "duid": choice.duid,
                    "eligible": choice.eligible,
                    "label": REASON_LABELS[choice.reason],
                }
                for choice in preview.families
            ],
            "confirm": confirm,
            # The signed review is issued for any reviewed list; confirmation
            # rechecks eligibility and the allowance, so only the button is
            # shown unavailable, with this reason.
            "unavailable": unavailable,
            "sendable": unavailable is None,
            "items": items,
            "refresh": refresh,
            "refresh_url": reverse("admin:campaign_mail_families", args=[revision_id]),
            "sent": sent,
            # A refused list's error summary (base template), which links to
            # the field and takes focus when Check is answered in place (#519).
            "errors": summary_errors(form) if refused else [],
        },
        status=400 if refused else 200,
    )
    if refused:
        # The refusal is this page with its errors, safe to show as it is
        # (not the generic "Invalid request." the middleware draws for other
        # 400s), so the reader sees which DUID to fix.
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
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
        rows = request_tests(
            request,
            service,
            campaign_id,
            revision_id,
            preview_token=form.cleaned_data["preview"],
            acknowledge=form.cleaned_data["acknowledge"],
        )
        request.session[SENT_KEY] = len(rows)
        return _checked(
            request,
            service,
            HttpResponseRedirect(
                reverse("admin:campaign_mail_families", args=[revision_id])
            ),
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Family test configuration is unavailable."))
    except ERRORS as error:
        return error_response(error)
