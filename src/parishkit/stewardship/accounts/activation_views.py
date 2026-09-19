"""Passive Admin preparation progress and CSRF-protected exact intent controls."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters

from .activation_progress import control, progress
from .authentication import runtime
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response


@require_http_methods(["GET", "HEAD", "POST"])
def links(request, campaign_id, request_id):
    """GET only reads progress; it cannot generate or disclose Family credentials."""
    try:
        values = filters(request.GET, allowed={"page"})
        window = PageWindow(expected_version(values.get("page", "1")), 25)
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
            response = HttpResponseRedirect(
                reverse("admin:go_live_links", args=[campaign_id, request_id])
            )
        else:
            context = progress(request, service, campaign_id, request_id, window=window)
            response = render(request, "stewardship/go-live-links.html", context)
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Link preparation is unavailable."))
    except ERRORS as error:
        return error_response(error)
