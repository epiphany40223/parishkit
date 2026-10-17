"""Original-Admin readiness sends and passive, private delivery status."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.jobs.delivery_metadata import STATE_LABELS
from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.admin_forms import AdminForm

from .authentication import runtime
from .integration_credentials import FAILED
from .setup_delivery_models import SetupMailDelivery
from .setup_drafts import view_draft
from .setup_mail import request_sample
from .setup_preview import PREVIEW_SALT, prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, error_response, page_error
from .setup_wizard import BY_KEY

# A setup test email has its own states, named with the outbox's nearest
# plain words (STATE_LABELS) so a test result reads like the same result on
# Sent mail (#678).
LABELS = {
    "queued": STATE_LABELS["pending"],
    "submitting": STATE_LABELS["submitting"],
    "accepted": STATE_LABELS["delivered"],
    "not_sent": STATE_LABELS["permanent_failure"],
    "delivery_unknown": STATE_LABELS["delivery_unknown"],
    "cancelled": STATE_LABELS["cancelled"],
}
# The Recent tests list before any test (setup-test-status.html).
EMPTY = _("No test has been requested yet. Select Send test email below to send one.")
# What a test that was not sent points to (#1476): the provider whose key
# advice it repeats (integration_credentials.FAILED, as Finishing setup and
# Integrations say it) and the wizard steps that fix it, in step order.
FIX = ("google_workspace", ("mail", "google_workspace"))


class SetupMailForm(AdminForm):
    """Only the acknowledgement is editable; message and routing are server-owned."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        required=False,
        label=_("The previous test may have arrived; I want to send another test."),
    )


def _items(draft, *, model=SetupMailDelivery, labels=LABELS):
    """Read only this original attempt's bounded, safe journal metadata."""
    rows = (
        model.objects.filter(attempt_id=draft.status.attempt_id)
        .order_by("-created_at", "-id")
        .values("id", "state", "created_at", "attempt_version")[:25]
    )
    return [
        {
            "id": str(row["id"]),
            "state": row["state"],
            "label": str(labels[row["state"]]),
            "created_at": row["created_at"].isoformat(),
            "current": row["attempt_version"] == draft.status.version,
        }
        for row in rows
    ]


def _status_data(draft, *, model=SetupMailDelivery, labels=LABELS):
    """The uncertainty acknowledgement applies even to older tests outside the list."""
    rows = model.objects.filter(attempt_id=draft.status.attempt_id)
    return {
        "revision": draft.status.version,
        "items": _items(draft, model=model, labels=labels),
        "pending": rows.filter(state__in=["queued", "submitting"]).exists(),
        "unknown": rows.filter(state="delivery_unknown").exists(),
    }


def page_status(form, data, empty, status_url, fix=FIX):
    """The test page's context for its form and live status region (#1354).

    An earlier test whose delivery is unknown makes the acknowledgement box
    required, so the browser and the acknowledgement gate both hold Send.
    ``fix`` names the provider and the steps a test that was not sent links
    (``failure``).
    """
    if data["unknown"]:
        form.fields["acknowledge_unknown"].widget.attrs["required"] = True
    return data | {
        "form": form,
        "tested": tested(data["items"]),
        "failure": failure(data["items"], *fix),
        "empty": empty,
        "status_url": status_url,
    }


def failure(items, provider, steps):
    """The fix notice when this revision's newest test was not sent (#1476).

    Only the newest test counts, and only for the current draft revision: a
    later test, or a saved fix (which bumps the revision), drops the notice.
    It carries the provider's rejected-key advice and the steps to correct.
    """
    if not items or not items[0]["current"] or items[0]["state"] != "not_sent":
        return None
    return {"advice": FAILED[provider], "steps": [BY_KEY[key] for key in steps]}


def status_response(request, data, empty):
    """Answer a passive status read: JSON, or the live region (?format=status).

    The JSON stays for the runtime setup probe; the open page polls the
    region (setup-test-status.html), which draws no form and no CSRF token.
    """
    query = request.GET.get("format")
    if query is None:
        return JsonResponse(data)
    if query != "status":
        raise ValueError("Invalid status format.")
    return render(
        request, "stewardship/setup-test-status.html", data | {"empty": empty}
    )


def tested(items):
    """Whether a test of the current draft revision was accepted (step done).

    The test page then offers Continue as its primary action instead of
    sending; tests are listed newest first, so a current one is among them.
    """
    return any(item["current"] and item["state"] == "accepted" for item in items)


@require_http_methods(["GET", "HEAD", "POST"])
def setup_mail(request):
    """Explicit POST queues mail; rendering and status never call the provider."""
    try:
        _closed(request, set(SetupMailForm.base_fields))
        service = runtime()
        preview = prepare_preview(request, service)
        form = SetupMailForm(
            request.POST if request.method == "POST" else None,
            initial={
                "preview_token": signing.dumps(preview.binding(), salt=PREVIEW_SALT),
                "request_key": uuid4(),
            },
        )
        status = 200
        if request.method == "POST":
            if form.is_valid():
                request_sample(request, service, **form.cleaned_data)
                return _checked(
                    request, service, HttpResponseRedirect(reverse("admin:setup_mail"))
                )
            status = 400
        data = _status_data(preview.draft)
        response = render(
            request,
            "stewardship/setup-mail.html",
            _context(preview.draft, "mail_test")
            | page_status(
                form,
                data,
                EMPTY,
                reverse("admin:setup_mail_status") + "?format=status",
            )
            | {
                "testing_recipient": preview.draft.sections["testing"][
                    "testing_recipient"
                ],
            },
            status=status,
        )
        return _checked(request, service, response, preview.draft)
    except signing.BadSignature:
        error = ValueError("The setup preview has expired or changed.")
        return page_error(request, error, "mail_test")
    except ERRORS as error:
        return page_error(request, error, "mail_test")


@require_http_methods(["GET", "HEAD"])
def setup_mail_status(request):
    """Passive original-owner polling never renews idle expiry or creates a request.

    It admits exactly the sign-in the page admits (the original owner's
    collecting draft, through ``_checked``), and answers JSON or the page's
    live status region (``status_response``).
    """
    try:
        _closed(request, set(), query={"format"})
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("Original setup mail status is unavailable.")
        response = status_response(request, _status_data(draft), EMPTY)
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
