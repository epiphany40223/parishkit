"""Original-login first-campaign form over unpublished setup catalogs."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version, filters

from . import setup_help
from .authentication import runtime
from .campaign_forms import CampaignForm, initial_fields
from .campaign_views import MULTIPLE_FIELDS
from .setup_campaign import campaign_catalog
from .setup_content import FILL_UNSET, default_updates
from .setup_content_views import result_url
from .setup_drafts import save_sections, view_draft
from .setup_views import ERRORS, _checked, _context, page_error
from .setup_wizard import continue_after


class SetupCampaignForm(CampaignForm):
    """Reuse all structural rules; setup uses the whole attempt's version token."""

    base_digest = None

    def __init__(self, *args, **kwargs):
        """The first draft inherits its original setup Parish timezone."""
        super().__init__(*args, **kwargs)
        self.fields["timezone"].disabled = True
        # Replace: the setup text is more specific, and it repeats the
        # multi-select instructions that are the shared form's own help.
        setup_help.apply(self, setup_help.CAMPAIGN, replace=True)


@require_http_methods(["GET", "HEAD", "POST"])
def setup_campaign(request):
    """Stage a complete structural draft without creating an active campaign."""
    try:
        filters(request.GET, allowed=set())
        if (
            request.FILES
            or set(request.POST)
            - {*SetupCampaignForm.base_fields, "version", "csrfmiddlewaretoken"}
            or any(
                len(values) != 1 and name not in MULTIPLE_FIELDS
                for name, values in request.POST.lists()
            )
        ):
            raise ValueError("Invalid first-campaign fields.")
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        catalog = campaign_catalog(request, service, draft.status.attempt_id)
        previous = draft.sections.get("campaign", {}).get("campaign")
        initial = initial_fields(previous, digest="") if previous else {}
        initial["timezone"] = catalog.timezone
        form = SetupCampaignForm(
            request.POST if request.method == "POST" else None,
            initial=initial,
            previous=previous,
            ministries=catalog.ministries,
            funds=catalog.funds,
        )
        status = 200
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload the first-campaign form.")
            if form.is_valid():
                values = form.values()
                # Pages and emails start with their default text: every
                # applicable slot the draft has never set (including page
                # slots a newly enabled module adds) is filled in the same
                # versioned save as the campaign, so the draft never shows a
                # half-applied edit. Saved text and slots the Admin
                # explicitly cleared are left alone.
                updates = {
                    "campaign": {
                        "source_result": str(catalog.result_id),
                        "campaign": values,
                    }
                } | default_updates(
                    draft.sections,
                    values,
                    draft.status.attempt_id,
                    which=FILL_UNSET,
                )
                save_sections(
                    request,
                    service,
                    draft.status.attempt_id,
                    updates=updates,
                    expected_version=version,
                )
                response = continue_after(request, service, "campaign")
                # Report the automatic fill on the content list when that is
                # the next page, as the fill button's own result does.
                filled = updates.keys() - {"campaign"}
                if filled and response["Location"] == reverse("admin:setup_content"):
                    response["Location"] = result_url("filled", filled)
                return _checked(request, service, response)
            status = 400
        response = render(
            request,
            "stewardship/setup-campaign.html",
            _context(draft, "campaign") | {"form": form},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "campaign")
