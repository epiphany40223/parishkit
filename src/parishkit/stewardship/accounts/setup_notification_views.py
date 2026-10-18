"""Original-Admin Slack readiness controls reuse passive bounded delivery status."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.admin_forms import AdminForm

from .authentication import runtime
from .setup_drafts import view_draft
from .setup_mail_views import LABELS, _status_data, page_status, status_response
from .setup_notification_models import SetupSlackDelivery
from .setup_notifications import request_notification
from .setup_tests import TEST_SALT, prepare_test
from .setup_views import ERRORS, _checked, _closed, _context, error_response, page_error
from .setup_wizard import wizard_for

# A Slack test message is not email: keep the shared result words, but name
# Slack where the email words would say "the mail service". "Awaiting Slack
# installer" is a deliberate exception to the shared words: a queued Slack
# test waits for the Slack installer, not for the mail worker.
SLACK_LABELS = LABELS | {
    "queued": _("Awaiting Slack installer"),
    "submitting": _("Still sending (handing to Slack)"),
}
# The Recent tests list before any test (setup-test-status.html).
EMPTY = _("No test has been requested yet. Select Send Slack test below to send one.")
# A Slack test that was not sent links the one Slack step (#1476, #1578).
FIX = ("slack", ("slack",))


class SetupNotificationForm(AdminForm):
    """Only explicit intent and uncertainty acknowledgement are client inputs."""

    test_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        required=False,
        label=_("The previous test may have arrived; I want to send another test."),
    )


def _skipped(request, service):
    """Redirect a visit to Test Slack while Slack is off, else return None.

    Once Slack is turned off (#1499) the Test Slack step leaves the wizard,
    but the browser's Back button or an old tab can still open its address.
    That visit is not the Administrator's mistake, so it opens the next step
    needing work, as Home's Continue setup does, instead of an error page
    (#1505). Only a GET or HEAD is redirected: a POST still reaches the
    view's refusal, so no Slack test is ever queued while Slack is off.
    """
    draft = view_draft(request, service)
    if draft is None or draft.sections.get("slack", {}).get("enabled"):
        return None
    wizard = wizard_for(draft)
    if wizard is None or wizard.resume is None:
        return None
    return _checked(request, service, HttpResponseRedirect(wizard.resume.href))


def _data(draft):
    """No credential bytes, channel names or message bodies enter polling JSON."""
    return _status_data(
        draft, "slack_test", model=SetupSlackDelivery, labels=SLACK_LABELS
    )


@require_http_methods(["GET", "HEAD", "POST"])
def setup_notification(request):
    """Explicit CSRF POST queues a fixed sample; GET never contacts Slack."""
    try:
        _closed(request, set(SetupNotificationForm.base_fields))
        service = runtime()
        skipped = request.method != "POST" and _skipped(request, service)
        if skipped:
            return skipped
        inputs = prepare_test(request, service, "slack_test")
        form = SetupNotificationForm(
            request.POST if request.method == "POST" else None,
            initial={
                "test_token": signing.dumps(inputs.binding(), salt=TEST_SALT),
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
        data = _data(inputs.draft)
        response = render(
            request,
            "stewardship/setup-notification.html",
            _context(inputs.draft, "slack_test")
            | page_status(
                form,
                data,
                EMPTY,
                reverse("admin:setup_notification_status") + "?format=status",
                FIX,
            )
            | {"channel_id": inputs.draft.sections["slack"]["channel_id"]},
            status=status,
        )
        return _checked(request, service, response, inputs.draft)
    except signing.BadSignature:
        error = ValueError("The setup test page has expired or changed.")
        return page_error(request, error, "slack_test")
    except ERRORS as error:
        return page_error(request, error, "slack_test")


@require_http_methods(["GET", "HEAD"])
def setup_notification_status(request):
    """Passive polling never extends original-session idle expiry or retries a send.

    It admits the sign-in the page admits, including the page's own rule
    that Slack is on for this setup, and answers JSON or the page's live
    status region (``status_response``).
    """
    try:
        _closed(request, set(), query={"format"})
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("Original setup notification status is unavailable.")
        if not draft.sections.get("slack", {}).get("enabled"):
            raise ValueError("Slack is disabled for this setup.")
        response = status_response(request, _data(draft), EMPTY)
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
