"""Original-Admin readiness sends and passive, private delivery status."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .authentication import runtime
from .content_forms import EMAIL_LABELS
from .setup_delivery_models import SetupMailDelivery
from .setup_drafts import view_draft
from .setup_mail import request_sample
from .setup_preview import PREVIEW_SALT, prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, error_response

LABELS = {
    "queued": _("Awaiting mail worker"),
    "submitting": _("Submitting to provider"),
    "accepted": _("Provider accepted the test"),
    "not_sent": _("Not sent"),
    "delivery_unknown": _("Delivery uncertain — it may have arrived"),
    "cancelled": _("Cancelled before submission"),
}


class SetupMailForm(forms.Form):
    """Only the slot and acknowledgement are editable; routing is server-owned."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    slot = forms.ChoiceField(
        label=_("Email sample"), choices=list(EMAIL_LABELS.items())
    )
    acknowledge_unknown = forms.BooleanField(
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
                "slot": "initial",
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
            _context(preview.draft)
            | {
                "form": form,
                **data,
                "testing_recipient": preview.draft.sections["testing"][
                    "testing_recipient"
                ],
            },
            status=status,
        )
        return _checked(request, service, response, preview.draft)
    except signing.BadSignature:
        return error_response(ValueError("The setup preview has expired or changed."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD"])
def setup_mail_status(request):
    """Passive original-owner polling never renews idle expiry or creates a request."""
    try:
        _closed(request, set())
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("Original setup mail status is unavailable.")
        response = JsonResponse(_status_data(draft))
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
