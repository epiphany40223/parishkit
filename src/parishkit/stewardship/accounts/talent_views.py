"""Versioned Member talent editing, reusing the share-option editor's machinery.

A campaign that never edited its talents shows the built-in defaults; saving
stores an explicit ``talent_options`` list (same shape as share options).
Like share options these are structural: they never change while live.
"""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.responses.service import default_talent_options
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import confirm, error_response, principal, sign_preview
from .authentication import runtime
from .campaign_views import _scope, _state, _target
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin
from .share_forms import ShareOptions, share_action


def current_talents(campaign):
    """The campaign's talent list, or the defaults it resolves to today."""
    values = campaign.active_configuration.values
    # An explicitly emptied list stays empty; only a never-edited one defaults.
    if "talent_options" in values:
        return values["talent_options"]
    return default_talent_options()


def _page(request, configuration, campaign, formset, *, status=200):
    """Show the ordered talents with a blank row for adding one."""
    response = render(
        request,
        "stewardship/talent-settings.html",
        {
            "configuration": configuration,
            "campaign": campaign,
            "formset": formset,
            "base_digest": configuration.active_configuration.digest,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, state, campaign, formset, salt):
    """Sign the reviewed ordered talents without changing configuration yet."""
    configuration, fingerprint = state[0], state[-1]
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the talents before editing them.")
    if not formset.is_valid():
        return _page(request, configuration, campaign, formset, status=400)
    options = formset.values()
    before = current_talents(campaign)
    if options == before:
        return render(
            request,
            "stewardship/talent-preview.html",
            {"unchanged": True, "campaign": campaign},
        )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {"talent_options": options},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())
    return render(
        request,
        "stewardship/talent-preview.html",
        {
            "campaign": campaign,
            "before": before,
            "after": options,
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=salt,
                snapshot=fingerprint,
            ),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def talent_settings(request, campaign_id):
    """Current Admins may preview and request changes only for an unlocked draft."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = f"stewardship-talent-options-preview-v1:{campaign_id}"
        if request.method == "POST" and share_action(request.POST) == "confirm":
            return confirm(request, service, actor, salt=salt, current_scope=_scope)
        if request.method != "POST":
            filters(request.GET, allowed=set())
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            if (
                not editable
                or "ministry" not in campaign.active_configuration.values["modules"]
            ):
                raise StaleRecordError("Member talents are not currently editable.")
            formset = ShareOptions(
                request.POST if request.method == "POST" else None,
                prefix="options",
                previous=current_talents(campaign),
            )
            response = (
                _preview(request, service, actor, state, campaign, formset, salt)
                if request.method == "POST"
                else _page(request, configuration, campaign, formset)
            )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
