"""Native delivery-control previews; GET/HEAD never changes campaign or mail."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError

from . import delivery_control_commands as commands
from .authentication import runtime
from .installation_lock import ConfigurationBusy
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response


@require_http_methods(["GET", "HEAD", "POST"])
def control(request, campaign_id):
    """Require closed input, CSRF and current authority for every explicit intent."""
    try:
        service = runtime()
        action = request.POST.get("action") if request.method == "POST" else None
        fields = {"action", "reason"}
        if action == "confirm":
            fields = {"action", "preview"}
        elif action == "preview_resolve":
            fields |= {"decision", *commands.RESOLVABLE_TYPES}
        _closed(
            request,
            fields,
        )
        if action == "confirm":
            commands.confirm(
                request, service, campaign_id, token=request.POST.get("preview", "")
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:delivery_control", args=[campaign_id])
                ),
            )
        context = commands.page(request, service, campaign_id)
        if action in {"preview_pause", "preview_resume"}:
            preview = (
                commands.preview_pause
                if action == "preview_pause"
                else commands.preview_resume
            )
            context["preview"], context["control_token"] = preview(
                request, service, campaign_id, reason=request.POST.get("reason", "")
            )
        elif action == "preview_resolve":
            types = sorted(
                kind for kind in commands.RESOLVABLE_TYPES if kind in request.POST
            )
            if any(request.POST[kind] != "yes" for kind in types):
                raise ValueError("Invalid held-message selection.")
            context["preview"], context["control_token"] = commands.preview_resolution(
                request,
                service,
                campaign_id,
                reason=request.POST.get("reason", ""),
                decision=request.POST.get("decision", ""),
                types=types,
            )
        elif request.method == "POST":
            raise ValueError("Invalid delivery-control action.")
        return _checked(
            request,
            service,
            render(request, "stewardship/delivery-control.html", context),
        )
    except ConfigurationBusy:
        return error_response(
            StaleRecordError("Another operation is finishing; retry delivery control.")
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Delivery controls are unavailable."))
    except ERRORS as error:
        return error_response(error)
