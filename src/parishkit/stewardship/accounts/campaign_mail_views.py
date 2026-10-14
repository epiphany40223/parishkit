"""Explicit sample preview/send and passive, non-retrying campaign test status."""

from urllib.parse import urlsplit

from django import forms
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.acknowledgment import ACKNOWLEDGMENT
from parishkit.stewardship.web.namespaces import admin_return_path

from . import admin_navigation
from .authentication import runtime
from .campaign_mail import SALT, prepare, recent_tests, request_sample
from .content_forms import EMAIL_LABELS
from .integration_views import ERRORS, _checked
from .setup_mail_views import LABELS
from .setup_views import _closed, error_response


class CampaignMailForm(forms.Form):
    """Routing and sample identity are server-owned, not arbitrary recipient fields."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    acknowledge_unknown = forms.BooleanField(
        widget=ACKNOWLEDGMENT,
        required=False,
        label=_("A previous test may have arrived; I want to send another test."),
    )


# This page's own flow: its Send reloads it and its "Send to chosen Families"
# child links back to it, so arriving from either never replaces the page the
# test was opened from.
OWN_FLOW = frozenset({"campaign_mail", "campaign_mail_families"})


def _origin_key(revision_id):
    """The remembered-origin key of one email's test page in this sign-in."""
    return f"campaign-mail:{revision_id}"


def _remember_opener(request, revision_id):
    """Remember the Admin page this test page was opened from (rule 5).

    Navigation rule 5: Preview and test email returns to the page it was
    opened from, kept in the signed-in session (the store a configuration
    change's origin uses), never in the URL. Only a GET's same-origin
    Referer naming another Admin page counts; anything else keeps what this
    sign-in remembered before, so a Referer that names a non-page route or a
    page a link cannot reopen never replaces a good opener.
    """
    if request.method != "GET":
        return
    referer = urlsplit(request.headers.get("Referer", ""))
    if (referer.scheme, referer.netloc) != (request.scheme, request.get_host()):
        return
    if admin_return_path(referer.path) != referer.path:
        return
    try:
        match = resolve(referer.path)
    except Resolver404:
        return
    page = admin_navigation.PAGES.get(match.url_name)
    if (
        match.namespace == admin_navigation.NAMESPACE
        and page is not None
        and page.linkable
        and match.url_name not in OWN_FLOW
    ):
        admin_navigation.remember_origin(
            request, _origin_key(revision_id), path=referer.path
        )


def _return_link(request, revision_id):
    """``{"label", "url"}`` back to the remembered opener, or None.

    None when no opener is remembered or the breadcrumb already leads there
    (Home, Pages and emails, the email's editor): rule 3 forbids a
    hand-written back link that repeats the trail.
    """
    origin = admin_navigation.change_origin(request, _origin_key(revision_id))
    if origin is None:
        return None
    name, arguments = origin
    trail, page = {"index"}, "campaign_mail"
    while page:
        trail.add(page)
        page = admin_navigation.PAGES[page].parent
    if name in trail:
        return None
    try:
        url = reverse(f"admin:{name}", kwargs=arguments)
    except NoReverseMatch:
        return None
    return {"label": admin_navigation.PAGES[name].label, "url": url}


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
                    reverse("admin:campaign_mail", args=[revision_id])
                ),
            )
        preview = prepare(request, service, campaign_id, revision_id)
        _remember_opener(request, revision_id)
        if request.method != "POST":
            form = CampaignMailForm(
                initial={"preview_token": signing.dumps(preview.binding(), salt=SALT)}
            )
        # The trail runs through this revision's editor, named for its email
        # (#196); the test route itself carries no kind or slot.
        slot = preview.row.template.slot
        admin_navigation.place(
            request,
            arguments={"kind": "email", "slot": slot},
            labels={"content_revision": EMAIL_LABELS.get(slot, slot)},
        )
        tests = recent_tests(campaign_id, preview.row)
        items = [{**item, "label": LABELS[item["state"]]} for item in tests["items"]]
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
                # Real chosen-Family sends exist only for a Testing draft whose
                # schedules use this template.
                "families_url": preview.families_url,
                "pending": tests["pending"],
                "unknown": tests["unknown"],
                "items": items,
                "return_link": _return_link(request, revision_id),
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
