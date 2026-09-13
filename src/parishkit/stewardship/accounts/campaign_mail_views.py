"""Explicit sample preview/send and passive, non-retrying campaign test status."""

from django import forms
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .authentication import runtime
from .campaign_mail import SALT, prepare, request_sample
from .campaign_mail_models import CampaignMailTest
from .integration_views import ERRORS, _checked
from .setup_mail_views import LABELS
from .setup_views import _closed, error_response


class CampaignMailForm(forms.Form):
    """Routing and sample identity are server-owned, not arbitrary recipient fields."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        required=False,
        label=_("A previous test may have arrived; I want to send another test."),
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_mail(request, campaign_id, revision_id):
    """GET never sends; POST names one exact reviewed revision and command key."""
    try:
        _closed(request, set(CampaignMailForm.base_fields))
        service = runtime()
        form = CampaignMailForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            request_sample(
                request, service, campaign_id, revision_id, **form.cleaned_data
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:campaign_mail", args=[campaign_id, revision_id])
                ),
            )
        preview = prepare(request, service, campaign_id, revision_id)
        if request.method != "POST":
            form = CampaignMailForm(
                initial={"preview_token": signing.dumps(preview.binding(), salt=SALT)}
            )
        rows = CampaignMailTest.objects.filter(campaign_id=campaign_id)
        items = [
            {
                "id": row.pk,
                "label": LABELS[row.state],
                "created_at": row.created_at,
                "current": row.configuration_id == preview.row.configuration_id
                and row.template_id == preview.row.template_id,
            }
            for row in rows.order_by("-created_at", "-id").only(
                "id", "state", "created_at", "configuration_id", "template_id"
            )[:25]
        ]
        response = render(
            request,
            "stewardship/campaign-mail.html",
            {
                "campaign": preview.row.campaign,
                "form": form,
                "sample": {
                    "subject": "[TEST] " + preview.sample.subject,
                    "html": preview.sample.html,
                    "text": preview.sample.text,
                },
                "testing_recipient": preview.sample.recipient,
                "pending": rows.filter(state__in=["queued", "submitting"]).exists(),
                "unknown": rows.filter(state="delivery_unknown").exists(),
                "items": items,
            },
            status=400 if request.method == "POST" else 200,
        )
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(
            LookupError("Campaign test configuration is unavailable.")
        )
    except ERRORS as error:
        return error_response(error)
