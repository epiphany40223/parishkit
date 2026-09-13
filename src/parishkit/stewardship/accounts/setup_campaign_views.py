"""Original-login first-campaign form over unpublished setup catalogs."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version, filters

from .authentication import runtime
from .campaign_forms import CampaignForm, initial_fields
from .campaign_views import MULTIPLE_FIELDS
from .setup_campaign import campaign_catalog
from .setup_drafts import save_section, view_draft
from .setup_views import ERRORS, _checked, _context, error_response


class SetupCampaignForm(CampaignForm):
    """Reuse all structural rules; setup uses the whole attempt's version token."""

    base_digest = None

    def __init__(self, *args, **kwargs):
        """The first draft inherits its original setup Parish timezone."""
        super().__init__(*args, **kwargs)
        self.fields["timezone"].disabled = True


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
                save_section(
                    request,
                    service,
                    draft.status.attempt_id,
                    step="campaign",
                    expected_version=version,
                    values={
                        "source_result": str(catalog.result_id),
                        "campaign": form.values(),
                    },
                )
                return _checked(request, service, HttpResponseRedirect("/admin/setup"))
            status = 400
        response = render(
            request,
            "stewardship/setup-campaign.html",
            _context(draft) | {"form": form},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
