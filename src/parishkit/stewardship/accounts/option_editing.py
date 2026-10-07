"""The ordered-option editors (Share options, Member talents), reviewed in place.

Both editors edit one ordered list of labelled options on the current draft
campaign with the same formset (``share_forms.ShareOptions``) and the same
rules: an unlocked Testing draft only, with the module the list belongs to.
They differ only in the values key, the module, the page and its words, which
``OptionEditor`` names. Review, apply and the change's status happen on the
page itself (#532, #750): the page answers its own POSTs, with the review,
a refusal or the change's status in its review region.

The form is an in-place region of its own (no script is bound to it), so an
answer redraws it with the values the Administrator sent, its field errors
beside each field, and, once a change has been applied, the applied list and
a fresh blank row. A refusal never pairs older values with a newer version
(which would let the next Review quietly undo a change made elsewhere): a
stale Review keeps the values and the version that were posted, so the next
Review is refused until the page is reloaded; a refused Apply redraws the
current list at the current version, so what is shown is what is saved.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation
from .admin_editing import (
    confirm,
    error_response,
    principal,
    requested_status,
    review_region,
    sign_preview,
)
from .authentication import runtime
from .campaign_views import _scope, _state, _target
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin
from .share_forms import ShareOptions, share_action


@dataclass(frozen=True)
class OptionEditor:
    """What distinguishes one ordered-option editor from the other."""

    key: str  # the campaign values key: "share_options", "talent_options"
    module: str  # the module the list belongs to
    page: str  # the page's URL name
    template: str  # the page's template
    salt: str  # the signed preview's salt, before ":<campaign id>"
    unchanged: str  # the refusal when nothing changed
    unavailable: str  # the refusal when the list cannot be edited now
    current: Callable  # campaign -> the list it shows today


def _page(request, editor, configuration, campaign, formset, **page):
    """Render the editor and its review region (``review_region``'s values).

    ``status`` (default 200) and ``digest`` (default the applied version)
    may be given; a stale refusal keeps the version the page was loaded at,
    so the Administrator reloads before changing anything.
    """
    status = page.pop("status", 200)
    digest = page.pop("digest", configuration.active_configuration.digest)
    step = "review" if page.get("review") else "edit"
    if page.get("receipt"):
        step = "apply"
    # Edit, review, apply (#196), all on this page.
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        editor.template,
        {
            "configuration": configuration,
            "campaign": campaign,
            "formset": formset,
            "base_digest": digest,
            **review_region(editor.page, formset, **page),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _preview(request, editor, service, actor, state, campaign, formset, salt):
    """Sign the reviewed ordered options without directly changing configuration."""
    configuration, fingerprint = state[0], state[-1]
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise stale_page()
    if not formset.is_valid():
        return _page(request, editor, configuration, campaign, formset, status=400)
    options = formset.values()
    before = editor.current(campaign)
    if options == before:
        # A clear, non-mutating refusal; no empty configuration request.
        return _page(
            request,
            editor,
            configuration,
            campaign,
            formset,
            status=400,
            message=editor.unchanged,
        )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {editor.key: options},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    review = {
        "template": "stewardship/option-review.html",
        "before": before,
        "after": options,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
        ),
    }
    return _page(request, editor, configuration, campaign, formset, review=review)


def option_settings(request, campaign_id, editor):
    """Current Admins may review and request changes only for an unlocked draft.

    A stale page or an out-of-date preview is refused in place: the page
    again (status 409) with the explanation in its review region.
    """
    try:
        service = runtime()
        # Reading a change's status (the page's own quiet refresh once it is
        # applied) is passive, as Change status is: it never renews idle time.
        actor = principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        salt = f"{editor.salt}:{campaign_id}"
        action, refusal = None, None
        if request.method == "POST":
            action = share_action(request.POST)
            if action == "confirm":
                try:
                    response = confirm(
                        request,
                        service,
                        actor,
                        salt=salt,
                        current_scope=_scope,
                        in_place=True,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    # Drawn below with the current list and its version
                    # together (see the module docstring).
                    refusal = error.refusal
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            receipt = (
                requested_status(request, service, actor, request.GET)
                if request.method != "POST"
                else None
            )
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            if (
                not editable
                or editor.module not in campaign.active_configuration.values["modules"]
            ):
                raise StaleRecordError(editor.unavailable)
            formset = ShareOptions(
                request.POST if action == "preview" else None,
                prefix="options",
                previous=editor.current(campaign),
            )
            if refusal is not None:
                response = _page(
                    request,
                    editor,
                    configuration,
                    campaign,
                    formset,
                    status=409,
                    refusal=refusal,
                )
            elif action == "preview":
                try:
                    response = _preview(
                        request, editor, service, actor, state, campaign, formset, salt
                    )
                except UserFacingStale as error:
                    response = _page(
                        request,
                        editor,
                        configuration,
                        campaign,
                        formset,
                        status=409,
                        refusal=error.refusal,
                        digest=request.POST.get("base_digest", ""),
                    )
            else:
                response = _page(
                    request, editor, configuration, campaign, formset, receipt=receipt
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
