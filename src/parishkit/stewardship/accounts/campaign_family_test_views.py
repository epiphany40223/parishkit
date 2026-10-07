"""Preview, confirm and passively follow chosen-Family Testing sends."""

from django import forms
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.jobs.delivery_metadata import STATE_LABELS
from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.contracts import filters

from . import admin_navigation
from .admin_editing import form_action
from .authentication import runtime
from .campaign_family_test import (
    SALT,
    parse_family_duids,
    prepare,
    recent_tickets,
    request_tests,
)
from .content_forms import EMAIL_LABELS
from .integration_views import ERRORS, _checked
from .sessions import FreshAuthenticationRequired, freshness
from .setup_views import error_response

# Session keys, never query parameters: DUIDs to restore after a Google
# step-up, and a one-time "requested" confirmation after a successful send.
RESTORE_KEY = "pk_family_test_restore"
SENT_KEY = "pk_family_test_sent"

TICKET_LABELS = {
    "queued": _("Queued — it will be sent shortly"),
    "prepared": _("Prepared for the mail worker"),
    "cancelled": _("Cancelled before preparation"),
    "failed": _("Preparation failed"),
}
# A test email is an ordinary outbox message, listed on Outgoing mail too,
# so it reads in the same plain words there and here (#678).
MESSAGE_LABELS = STATE_LABELS
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


def _scope(campaign_id, revision_id):
    """Bind a restore entry to exactly this campaign and email template."""
    return {"campaign": str(campaign_id), "revision": str(revision_id)}


def _restore(request, campaign_id, revision_id):
    """Take back the DUIDs saved before a Google step-up, once, if they match.

    A visit to another template's page leaves the entry alone, so it is used
    only by the page it was saved for.
    """
    saved = request.session.get(RESTORE_KEY)
    if not isinstance(saved, dict) or {
        key: saved.get(key) for key in ("campaign", "revision")
    } != _scope(campaign_id, revision_id):
        return ()
    del request.session[RESTORE_KEY]
    try:
        return parse_family_duids(" ".join(str(value) for value in saved["families"]))
    except (KeyError, TypeError, ValueError):
        return ()


def _remember(request, campaign_id, revision_id, duids):
    """Keep a reviewed selection across the Google step-up (DUIDs only).

    Nothing is sent from this: after returning, the Admin sees a fresh preview
    of the same Families and still confirms and presses Send themselves.
    """
    request.session[RESTORE_KEY] = _scope(campaign_id, revision_id) | {
        "families": [str(duid) for duid in duids]
    }


def _pending(item):
    """Whether a recent test is still on its way, so the list should refresh."""
    if item["message_state"] is not None:
        return item["message_state"] in {"pending", "retry_wait", "submitting"}
    return item["state"] == "queued"


def _page(
    request, service, campaign_id, revision_id, *, duids=(), form=None, restored=False
):
    """Render current eligibility and status; a preview is intent, never a send."""
    preview = prepare(request, service, campaign_id, revision_id, duids)
    fresh, minutes = freshness(request)
    if duids and not fresh:
        # Sending needs a recent Google sign-in; keep the selection for after.
        _remember(request, campaign_id, revision_id, duids)
    confirm = None
    if duids and preview.epoch_id is not None:
        confirm = FamilyTestConfirmForm(
            initial={"preview": signing.dumps(preview.binding(), salt=SALT)}
        )
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
            # rechecks eligibility and the allowance, so only the button hides.
            "sendable": not preview.held
            and all(choice.eligible for choice in preview.families)
            and len(preview.families) <= preview.available,
            "items": items,
            "refresh": refresh,
            "refresh_url": reverse("admin:campaign_mail_families", args=[revision_id]),
            "fresh": fresh,
            "signed_in_minutes": minutes,
            "restored": restored,
            "sent": sent,
            "next": request.path,
            "sample_url": reverse("admin:campaign_mail", args=[revision_id]),
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
            duids = _restore(request, campaign_id, revision_id)
            return _page(
                request,
                service,
                campaign_id,
                revision_id,
                duids=duids,
                restored=bool(duids),
            )
        if _action(request) == "preview":
            form = FamilyTestForm(request.POST)
            duids = form.cleaned_data["families"] if form.is_valid() else ()
            return _page(
                request, service, campaign_id, revision_id, duids=duids, form=form
            )
        form = FamilyTestConfirmForm(request.POST)
        if not form.is_valid():
            raise ValueError("Confirm the reviewed Families and the acknowledgement.")
        try:
            rows = request_tests(
                request,
                service,
                campaign_id,
                revision_id,
                preview_token=form.cleaned_data["preview"],
                acknowledge=form.cleaned_data["acknowledge"],
            )
        except FreshAuthenticationRequired as error:
            # Nothing was sent. Keep the reviewed DUIDs so that, after the
            # Google step-up, the page shows the same review ready to send.
            try:
                binding = signing.loads(form.cleaned_data["preview"], salt=SALT)
                _remember(
                    request,
                    campaign_id,
                    revision_id,
                    parse_family_duids(" ".join(binding["families"])),
                )
                kept = True
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                kept = False
            response = error_response(error)
            response.stewardship_inputs_kept = kept
            return response
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
