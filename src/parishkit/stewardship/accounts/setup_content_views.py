"""Named temporary page/email editing using the normal sanitized visual controls."""

from uuid import uuid4

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import PLACEHOLDERS, sanitize_html
from parishkit.stewardship.web.contracts import expected_version, filters

from .authentication import runtime
from .content_forms import EMAIL_LABELS, ContentForm, page_slots, sample_render
from .setup_content import content_label, draft_campaign
from .setup_drafts import save_section, view_draft
from .setup_views import ERRORS, _checked, _closed, _context, error_response


class SetupContentForm(ContentForm):
    """The original attempt version replaces the active YAML digest in setup."""

    base_digest = None


def _draft(request, service):
    """Do not create a wizard or let another login select a content owner."""
    draft = view_draft(request, service)
    if draft is None:
        raise LookupError(
            "Start and prepare the first campaign before editing content."
        )
    return draft_campaign(request, service, draft.status.attempt_id)


@require_http_methods(["GET", "HEAD"])
def setup_content(request):
    """Show enabled named pages and independent email slots, including empty slots."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        draft, campaign = _draft(request, service)
        groups = []
        for kind, labels in (("page", page_slots(campaign)), ("email", EMAIL_LABELS)):
            groups.append(
                {
                    "kind": kind,
                    "entries": [
                        {
                            "label": label,
                            "url": reverse(
                                "admin:setup_content_edit", args=[kind, slot]
                            ),
                            "saved": bool(
                                draft.sections.get(f"{kind}_{slot}", {}).get("values")
                            ),
                        }
                        for slot, label in labels.items()
                    ],
                }
            )
        response = render(
            request,
            "stewardship/setup-content.html",
            _context(draft)
            | {
                "groups": groups,
                "campaign_name": campaign["name"],
            },
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def setup_content_edit(request, kind, slot):
    """Every save stays temporary; the sample never reads real Family information."""
    try:
        service = runtime()
        draft, campaign = _draft(request, service)
        label = content_label(campaign, kind, slot)
        step = f"{kind}_{slot}"
        previous = draft.sections.get(step, {}).get("values")
        initial = {
            name: value
            for name, value in (previous or {}).items()
            if name in SetupContentForm.base_fields
        }
        initial["generate_text"] = previous is None
        form = SetupContentForm(
            request.POST if request.method == "POST" else None,
            kind=kind,
            slot=slot,
            initial=initial,
        )
        _closed(request, {*form.fields, "version"})
        status = 200
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload the first-campaign content.")
            if form.is_valid():
                values = form.values(campaign_id=draft.status.attempt_id, slot=slot)
                record = (
                    {"id": str(uuid4()), "values": values}
                    if values
                    else {"id": None, "values": None}
                )
                save_section(
                    request,
                    service,
                    draft.status.attempt_id,
                    step=step,
                    values=record,
                    expected_version=version,
                )
                return _checked(
                    request,
                    service,
                    HttpResponseRedirect(
                        reverse("admin:setup_content_edit", args=[kind, slot])
                    ),
                )
            status = 400
        try:
            visual = sanitize_html(form["html"].value() or "")
        except ValueError:
            visual = ""
        sample = sample_render(
            previous, parish=draft.sections["parish"], campaign=campaign
        )
        response = render(
            request,
            "stewardship/setup-content-edit.html",
            _context(draft)
            | {
                "form": form,
                "label": label,
                "visual": visual,
                "placeholders": sorted(PLACEHOLDERS),
                "sample": sample,
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
