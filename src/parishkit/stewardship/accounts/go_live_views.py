"""Admin-only, passive readiness evidence before any irreversible cleanup."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.cleanup_preview import cleanup_families
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.source.readiness import source_readiness
from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters

from .admin_editing import editable_configuration, principal
from .authentication import runtime
from .go_live_commands import start_cleanup, verify_preview
from .go_live_inputs import collect_inputs
from .go_live_progress import control, progress
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response

PROBLEMS = {
    "initial_schedule_required": _("Save exactly one initial invitation schedule."),
    "page_reference_unavailable": _("Resolve the selected campaign page revisions."),
    "mail_template_unavailable": _(
        "Select an available template for every mail schedule."
    ),
    "ministry_mapping_unavailable": _(
        "Review Ministries missing from the current source."
    ),
    "financial_mapping_incomplete": _(
        "Complete financial periods, fund mappings and sharing options."
    ),
    "integration_configuration_incomplete": _(
        "Complete the required integration settings."
    ),
    "admin_recipient_required": _(
        "Configure at least one explicit Administrator email address."
    ),
    "tenant_unavailable": _("Check the configured ParishSoft organization."),
    "full_refresh_required": _("Complete a full ParishSoft refresh for this campaign."),
    "source_scope_changed": _(
        "Refresh the source for this campaign and its selected financial periods."
    ),
    "full_refresh_stale": _(
        "The last full refresh is too old. Run another full refresh."
    ),
    "testing_delivery_unresolved": _(
        "Wait for or resolve all unfinished and uncertain Testing deliveries."
    ),
    "production_delivery_unresolved": _(
        "Resolve uncertain or already allocated live mail before continuing."
    ),
    "family_population_unavailable": _(
        "Wait for the current source and Family eligibility population to agree."
    ),
    "cleanup_already_started": _("A go-live cleanup already owns this campaign."),
    "campaign_work_held": _(
        "Another campaign operation currently holds this workflow."
    ),
    "other_campaign_live": _("Another campaign is scheduled or active."),
    "configuration_pending": _("Wait for the pending configuration change to finish."),
    "parishsoft_check_required": _(
        "Verify the current ParishSoft credential and organization."
    ),
    "google_workspace_check_required": _(
        "Verify the current Google Workspace credential and sender settings."
    ),
    "slack_check_required": _("Verify the current Slack credential and channel."),
    "family_test_mail_required": _(
        "Preview and successfully send a selected Family email "
        "using the current configuration."
    ),
    "family_test_mail_pending": _("Wait for the pending Family email test to finish."),
    "campaign_closed": _("The campaign closing instant has passed. Review its dates."),
}


@require_http_methods(["GET", "HEAD", "POST"])
def readiness(request, campaign_id):
    """GET does not send mail, create cleanup intent or extend login idle time."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        verified, token = None, None
        if request.method == "POST":
            action = request.POST.get("action")
            _closed(
                request,
                {"action", "preview_token", "acknowledge"}
                if action == "cleanup"
                else {"action"},
            )
            if action == "cleanup":
                status = start_cleanup(
                    request,
                    service,
                    campaign_id,
                    preview_token=request.POST.get("preview_token", ""),
                    acknowledge=request.POST.get("acknowledge") == "yes",
                )
                return _checked(
                    request,
                    service,
                    HttpResponseRedirect(
                        reverse(
                            "admin:go_live_cleanup",
                            args=[campaign_id, status.request_id],
                        )
                    ),
                )
            if action != "verify":
                raise ValueError("Invalid readiness action.")
            preview, verified, token = verify_preview(request, service, campaign_id)
        else:
            _closed(request, set())
            preview = collect_inputs(request, service, campaign_id)
        counts = preview.families.counts
        response = render(
            request,
            "stewardship/go-live-readiness.html",
            {
                "preview": preview,
                "campaign": preview.campaign,
                "problems": [PROBLEMS[code] for code in preview.problems],
                "counts": counts,
                "email_eligibility": Percentage(counts.email_eligible, counts.active),
                "no_email": Percentage(counts.no_eligible_email, counts.active),
                "inventory": sorted(preview.cleanup.inventory.counts.items()),
                "origin_verified": verified,
                "cleanup_token": token,
                "cleanup_requests": list(
                    ProductionTransitionRequest.objects.filter(campaign_id=campaign_id)
                    .order_by("-created_at", "-id")
                    .only("id", "state", "created_at")[:10]
                ),
            },
        )
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Readiness configuration is unavailable."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def cleanup_status(request, campaign_id, request_id):
    """Passive progress and exact signed controls never expose direct deletion."""
    try:
        filters(request.GET, allowed=set())
        _closed(request, {"control"})
        service = runtime()
        if request.method == "POST":
            control(
                request,
                service,
                campaign_id,
                request_id,
                token=request.POST.get("control", ""),
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:go_live_cleanup", args=[campaign_id, request_id])
                ),
            )
        context = progress(request, service, campaign_id, request_id)
        status = context["status"]
        context["completion"] = Percentage(
            status.processed_count, status.inventory_total
        )
        response = render(request, "stewardship/go-live-cleanup.html", context)
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Cleanup request is unavailable."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD"])
def testing_families(request, campaign_id):
    """Admin-only current Testing Family identities, with bounded server pagination."""
    try:
        parameters = filters(request.GET, allowed={"page"})
        window = PageWindow(expected_version(parameters.get("page", "1")), 50)
        service = runtime()
        with work_transaction():
            principal(request, service, passive=True)
            configuration = editable_configuration(service)
            scope = _scope(campaign_id)
            if (
                configuration.current_campaign_id != campaign_id
                or configuration.mode != "testing"
                or scope.campaign.state != "draft"
            ):
                raise PermissionError("Testing inventory belongs to the current draft.")
            source = source_readiness(scope)
            source_id = (
                source.current_id
                if source.reason in {"ready", "full_refresh_stale"}
                else None
            )
            rows, has_next = cleanup_families(
                campaign_id, source_id=source_id, window=window
            )
        response = render(
            request,
            "stewardship/go-live-families.html",
            {
                "campaign": scope.campaign,
                "rows": rows,
                "page": window.page,
                "has_next": has_next,
                "next_page": window.page + 1,
                "previous_page": window.page - 1,
            },
        )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
