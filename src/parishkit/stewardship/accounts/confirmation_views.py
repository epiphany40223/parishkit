"""Native forms for exact Production confirmation and durable catch-up progress."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.production_models import ProductionTransitionEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from . import confirmation_commands, confirmation_progress
from .authentication import runtime
from .confirmation_readiness import collect_readiness
from .go_live_views import PROBLEMS
from .installation_lock import ConfigurationBusy
from .integration_views import ERRORS, _checked
from .sessions import require_fresh
from .setup_views import _closed, error_response


def _fresh_after_cleanup(request, transition_id):
    """Show reauthentication guidance without treating a browser flag as proof."""
    try:
        instant = require_fresh(request)
    except PermissionError:
        return False
    return (
        instant
        >= ProductionTransitionEvent.objects.filter(
            request_id=transition_id, action="complete"
        )
        .latest("created_at")
        .created_at
    )


@require_http_methods(["GET", "HEAD", "POST"])
def confirmation(request, campaign_id, request_id, preparation_id):
    """GET/HEAD cannot verify DNS, enumerate impact or activate Production."""
    try:
        service = runtime()
        arguments = request, service, campaign_id, request_id, preparation_id
        action = request.POST.get("action") if request.method == "POST" else None
        _closed(
            request,
            {"action", "preview", "typed"} if action == "confirm" else {"action"},
        )
        if action == "confirm":
            confirmation_commands.confirm(
                *arguments,
                token=request.POST.get("preview", ""),
                typed=request.POST.get("typed", ""),
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:production_progress", args=[campaign_id])
                ),
            )
        preview, verified, token = None, None, None
        if action == "verify":
            preview, verified, token = confirmation_commands.verify_preview(*arguments)
            state = preview.readiness
        elif request.method == "POST":
            raise ValueError("Invalid Production confirmation action.")
        else:
            with work_transaction():
                state = collect_readiness(*arguments)
        context = {
            "campaign": state.campaign,
            "transition": state.transition,
            "state": state,
            "preview": preview,
            "origin_verified": verified,
            "confirmation_token": token,
            "fresh": _fresh_after_cleanup(request, request_id),
            "problems": [
                PROBLEMS[code]
                for code in (preview.problems if preview else state.problems)
            ],
        }
        if preview:
            counts = preview.families.counts
            context.update(
                counts=counts,
                email_eligibility=Percentage(counts.email_eligible, counts.active),
                no_email=Percentage(counts.no_eligible_email, counts.active),
            )
        return _checked(
            request,
            service,
            render(request, "stewardship/production-confirmation.html", context),
        )
    except ConfigurationBusy:
        return error_response(
            StaleRecordError(
                "Another configuration operation is finishing; retry confirmation."
            )
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Production confirmation is unavailable."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def progress(request, campaign_id):
    """Passive progress stays separate from an explicit CSRF-protected retry."""
    try:
        _closed(request, {"control"})
        service = runtime()
        if request.method == "POST":
            confirmation_progress.retry(
                request, service, campaign_id, token=request.POST.get("control", "")
            )
            response = HttpResponseRedirect(
                reverse("admin:production_progress", args=[campaign_id])
            )
        else:
            response = render(
                request,
                "stewardship/production-progress.html",
                confirmation_progress.progress(request, service, campaign_id),
            )
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Production progress is unavailable."))
    except ERRORS as error:
        return error_response(error)
