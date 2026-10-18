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

from . import stages
from .authentication import runtime
from .integration_credentials import FAILED
from .setup_delivery_models import SetupMailDelivery
from .setup_drafts import view_draft
from .setup_mail import request_sample
from .setup_tests import TEST_SALT, prepare_test, staged_credential
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
# The delivery columns a test is bound to (#1578), as accepted_tests reads
# them.
BINDING = ("credential_id", "credential_version", "fingerprint")
# The Recent tests list before any test (setup-test-status.html).
EMPTY = _("No test has been requested yet. Select Send test email below to send one.")
# What a test that was not sent points to (#1476): the provider whose key
# advice it repeats (integration_credentials.FAILED, as Finishing setup and
# Integrations say it) and the wizard steps that fix it, in step order.
FIX = ("google_workspace", ("mail", "testing", "google_workspace"))


class SetupMailForm(AdminForm):
    """Only the acknowledgement is editable; message and routing are server-owned."""

    test_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        required=False,
        label=_("The previous test may have arrived; I want to send another test."),
    )


# The stages a setup test moves through, drawn as the shared stage boxes on
# Test email and Test Slack (#1578): the worker reports exactly these.
STAGES = (
    ("queued", _("Queued")),
    ("sending", _("Sending")),
    ("result", _("Result")),
)
# Each test state's place in STAGES: the box it reached and that box's state.
# An accepted test has passed every stage; one not sent, or of unknown
# delivery, failed at its Result; a cancelled one never left the queue.
PLACES = {
    "queued": (0, "active"),
    "submitting": (1, "active"),
    "accepted": (len(STAGES), "done"),
    "not_sent": (2, "failed"),
    "delivery_unknown": (2, "failed"),
    "cancelled": (0, "failed"),
}


def progress(items):
    """The newest test's stage boxes (``stages.rows``), or None before any.

    A settled test's last box says its outcome in the list's own words
    (its ``label``, e.g. Delivered or Not sure it arrived) instead of only Done
    or Failed, so the boxes and the line above them agree.
    """
    if not items:
        return None
    newest = items[0]
    index, current = PLACES[newest["state"]]
    boxes = stages.rows(STAGES, index, current)
    if newest["state"] not in {"queued", "submitting"}:
        boxes[min(index, len(STAGES) - 1)]["status"] = newest["label"]
    return boxes


def _items(draft, binding, *, model=SetupMailDelivery, labels=LABELS):
    """Read only this original attempt's bounded, safe journal metadata.

    A test is ``current`` while it was sent with ``binding``, the current
    staged credential's (id, version, fingerprint), or None when there is
    none (#1578): only such a test can complete the step.
    """
    rows = (
        model.objects.filter(attempt_id=draft.status.attempt_id)
        .order_by("-created_at", "-id")
        .values("id", "state", "created_at", *BINDING)[:25]
    )
    return [
        {
            "id": str(row["id"]),
            "state": row["state"],
            "label": str(labels[row["state"]]),
            "created_at": row["created_at"].isoformat(),
            "current": tuple(row[name] for name in BINDING) == binding,
        }
        for row in rows
    ]


def _status_data(draft, key="mail_test", *, model=SetupMailDelivery, labels=LABELS):
    """The uncertainty acknowledgement applies even to older tests outside the list."""
    rows = model.objects.filter(attempt_id=draft.status.attempt_id)
    credential = staged_credential(draft, key)
    binding = credential and (credential.pk, credential.version, credential.fingerprint)
    return {
        "revision": draft.status.version,
        "items": _items(draft, binding, model=model, labels=labels),
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
    """The fix notice when the newest test of these settings was not sent (#1476).

    Only the newest test counts, and only while it was sent with the current
    credential and settings: a later test, or a saved fix (which changes or
    re-enters the credential, #1578), drops the notice.
    It carries the provider (a Slack notice offers turning Slack off,
    #1499), its rejected-key advice and the steps to correct.
    """
    if not items or not items[0]["current"] or items[0]["state"] != "not_sent":
        return None
    return {
        "provider": provider,
        "advice": FAILED[provider],
        "steps": [BY_KEY[key] for key in steps],
    }


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
    """Whether a test of the current settings was accepted (step done).

    The test page then offers Continue as its primary action instead of
    sending; tests are listed newest first, so a current one is among them.
    """
    return any(item["current"] and item["state"] == "accepted" for item in items)


def retest(items):
    """Whether an accepted test exists only for settings changed since.

    Test email then says why it must be sent again (#1499): its outgoing
    email settings, Testing recipient or Google Workspace key changed, and
    only a test of the current ones counts (#1578).
    """
    return not tested(items) and any(
        not item["current"] and item["state"] == "accepted" for item in items
    )


@require_http_methods(["GET", "HEAD", "POST"])
def setup_mail(request):
    """Explicit POST queues mail; rendering and status never call the provider."""
    try:
        _closed(request, set(SetupMailForm.base_fields))
        service = runtime()
        inputs = prepare_test(request, service, "mail_test")
        form = SetupMailForm(
            request.POST if request.method == "POST" else None,
            initial={
                "test_token": signing.dumps(inputs.binding(), salt=TEST_SALT),
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
        data = _status_data(inputs.draft)
        response = render(
            request,
            "stewardship/setup-mail.html",
            _context(inputs.draft, "mail_test")
            | page_status(
                form,
                data,
                EMPTY,
                reverse("admin:setup_mail_status") + "?format=status",
            )
            | {
                "testing_recipient": inputs.draft.sections["testing"][
                    "testing_recipient"
                ],
                "retest": retest(data["items"]),
            },
            status=status,
        )
        return _checked(request, service, response, inputs.draft)
    except signing.BadSignature:
        error = ValueError("The setup test page has expired or changed.")
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
