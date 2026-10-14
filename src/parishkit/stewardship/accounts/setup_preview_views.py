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
        # Showing the whole review is what completes this step, for this exact
        # draft version. Record it first so this page's own stepper and
        # Continue already reflect it; a draft that changes meanwhile has a
        # newer version, so the mark cannot complete the newer draft.
        draft = preview.draft
        mark_reviewed(request, draft.status.attempt_id, "preview", draft.status.version)
        draft = replace(
            draft, reviewed=draft.reviewed | {"preview": draft.status.version}
        )
        response = render(
            request,
            "stewardship/setup-preview.html",
            _context(draft, "preview")
            | {
                "parish": parish,
                "candidate_digest": preview.compiled.candidate.digest,
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
