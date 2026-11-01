"""A report send's saved copy page and its listed requests' pages (#1786, #1946).

Every report email links its send's saved copy
(``report_document.send_path``): its "Open protected report" button and the
additional information block's request links, and Report emails lists it
under its report's past sends. The page is drawn from the send's snapshot
as web components (report-emails spec, "Saved copies";
``report_email_copy``), each block through its saved copy renderer, with the
email exactly as it was sent, with its retained chart images, in a closed
disclosure. A listed request has its own page under the copy, which links
the live request on Additional information.

Every Administrator may open it; Staff only when every block in it may be
read by Staff (decision 11, each block's reader role, which is Staff for
every block today). Each opening of the copy or of one of its requests is
audited (``report_email_viewed``) with the send only, never its content.
"""

import base64
import re
from datetime import datetime
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .export_services import admit_campaign
from .export_views import _principal
from .facts import FactUnavailable
from .report_blocks import REGISTRY
from .report_email_models import ReportEmailPreparation, ReportEmailSnapshot
from .weekly_presentation import DISPOSITIONS

# The kept email's chart references, as the compiler writes them.
_CID = re.compile(r'src="cid:([A-Za-z0-9._@-]{1,64})"')

# The snapshot columns the page reads; the web login may read no others
# (``report_email_grants``).
_COPY_FIELDS = (
    "id",
    "preparation_id",
    "campaign_id",
    "configuration_id",
    "fact_set_id",
    "subject",
    "html",
    "images",
    "blocks",
    "values",
    "recipients",
)

# The errors that make a saved copy unavailable rather than refused.
_UNAVAILABLE = (
    ConfigError,
    DatabaseError,
    FactUnavailable,
    LimiterUnavailable,
    ReadUnavailable,
    StorageInvariantError,
)


def _snapshot(send_id, principal, fields=_COPY_FIELDS):
    """The send's snapshot, admitted for reading its campaign's data.

    A reader who is not an Administrator may open it only when every block
    in it allows their role (decision 11): a block unknown to this release
    allows nobody but Administrators.
    """
    snapshot = ReportEmailSnapshot.objects.only(*fields).get(preparation_id=send_id)
    if not readable(snapshot.blocks, principal.roles):
        raise PermissionError("This saved copy holds a block the reader may not read.")
    admit_campaign(snapshot.campaign_id, mutating=False)
    return snapshot


def readable(blocks, roles):
    """Whether a reader with ``roles`` may open a copy of ``blocks`` (decision 11)."""
    if "administrator" in roles:
        return True
    return all(
        entry.get("block") in REGISTRY and REGISTRY[entry["block"]].reader_role in roles
        for entry in blocks
    )


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


def _pinned(snapshot):
    """The snapshot's preparation and the configuration it was compiled under.

    The configuration pins the parish's date format and names, so the page
    says what the email said (#280).
    """
    snapshot.configuration = AppliedConfigurationVersion.objects.select_related(
        "parish"
    ).get(pk=snapshot.configuration_id)
    return ReportEmailPreparation.objects.get(pk=snapshot.preparation_id)


def _linkable(snapshot, preparation):
    """Whether Family names may link their Timeline (#1570).

    The Timeline serves only the current campaign's live Families, so an
    earlier campaign's copy, or a Testing send's rehearsal Families, show
    plain names.
    """
    current = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).get()
    return current == snapshot.campaign_id and preparation.mode == "production"


def _participation(snapshot, preparation):
    """The pinned fact generation's participation document, or None.

    Only a send whose Participation charts block kept data needs it: the
    snapshot pins the generation (``ReportEmailSnapshot.fact_set``) for as
    long as the snapshot is kept, so it is never current data.
    """
    from .documents import participation_document
    from .models import CampaignDailyFactSet

    charted = any(
        entry["block"] == "participation_charts" and entry.get("data") is not None
        for entry in snapshot.blocks
    )
    if not charted or snapshot.fact_set_id is None:
        return None
    facts = CampaignDailyFactSet.objects.get(pk=snapshot.fact_set_id, state="ready")
    zone = CampaignConfiguration.objects.values_list("timezone", flat=True).get(
        pk=preparation.timezone_configuration_id
    )
    return participation_document(
        facts,
        parish_name=snapshot.configuration.parish.name,
        browser_timezone=zone,
        requested_at=preparation.captured_at,
    )


def copy_context(snapshot, preparation, principal):
    """The saved copy page's context, drawn from the snapshot alone.

    Runs inside the snapshot's pinned date format, as the email was drawn.
    """
    from parishkit.stewardship.source.data_age import data_age_at

    from .report_email_copy import copy_blocks, copy_facts, copy_frame

    facts = copy_facts(
        snapshot, preparation, administrator="administrator" in principal.roles
    )
    frame = copy_frame(
        snapshot,
        preparation,
        participation=_participation(snapshot, preparation),
        linkable=_linkable(snapshot, preparation),
    )
    blocks = copy_blocks(snapshot, frame)
    # The compiled email is the validator's closed report markup; only its
    # inline chart references are pointed at this page's images.
    html = _CID.sub(lambda match: f'src="images/{match[1]}.png"', snapshot.html)
    return {
        "facts": facts,
        "as_of_day": dates.format_date(facts.as_of_day) if facts.as_of_day else "",
        # The ParishSoft data age and connection as known when the email
        # was made, as the saved daily report page states it (#510).
        "age": data_age_at(preparation.captured_at),
        "blocks": blocks,
        "charts": any(
            block["key"] in {"participation_charts", "compared_with_past_campaigns"}
            and block["template"]
            for block in blocks
        ),
        "email": mark_safe(html),
    }


@require_GET
def saved_copy(request, send_id):
    """The send's saved copy: its blocks as web components, and the email as sent."""
    try:
        if request.GET:
            return private_response("Invalid saved copy request.\n", status=400)
        service = runtime()
        principal = _principal(request, service.store)
        snapshot = _snapshot(send_id, principal)
        preparation = _pinned(snapshot)
        with dates.using(snapshot.configuration.parish.date_format):
            body = render_to_string(
                "stewardship/report-email-copy.html",
                copy_context(snapshot, preparation, principal),
                request=request,
            )
        _record(principal, send_id, snapshot.campaign_id)
        return _no_store(HttpResponse(body))
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except _UNAVAILABLE:
        return report_unavailable()


@require_GET
def saved_image(request, send_id, name):
    """One chart image the send kept, as the email carried it."""
    try:
        service = runtime()
        principal = _principal(request, service.store, read_only=True)
        snapshot = _snapshot(
            send_id,
            principal,
            ("id", "preparation_id", "campaign_id", "images", "blocks"),
        )
        data = next(
            (base64.b64decode(png) for cid, png in snapshot.images if cid == name),
            None,
        )
        if data is None:
            raise ObjectDoesNotExist("No such image.")
        return _no_store(HttpResponse(data, content_type="image/png"))
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except _UNAVAILABLE:
        return report_unavailable()


def item_context(snapshot, preparation, item_id):
    """One listed request's page: the kept request and its current state.

    Only a request the send listed is shown; its text is the snapshot's,
    never the live item's, and its state is read now.
    """
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    from .report_email_copy import copy_facts, listed_requests

    key = str(item_id)
    if key not in listed_requests(snapshot):
        raise ObjectDoesNotExist("The send did not list this request.")
    # A send captured since #2331 keeps each request's stage last.
    ((_, duid, name, submitted, text, *_stage),) = [
        item
        for entry in snapshot.blocks
        if entry["block"] == "additional_information" and entry.get("data")
        for item in entry["data"]["items"]
        if item[0] == key
    ]
    found = (
        AdditionalInformationItem.objects.filter(
            pk=item_id, submission__campaign_id=snapshot.campaign_id
        )
        .values_list("disposition", "submission__family_id")
        .first()
    )
    disposition, family_id = found if found else (None, None)
    facts = copy_facts(snapshot, preparation, administrator=False)
    return {
        "title": facts.title,
        "row": {
            "family_id": family_id if _linkable(snapshot, preparation) else None,
            "family_name": " ".join(name.split()),
            "family_duid": duid,
            "submitted_at": datetime.fromisoformat(submitted),
            "text": text,
        },
        "current": DISPOSITIONS.get(disposition, "No longer available"),
        # A request that is gone has no live page to open.
        "live_url": (
            reverse("admin:information_item", kwargs={"item_id": item_id})
            if found
            else None
        ),
    }


@require_GET
def saved_item(request, send_id, item_id):
    """A request the send listed: its own page, linking the live request."""
    try:
        if request.GET:
            return private_response("Invalid saved request.\n", status=400)
        service = runtime()
        principal = _principal(request, service.store)
        snapshot = _snapshot(send_id, principal)
        preparation = _pinned(snapshot)
        with dates.using(snapshot.configuration.parish.date_format):
            context = item_context(snapshot, preparation, UUID(str(item_id)))
            # The trail's parent crumb is the saved copy, by its own name.
            admin_navigation.place(
                request, labels={"report_email_send": context["title"]}
            )
            body = render_to_string(
                "stewardship/report-email-copy-item.html", context, request=request
            )
        _record(principal, send_id, snapshot.campaign_id)
        return _no_store(HttpResponse(body))
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except _UNAVAILABLE:
        return report_unavailable()
