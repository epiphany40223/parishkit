"""CSRF-protected native pre-start withdrawal with explicit irreversible consent."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError

from . import withdrawal_commands
from .authentication import runtime
from .installation_lock import ConfigurationBusy
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response


@require_http_methods(["GET", "HEAD", "POST"])
def withdrawal(request, campaign_id):
    """GET/HEAD only report status; signed confirmation owns the actual transition."""
    try:
        service = runtime()
        action = request.POST.get("action") if request.method == "POST" else None
        fields = (
            {"action", "preview"}
            if action == "confirm"
            else {"action", "reason", "acknowledged"}
        )
        _closed(request, fields)
        if action == "confirm":
            withdrawal_commands.withdraw(
                request, service, campaign_id, token=request.POST.get("preview", "")
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:production_withdrawal", args=[campaign_id])
                ),
            )
        context = withdrawal_commands.page(request, service, campaign_id)
        if action == "preview":
            context["preview"], context["withdrawal_token"] = (
                withdrawal_commands.preview(
                    request,
                    service,
                    campaign_id,
                    reason=request.POST.get("reason", ""),
                    acknowledged=request.POST.get("acknowledged") == "yes",
                )
            )
        elif request.method == "POST":
            raise ValueError("Invalid withdrawal action.")
        return _checked(
            request,
            service,
            render(request, "stewardship/production-withdrawal.html", context),
        )
    except ConfigurationBusy:
        return error_response(
            StaleRecordError(
                "Another configuration operation is finishing; retry withdrawal."
            )
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Production withdrawal is unavailable."))
    except ERRORS as error:
        return error_response(error)
