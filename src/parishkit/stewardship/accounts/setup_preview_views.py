"""Non-mutating whole-wizard public preview of the system settings."""

import json
from dataclasses import replace

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.jobs.campaign_mail_values import document_parish

from .authentication import runtime
from .branding_context import header_logo_size
from .branding_models import BrandingAsset
from .setup_drafts import mark_reviewed
from .setup_preview import prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, page_error
from .setup_wizard import BY_KEY, applicable, observed


@require_http_methods(["GET", "HEAD"])
def setup_preview(request):
    """Make draft selections inspectable without exposing a false completion action."""
    try:
        _closed(request, set())
        service = runtime()
        preview = prepare_preview(request, service)
        document = preview.compiled.candidate.document()
        # Setup collects no campaign (#142), so the review shows the parish,
        # integration and access settings and no page or email samples.
        parish = document_parish(document)
        # Showing the whole review, with the tests of this exact draft version
        # accepted (#1476), is what completes this step. Record it first so
        # this page's own stepper and Continue already reflect it; a draft
        # that changes meanwhile has a newer version, so the mark cannot
        # complete the newer draft. Review opened before its tests (by
        # address) shows them as not accepted and records nothing, so it is
        # asked for again once they are.
        draft = preview.draft
        credentials, tests = observed(draft)
        readiness = _readiness(draft, tests)
        if all(item["accepted"] for item in readiness):
            mark_reviewed(
                request, draft.status.attempt_id, "preview", draft.status.version
            )
            draft = replace(
                draft, reviewed=draft.reviewed | {"preview": draft.status.version}
            )
        response = render(
            request,
            "stewardship/setup-preview.html",
            {
                **_context(draft, "preview", receipts=(credentials, tests)),
                "readiness": readiness,
                "tests_accepted": all(item["accepted"] for item in readiness),
                "parish": parish,
                "document": json.dumps(document, ensure_ascii=False, indent=2),
                "testing_recipient": preview.draft.sections["testing"][
                    "testing_recipient"
                ],
                "parish_branding": {
                    "name": parish["name"],
                    **{
                        label: reverse("admin:setup_branding_asset", args=[identifier])
                        for label, identifier in preview.branding.items()
                    },
                    **_menu_size(preview.branding.get("menu")),
                },
            },
        )
        return _checked(request, service, response, preview.draft)
    except ERRORS as error:
        return page_error(request, error, "preview")


def _readiness(draft, tests):
    """Each required test's state for the current draft version (#1476).

    One row per applicable test step, in step order: its label, whether a
    test of these exact settings was accepted, and the step's address, which
    Review links only for a test that was not.
    """
    return [
        {
            "label": BY_KEY[key].label,
            "accepted": key in tests,
            "url": BY_KEY[key].url,
        }
        for key in ("mail_test", "slack_test")
        if applicable(key, draft.sections)
    ]


def _menu_size(identifier):
    """The staged menu logo's drawn header size, as ``branding_for`` gives
    the live one, so the preview's header reserves the same box (#1062)."""
    if identifier is None:
        return {}
    assets = BrandingAsset.objects.filter(pk=identifier)
    stored = assets.values_list("width", "height").first()
    if stored is None:
        return {}
    width, height = header_logo_size(*stored)
    return {"menu_width": width, "menu_height": height}
