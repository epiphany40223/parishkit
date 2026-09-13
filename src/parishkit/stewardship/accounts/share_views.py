"""Versioned share-option editing; these structural values never change live."""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
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


def _page(request, configuration, campaign, formset, *, status=200):
    """Show explicit stable rows, order inputs and deletions with a blank new row."""
    response = render(
        request,
        "stewardship/share-settings.html",
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
    """Sign the reviewed ordered options without directly changing configuration."""
    configuration, fingerprint = state[0], state[-1]
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the share options before editing them.")
    if not formset.is_valid():
        return _page(request, configuration, campaign, formset, status=400)
    options = formset.values()
    if options == campaign.active_configuration.values["share_options"]:
        # Keep a clear, non-mutating no-op error without reaching into formset
        # private error storage or creating an empty configuration request.
        return render(
            request,
            "stewardship/share-preview.html",
            {
                "unchanged": True,
                "campaign": campaign,
            },
        )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {"share_options": options},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())
    return render(
        request,
        "stewardship/share-preview.html",
        {
            "campaign": campaign,
            "before": campaign.active_configuration.values["share_options"],
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
def share_settings(request, campaign_id):
    """Current Admins may preview and request changes only for an unlocked draft."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = f"stewardship-share-options-preview-v1:{campaign_id}"
        if request.method == "POST" and share_action(request.POST) == "confirm":
            return confirm(request, service, actor, salt=salt, current_scope=_scope)
        if request.method != "POST":
            filters(request.GET, allowed=set())
        with work_transaction():
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            if (
                not editable
                or "financial" not in campaign.active_configuration.values["modules"]
            ):
                raise StaleRecordError(
                    "Financial share options are not currently editable."
                )
            formset = ShareOptions(
                request.POST if request.method == "POST" else None,
                prefix="options",
                previous=campaign.active_configuration.values["share_options"],
            )
            response = (
                _preview(request, service, actor, state, campaign, formset, salt)
                if request.method == "POST"
                else _page(request, configuration, campaign, formset)
            )
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
