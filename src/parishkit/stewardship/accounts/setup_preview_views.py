"""Non-mutating whole-wizard public preview and fictional named content samples."""

import json

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .authentication import runtime
from .content_forms import EMAIL_LABELS, page_slots, sample_render
from .setup_preview import prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, error_response


@require_http_methods(["GET", "HEAD"])
def setup_preview(request):
    """Make draft selections inspectable without exposing a false completion action."""
    try:
        _closed(request, set())
        service = runtime()
        preview = prepare_preview(request, service)
        document = preview.compiled.candidate.document()
        sections = document["sections"]
        parish, campaign = (
            sections["parish"][0]["values"],
            sections["campaigns"][0]["values"],
        )
        content = {
            (row["values"]["kind"], row["values"]["slot"]): row["values"]
            for row in sections.get("content", [])
        }
        samples = [
            {
                "label": label,
                "kind": kind,
                "sample": sample_render(
                    content.get((kind, slot)), parish=parish, campaign=campaign
                ),
            }
            for kind, labels in (
                ("page", page_slots(campaign)),
                ("email", EMAIL_LABELS),
            )
            for slot, label in labels.items()
        ]
        response = render(
            request,
            "stewardship/setup-preview.html",
            _context(preview.draft)
            | {
                "parish": parish,
                "campaign": campaign,
                "samples": samples,
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
                },
            },
        )
        return _checked(request, service, response, preview.draft)
    except ERRORS as error:
        return error_response(error)
