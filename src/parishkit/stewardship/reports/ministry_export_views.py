"""The follow-up packet's form: native private selection, no client scope.

One Ministry's joining or leaving export form was retired with its lists
(#1157); the follow-up queue's Export these requests replaced it.
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.dates import parish_zone, unasked_zone
from parishkit.stewardship.web.error_pages import page_request
from parishkit.stewardship.web.filter_errors import FilterError
from parishkit.stewardship.web.refusals import Refusal
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES
from .ministry_exports import MAX_PACKET_MINISTRIES, create_ministry_export
from .ministry_followup_views import queue_response
from .ministry_views import _principal

# Same meaning as the shared export-request-bound.html notice on the HTML pages.
BOUND = (
    "This export form was already used for a different export. Nothing new was"
    " queued. Reload the page and try again.\n"
)
# The packet form's help states the same limit (ministry-packet.html).
TOO_MANY = format_lazy(
    _(
        "A follow-up packet can include at most {limit} ticked Ministries, so "
        "nothing was queued."
    ),
    limit=MAX_PACKET_MINISTRIES,
)
TOO_MANY_FIX = _(
    "Tick fewer Ministries, or leave every box unticked to include all the "
    "Ministries you are authorized for."
)


# The same refusal at the packet's boxes (#1332): the reason and the fix in
# one message, as the field and the error summary show it.
PACKET_MESSAGE = format_lazy("{} {}", TOO_MANY, TOO_MANY_FIX)


class TooManyMinistries(ValueError):
    """A packet selection ticks more Ministries than one packet may hold.

    ``chosen`` is the submitted selection, unchecked, so the page drawn
    again can keep the ticks it offers (it ticks only its own choices).
    """

    def __init__(self, chosen):
        """Keep the submitted selection for the page drawn again."""
        super().__init__("Too many Ministries for one packet.")
        self.chosen = tuple(chosen)


def packet_selection(parameters):
    """Closed form grammar: ticked Ministries, or none for every authorized one.

    One control has one meaning, so the form cannot reach an ambiguous state
    such as "all" selected beside individually ticked boxes.
    """
    fields = {"format", "request_key"}
    keys = set(parameters)
    if (
        not fields <= keys
        or keys - fields - {"ministries", "history", "browser_timezone"}
        or any(len(parameters.getlist(key)) != 1 for key in keys - {"ministries"})
        or parameters.get("history", "yes") != "yes"
    ):
        raise ValueError("Invalid Ministry packet fields.")
    chosen = parameters.getlist("ministries")
    if not chosen:
        return None
    if len(chosen) > MAX_PACKET_MINISTRIES:
        raise TooManyMinistries(chosen)
    if any(
        len(value) > 10
        or not value.isascii()
        or not value.isdecimal()
        or str(int(value)) != value
        or not 0 < int(value) < 2**31
        for value in chosen
    ):
        raise ValueError("Invalid Ministry packet selection.")
    ministries = tuple(sorted({int(value) for value in chosen}))
    if len(ministries) != len(chosen):
        raise ValueError("A Ministry was selected more than once.")
    return ministries


@require_POST
def create_packet(request, campaign_id):
    """Queue one multi-Ministry follow-up packet through a CSRF form."""
    try:
        service = runtime()
        actor = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        if request.GET:
            raise ValueError("Ministry packets require a POST body.")
        ministries = packet_selection(parameters)
        result = create_ministry_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            action="packet",
            ministries=ministries,
            history="history" in parameters,
            format=parameters["format"],
            # Asked for nowhere: the browser's zone, sent unasked, else the
            # parish's (#1127).
            browser_timezone=unasked_zone(
                parameters.get("browser_timezone"), parish_zone()
            ),
            request_key=UUID(parameters["request_key"]),
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return private_response(BOUND, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except TooManyMinistries as error:
        # Ticking past the limit is an ordinary choice, not a malformed
        # form, so the refusal says what to do instead. The packet's gate
        # holds Queue past the limit, so only a request built by hand (or
        # a browser whose gate did not run) gets here. A person gets
        # Ministry requests drawn again with the error at the boxes
        # (#1332); a script gets the typed refusal as JSON.
        if campaign_id is not None and page_request(request):
            refusal = FilterError("ministries", PACKET_MESSAGE, error.chosen)
            return queue_response(request, campaign_id, packet=refusal)
        return validation_response(
            [FieldError(ErrorCode.INVALID, "ministries")],
            refusal=Refusal(
                TOO_MANY,
                fix=TOO_MANY_FIX,
                link=reverse("admin:ministry_report"),
                link_label=_("Return to Ministry requests"),
            ),
        )
    except ValueError:
        return private_response("Invalid Ministry packet selection.\n", status=400)
