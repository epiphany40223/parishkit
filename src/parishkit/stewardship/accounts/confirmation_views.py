"""Production activation: durable catch-up progress and its retry.

The final confirmation itself is the Go live page's Confirm Production step
(``go_live_confirm_views``, #1585 slice 4).
"""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import admin_navigation, confirmation_progress
from .admin_caller import AdminCaller
from .authentication import runtime
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response


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
            response = HttpResponseRedirect(reverse("admin:production_progress"))
        else:
            # The last step of going live: following activation (#196).
            admin_navigation.place(request, flow="go_live", step="activate")
            response = render(
                request,
                "stewardship/production-progress.html",
                confirmation_progress.progress(
                    AdminCaller.from_request(request), service, campaign_id
                ),
            )
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Production progress is unavailable."))
    except ERRORS as error:
        return error_response(error)
