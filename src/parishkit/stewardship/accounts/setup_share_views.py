"""The first campaign's share choices use ordinary stable option identities."""

from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version

from . import setup_help
from .authentication import runtime
from .setup_content_views import _draft
from .setup_drafts import save_section
from .setup_formsets import closed_formset
from .setup_views import ERRORS, _checked, _context, page_error
from .setup_wizard import continue_after
from .share_forms import ShareOptionForm, ShareOptions


@require_http_methods(["GET", "HEAD", "POST"])
def setup_shares(request):
    """Reorder, add and remove temporary options without activating configuration."""
    try:
        closed_formset(
            request,
            prefix="options",
            fields=(*ShareOptionForm.base_fields, "ORDER", "DELETE"),
        )
        service = runtime()
        draft, campaign = _draft(request, service)
        if "financial" not in campaign["modules"]:
            raise LookupError("Financial stewardship is not enabled.")
        formset = ShareOptions(
            request.POST if request.method == "POST" else None,
            prefix="options",
            previous=campaign["share_options"],
        )
        for form in formset.forms:
            setup_help.apply(form, setup_help.SHARE)
        status = 200
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload the first-campaign share options.")
            if formset.is_valid():
                save_section(
                    request,
                    service,
                    draft.status.attempt_id,
                    step="campaign",
                    values=draft.sections["campaign"]
                    | {"campaign": campaign | {"share_options": formset.values()}},
                    expected_version=version,
                )
                return _checked(
                    request, service, continue_after(request, service, "shares")
                )
            status = 400
        response = render(
            request,
            "stewardship/setup-shares.html",
            _context(draft, "shares")
            | {"formset": formset, "campaign_name": campaign["name"]},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "shares")
