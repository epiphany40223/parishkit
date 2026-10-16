"""Delete email templates from Pages and emails' action tables (#879).

Each schedulable mail type's table on Pages and emails offers Delete for an
email no schedule sends, one at a time or for the ticked rows (admin-portal
spec, "Default content"). The shared confirmation dialog posts the chosen
revision IDs here. The view builds exactly the removal the editor's "Remove
this selected content" control builds (``content_forms.revision_patch`` with
no new text) for every chosen email, signs it as a review would and records
it at once through ``admin_editing.confirm_intent``, which rechecks the
applied configuration and the campaign's work under the work lock. The
answer is the change's status page, which the dialog follows until the change
is applied. Any chosen email that can't be removed refuses the whole request.
"""

from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    stale_page,
    unexpected_fields,
)

from . import admin_navigation
from .admin_editing import confirm_intent, error_response, principal, sign_preview
from .authentication import runtime
from .campaign_views import _scope, _state
from .content_forms import revision_patch
from .content_views import _content_campaign, _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .schedule_forms import KINDS
from .sessions import authenticated_admin

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    LookupError,
    PermissionError,
    StaleRecordError,
    ValueError,
    signing.BadSignature,
)


def delete_salt(campaign_id):
    """The signing salt of one campaign's email template deletions."""
    return f"stewardship-content-delete-v1:{campaign_id}"


def _chosen(parameters):
    """The revision IDs a delete names: at least one, each once.

    The form carries only its CSRF token, its base version and the chosen
    ``revision_id`` values; anything else is a stale or crafted page.
    """
    allowed = {"csrfmiddlewaretoken", "base_digest", "revision_id"}
    if set(parameters) - allowed or any(
        len(values) != 1 for name, values in parameters.lists() if name != "revision_id"
    ):
        raise unexpected_fields(parameters, allowed=allowed, multiple={"revision_id"})
    values = parameters.getlist("revision_id")
    if not values:
        raise UserFacingError(
            _("Choose at least one email to delete."),
            fix=_("Reload the page and try again."),
        )
    if len(set(values)) != len(values):
        raise UserFacingError(
            _("The same email was chosen more than once."),
            fix=_("Nothing was deleted. Reload the page and choose again."),
        )
    return [str(UUID(value)) for value in values]


def deletion_patch(document, campaign, records, identifiers):
    """The one configuration patch that removes every chosen email template.

    Each chosen ID must be one of this campaign's schedulable emails
    (invitation, reminder or Admin digest); the confirmation email is a
    single entry that is edited, never deleted. ``revision_patch`` refuses
    an email a schedule still sends, with the same explanation the editor
    gives, so nothing is removed.
    """
    by_id = {row["id"]: row for row in records}
    patch = []
    for identifier in identifiers:
        row = by_id.get(identifier)
        if (
            row is None
            or row["values"]["kind"] != "email"
            or row["values"]["slot"] not in KINDS
        ):
            raise UserFacingError(
                _("A chosen email is no longer one that can be deleted here."),
                fix=_("Nothing was deleted. Reload the page and choose again."),
            )
        removal, _affected = revision_patch(document, campaign, row, None)
        patch += removal
    return patch


@require_http_methods(["POST"])
def content_delete(request, campaign_id):
    """Delete the chosen email templates in one change, as the dialog confirmed.

    The change is built and signed as the editor's review builds it, then
    recorded at once; a refusal changes nothing. The answer is the change's
    status page, which the dialog follows until the change is applied.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        identifiers = _chosen(request.POST)
        listing = reverse("admin:content_catalog")
        with work_transaction():
            state = _state(service)
            configuration, fingerprint = state[0], state[-1]
            campaign = _content_campaign(state, campaign_id)
            if (
                request.POST.get("base_digest")
                != configuration.active_configuration.digest
            ):
                raise stale_page()
            base = service.store.active()
            if base is None or base.digest != configuration.active_configuration.digest:
                raise StaleRecordError("The applied configuration changed.")
            patch = deletion_patch(
                base.document(),
                campaign,
                _records(configuration, campaign.pk),
                identifiers,
            )
            # The whole-configuration check a review makes, before signing. A
            # configuration that would no longer validate is the Admin's to
            # fix, not a server fault, so it is refused in plain words.
            try:
                build_candidate(base, patch, candidate_id=uuid4())
            except ConfigError as error:
                raise UserFacingError(
                    _("These emails can't be deleted: the settings would be invalid."),
                    fix=_("Nothing was deleted. Reload the page and check the emails."),
                ) from error
            token = sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=delete_salt(campaign_id),
                snapshot=fingerprint,
            )
        receipt = confirm_intent(
            request,
            service,
            actor,
            token=token,
            salt=delete_salt(campaign_id),
            link=listing,
            current_scope=_scope,
        )
        admin_navigation.remember_origin(request, receipt.request_id, path=listing)
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Content access was revoked.")
        return HttpResponseRedirect(
            reverse("admin:configuration_request", args=[receipt.request_id])
        )
    except ERRORS as error:
        return error_response(error)
