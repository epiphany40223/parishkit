"""A report send's saved copy, as its email was sent (#1786, interim).

Every report email links its send's saved copy
(``report_document.send_path``): its "Open protected report" button and the
additional information block's request links. The Report emails page's own
saved copy, drawn as web components from the snapshot, ships with that page
(report-emails spec, "Saved copies", delivery plan item 5); until then this
page shows the email exactly as the send made it, from its snapshot, with
its retained chart images, so no link in a sent email is broken. A request
link opens that request's own page on Additional information.

Administrators and Staff may open it (decision 3; every block's reader role
is Staff). Each opening is audited (``report_email_viewed``) with the send
only, never its content.
"""

import base64
import re

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .export_services import admit_campaign
from .export_views import _principal
from .report_email_models import ReportEmailSnapshot

# The kept email's chart references, as the compiler writes them.
_CID = re.compile(r'src="cid:([A-Za-z0-9._@-]{1,64})"')


def _snapshot(send_id):
    """The send's snapshot, admitted for reading its campaign's data."""
    snapshot = ReportEmailSnapshot.objects.only(
        "id", "preparation_id", "campaign_id", "subject", "html", "images", "blocks"
    ).get(preparation_id=send_id)
    admit_campaign(snapshot.campaign_id, mutating=False)
    return snapshot


def _record(principal, send_id, campaign_id):
    """Journal the opening of one saved copy, never its content."""
    with transaction.atomic():
        current = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.REPORT_EMAIL_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=send_id,
            parish_id=current.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": Outcome.SUCCEEDED},
        )


def _no_store(response):
    """Saved copies hold Family data: never cached."""
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def saved_copy(request, send_id):
    """The send's email as it was sent, with its charts served beside it."""
    try:
        if request.GET:
            return private_response("Invalid saved copy request.\n", status=400)
        service = runtime()
        principal = _principal(request, service.store)
        snapshot = _snapshot(send_id)
        _record(principal, send_id, snapshot.campaign_id)
        # The compiled email is the validator's closed report markup; only
        # its inline chart references are pointed at this page's images.
        html = _CID.sub(lambda match: f'src="images/{match[1]}.png"', snapshot.html)
        response = HttpResponse(
            render_to_string(
                "stewardship/report-email-copy.html",
                {"subject": snapshot.subject, "email": mark_safe(html)},
                request=request,
            )
        )
        return _no_store(response)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        ReadUnavailable,
        StorageInvariantError,
    ):
        return report_unavailable()


@require_GET
def saved_image(request, send_id, name):
    """One chart image the send kept, as the email carried it."""
    try:
        service = runtime()
        _principal(request, service.store, read_only=True)
        snapshot = _snapshot(send_id)
        data = next(
            (base64.b64decode(png) for cid, png in snapshot.images if cid == name),
            None,
        )
        if data is None:
            raise ObjectDoesNotExist("No such image.")
        return _no_store(HttpResponse(data, content_type="image/png"))
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        ReadUnavailable,
        StorageInvariantError,
    ):
        return report_unavailable()


@require_GET
def saved_item(request, send_id, item_id):
    """A request the send listed: its own page on Additional information."""
    try:
        service = runtime()
        _principal(request, service.store, read_only=True)
        snapshot = _snapshot(send_id)
        listed = {
            str(item[0])
            for block in snapshot.blocks
            if block["block"] == "additional_information" and block["data"]
            for item in block["data"]["items"]
        }
        if str(item_id) not in listed:
            raise ObjectDoesNotExist("The send did not list this request.")
        return redirect("admin:information_item", item_id=item_id)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        ReadUnavailable,
        StorageInvariantError,
    ):
        return report_unavailable()
