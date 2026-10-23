"""Preview of a report email in the review dialog (#1786, #2035).

The report-emails spec's "Preview": the Report emails row action posts the
saved report's id here (``report_email_render``); the editor's Preview
posts the unsaved form to the editor itself, which calls
``preview_answer`` once the form's own checks pass. Either answer is the
shared review dialog's fragment (``report-email-preview.html``), with no
signed preview, so the dialog offers no Apply: the email as recipients
would see it, its plain text, its subject and who it would go to now.

The page's content policy admits no inline image data, so each chart
image in the dialog's email points at ``report_email_preview_image``,
which draws it again from a signed recipe bound to the Administrator
(``report_email_preview.image_token``).

Only Administrators preview. Each preview records ``report_email_previewed``
(it reads Family data), naming the report, or the campaign for an unsaved
form; nothing else is written.
"""

import re
from uuid import UUID

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_editing import error_response, principal
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.hosted_files import public_origin
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingMissing, unexpected_fields

from .chart_rendering import ChartRenderError
from .export_services import admit_campaign
from .facts import FactUnavailable
from .report_email_copy import recipients_by_kind
from .report_email_list import report_records, version_user_names
from .report_email_preview import (
    NoSource,
    image_token,
    preview_image,
    preview_report,
)

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    LookupError,
    PermissionError,
    StaleRecordError,
    StorageInvariantError,
    ValueError,
)
# The compiled email's inline image references, as the compiler writes them.
_CID = re.compile(r'src="cid:([A-Za-z0-9._@-]{1,64})"')
NO_SOURCE = _(
    "This report can't be previewed yet: no ParishSoft data has been loaded. "
    "Load ParishSoft data first."
)
BUSY = _(
    "This report's figures are being updated right now, so it can't be "
    "previewed. Try again in a moment."
)
IMAGE_GONE = _("This preview image is no longer available. Preview the report again.")
NO_CHART = _(
    "A chart in this report could not be drawn just now, so it can't be "
    "previewed. Try again in a moment."
)


def _audit(actor, subject_id, campaign_id, parish_id):
    """Journal one preview: who previewed which report, never its content."""
    with transaction.atomic():
        record_action(
            Action.REPORT_EMAIL_PREVIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=subject_id,
            parish_id=parish_id,
            campaign_id=campaign_id,
            context={"outcome": Outcome.SUCCEEDED},
        )


def draw(values, system, campaign):
    """Compile ``values`` from current data, inside one read snapshot.

    No ParishSoft data yet (``NoSource``), figures locked or replaced
    while they are read (any other ``FactUnavailable``, including
    ``FactBusy``) and a chart that cannot be drawn become the dialog's
    plain-language refusals.
    """
    try:
        with dates.using(system.active_configuration.parish.date_format):
            return preview_report(
                values, system, campaign, public_origin=public_origin()
            )
    except NoSource:
        raise UserFacingMissing(NO_SOURCE) from None
    except FactUnavailable:
        raise UserFacingMissing(BUSY) from None
    except ChartRenderError:
        raise UserFacingMissing(NO_CHART) from None


def _email_markup(preview, values, campaign_id, actor):
    """The compiled email, its chart references pointed at the preview's images.

    The email is the validator's closed report markup, as the saved copy
    shows it; an image the recipe cannot draw keeps its alt text.
    """
    token = image_token(preview, values, campaign_id, actor.identity)

    def address(match):
        """One chart's preview address, or no source without a recipe."""
        if token is None:
            return 'src=""'
        url = reverse("admin:report_email_preview_image", args=[token, match[1]])
        return f'src="{escape(url)}"'

    return mark_safe(_CID.sub(address, preview.html))


def preview_answer(request, actor, values, *, subject_id):
    """The review dialog's Preview fragment for ``values``, audited.

    ``subject_id`` names the report previewed (the campaign for an unsaved
    form). The read runs in one snapshot; the audit event is recorded
    after it, as every audited read is.
    """
    with read_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish", "current_campaign__active_configuration"
        ).get()
        campaign = system.current_campaign
        if campaign is None:
            raise LookupError("There is no current campaign.")
        admit_campaign(campaign.pk, mutating=False)
        preview = draw(values, system, campaign)
        names = version_user_names(system.active_configuration)
        parish_id = system.active_configuration.parish.pk
    context = {
        "preview": preview,
        "name": values["name"],
        "recipients": recipients_by_kind(preview.recipients, names, administrator=True),
        "email": _email_markup(preview, values, campaign.pk, actor),
    }
    response = render(request, "stewardship/report-email-preview.html", context)
    _audit(actor, subject_id, campaign.pk, parish_id)
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def report_email_render(request, campaign_id):
    """Preview a saved report email: the row action's dialog (spec, "Preview").

    The post names the report; one that is not the current campaign's is
    refused as an unknown request.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        allowed = {"report", "csrfmiddlewaretoken"}
        posted = request.POST
        if (
            "report" not in posted
            or set(posted) - allowed
            or any(len(values) != 1 for _name, values in posted.lists())
        ):
            raise unexpected_fields(posted, allowed=allowed)
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        report = str(UUID(posted["report"]))
        with read_transaction():
            system = SystemConfiguration.objects.select_related(
                "active_configuration"
            ).get()
            records = {
                row["id"]: row["values"] for row in report_records(system, campaign_id)
            }
        if report not in records:
            # No sources: the field was allowed; the id is no report's.
            raise unexpected_fields()
        response = preview_answer(
            request, actor, records[report], subject_id=UUID(report)
        )
        # The read is rechecked after it, so a revocation during it refuses.
        principal(request, service, read_only=True)
        return response
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)


@require_GET
def report_email_preview_image(request, campaign_id, token, name):
    """One chart image of an open preview, drawn again from its signed recipe.

    Only the Administrator the recipe was made for, for the current
    campaign, within its fifteen minutes. The image holds aggregate figures
    only and is not audited; it is never cached.
    """
    try:
        service = runtime()
        actor = principal(request, service, read_only=True)
        # The recipe travels in the path: a token is longer than a filter.
        filters(request.GET, allowed=set())
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        with read_transaction():
            system = SystemConfiguration.objects.select_related(
                "active_configuration__parish", "current_campaign__active_configuration"
            ).get()
            campaign = system.current_campaign
            if campaign is None or campaign.pk != campaign_id:
                raise LookupError("There is no current campaign.")
            admit_campaign(campaign.pk, mutating=False)
            data = preview_image(token, actor.identity, system, campaign, name)
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "no-store"
        return response
    except (signing.BadSignature, LookupError, ObjectDoesNotExist, FactUnavailable):
        # An expired or altered recipe, or facts no longer kept: the image
        # keeps its alt text in the dialog.
        return error_response(UserFacingMissing(IMAGE_GONE))
    except ChartRenderError:
        return error_response(UserFacingMissing(NO_CHART))
    except PermissionError:
        return denial()
    except ERRORS as error:
        return error_response(error)
