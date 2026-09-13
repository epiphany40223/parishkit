"""Original-Admin Slack readiness controls reuse passive bounded delivery status."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .authentication import runtime
from .setup_drafts import view_draft
from .setup_mail_views import LABELS, _status_data
from .setup_notification_models import SetupSlackDelivery
from .setup_notifications import request_notification
from .setup_preview import PREVIEW_SALT, prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, error_response

SLACK_LABELS = LABELS | {"queued": _("Awaiting Slack installer")}


class SetupNotificationForm(forms.Form):
    """Only explicit intent and uncertainty acknowledgement are client inputs."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        required=False,
        label=_("The previous test may have arrived; I want to send another test."),
    )


def _data(draft):
    """No credential bytes, channel names or message bodies enter polling JSON."""
    return _status_data(draft, model=SetupSlackDelivery, labels=SLACK_LABELS)


@require_http_methods(["GET", "HEAD", "POST"])
def setup_notification(request):
    """Explicit CSRF POST queues a fixed sample; GET never contacts Slack."""
    try:
        _closed(request, set(SetupNotificationForm.base_fields))
        service = runtime()
        preview = prepare_preview(request, service)
        if not preview.draft.sections["slack"]["enabled"]:
            raise ValueError("Slack is disabled for this setup.")
        form = SetupNotificationForm(
            request.POST if request.method == "POST" else None,
            initial={
                "preview_token": signing.dumps(preview.binding(), salt=PREVIEW_SALT),
                "request_key": uuid4(),
            },
        )
        status = 200
        if request.method == "POST":
            if form.is_valid():
                request_notification(request, service, **form.cleaned_data)
                return _checked(
                    request,
                    service,
                    HttpResponseRedirect(reverse("admin:setup_notification")),
                )
            status = 400
        response = render(
            request,
            "stewardship/setup-notification.html",
            _context(preview.draft)
            | _data(preview.draft)
            | {
                "form": form,
                "channel_id": preview.draft.sections["slack"]["channel_id"],
            },
            status=status,
        )
        return _checked(request, service, response, preview.draft)
    except signing.BadSignature:
        return error_response(ValueError("The setup preview has expired or changed."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD"])
def setup_notification_status(request):
    """Passive polling never extends original-session idle expiry or retries a send."""
    try:
        _closed(request, set())
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("Original setup notification status is unavailable.")
        return _checked(request, service, JsonResponse(_data(draft)), draft)
    except ERRORS as error:
        return error_response(error)
