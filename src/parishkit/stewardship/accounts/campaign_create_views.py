"""Create the campaign: the deployment's first campaign, after system setup (#142).

System setup creates no campaign. While the deployment has never had one, an
Administrator creates it here: the campaign's basics (name, modules, dates,
Ministries and funds) in the ordinary campaign form, then a review, then one
versioned configuration request that adds the draft campaign with the
built-in default text for every applicable page and email. This is not a
multi-campaign control: ``single_campaign.first_campaign_admitted`` refuses
it as soon as any campaign row exists (admin-portal spec, "Create the
campaign").

Like Parish and Campaign settings, the page reviews and confirms in place
(#532): the review, its refusals and the change's live status fill the
review region under the form (``settings-review.html``), and confirmation
answers with this page again, naming the request. Unlike them, it never
refreshes itself once the change is applied: by then the campaign exists, so
the form has nothing left to create.

The campaign's Families and Family codes are not created here: a refresh reads
the current campaign when it is requested, so it can only follow the
request's activation. The change's live status then offers "Load the
campaign's ParishSoft data" (``ministry_views.created_campaign_refresh``),
in this page's region and on Change status alike. Otherwise the new giving
window makes the next scheduled quick update run as a full refresh (see
``source.cursors.delta_dates``).
"""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.single_campaign import (
    first_campaign_admitted,
    first_campaign_refused,
)
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation, setup_help
from .admin_editing import (
    confirm,
    error_response,
    form_action,
    principal,
    receipt_base,
    requested_status,
    review_region,
    reviewed_base,
    sign_preview,
)
from .authentication import runtime
from .campaign_forms import CampaignForm
from .campaign_preview import describe_changes
from .campaign_views import MULTIPLE_FIELDS, _catalog, _state
from .content_forms import default_content
from .limiting import LimiterUnavailable
from .ministry_views import created_campaign_refresh
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

# A salt of its own, so a creation preview can never be confirmed on Campaign
# settings (whose confirmation refuses any added campaign) or the reverse.
SALT = "stewardship-campaign-create-preview-v1"
# The review's notes: what confirming does, and where the text comes from.
CREATES_TESTING_DRAFT = _(
    "This creates the campaign as a draft in Testing mode. No Family emails "
    "are sent by this action."
)
DEFAULT_TEXT = _("Pages and emails start with the default text.")


def _admitted(state):
    """Whether the first campaign may be created now, from the editing state.

    ``state`` is ``campaign_views._state``'s tuple, read under the caller's
    transaction; its fourth item is "held" (background campaign work or a
    Testing cleanup).
    """
    return not state[3] and first_campaign_admitted(state[0])


def _scope(service):
    """Confirmation rechecks admission and the same domain version under the lock."""
    state = _state(service)
    if not _admitted(state):
        raise first_campaign_refused()
    return state[0], state[-1]


def _page(request, configuration, form, *, status=200, **region):
    """The creation page: its form and the in-place review region (#532).

    ``region`` is ``review_region``'s ``review``, ``receipt`` or ``refusal``.
    The step indicator follows the region: Make changes, Review, then Apply
    once a change is shown.
    """
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    receipt = region.get("receipt")
    response = render(
        request,
        "stewardship/campaign-create.html",
        {
            "configuration": configuration,
            "form": form,
            # No in-place follow-up (page None): once applied, the campaign
            # exists and this page has nothing left to create. The status
            # offers the campaign's first refresh instead.
            **review_region(None, form, **region),
            "refresh_key": created_campaign_refresh(receipt) if receipt else None,
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, state, form):
    """Validate the whole new campaign and sign its review; record nothing yet."""
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return _page(request, configuration, form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    values = form.values()
    if values["timezone"] != configuration.active_configuration.parish.timezone:
        form.add_error(
            "timezone",
            _(
                "The campaign starts in the Parish timezone. "
                "Edit the draft afterward to change it."
            ),
        )
        return _page(request, configuration, form, status=400)
    target = str(uuid4())
    # The campaign starts with the default text for every page and email its
    # modules use, added in this same configuration change so it never exists
    # without content (#125). Each default passes the normal editor
    # validation, and build_candidate below validates the whole candidate.
    content, values["content_versions"] = default_content(target, values)
    patch = [
        {"operation": "add", "section": "campaigns", "id": target, "values": values},
        *({"operation": "add", "section": "content", **row} for row in content),
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except ConfigError:
        form.add_error(None, _("Check the campaign name and dates."))
        return _page(request, configuration, form, status=400)
    review = {
        "changes": describe_changes(
            {},
            values,
            ministries=form.fields["ministry_duids"].choices,
            funds=form.fields["fund_duids"].choices,
        ),
        "notes": [CREATES_TESTING_DRAFT, *([DEFAULT_TEXT] if content else [])],
        "apply_label": _("Create the campaign"),
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT,
            snapshot=fingerprint,
        ),
    }
    return _page(request, configuration, form, review=review)


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_create(request):
    """Read the creation form, review the new campaign in place, or confirm it.

    The page, its review and its confirmation each require admission
    (``first_campaign_admitted``, and no campaign work or credential hold);
    the confirmation rechecks it under the work lock that records the request
    (``_scope``). Two reads skip it, because they show a change already
    recorded rather than offering a new one: the page naming one of this
    Administrator's own changes (``?request=``, confirmation's in-place
    answer, by which time the campaign may exist), and a refused
    confirmation, redrawn in place with its explanation (status 409).
    """
    try:
        service = runtime()
        # Reading a change's status is passive, as Change status is: it never
        # renews idle time.
        actor = principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        action, refusal, digest = None, None, None
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=set(CampaignForm.base_fields),
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                try:
                    response = confirm(
                        request,
                        service,
                        actor,
                        salt=SALT,
                        current_scope=_scope,
                        first_campaign=True,
                        in_place=True,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    # Drawn below, the form kept at the version the change
                    # was reviewed at (admin_editing.reviewed_base).
                    refusal = error.refusal
                    digest = reviewed_base(request.POST, SALT)
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
            if receipt is not None:
                digest = receipt_base(request, receipt)
            state = _state(service)
            configuration, source = state[0], state[2]
            if receipt is None and refusal is None and not _admitted(state):
                # A Review is refused in place, keeping the reader's values;
                # a plain load of the page is refused whole.
                if action != "preview":
                    raise first_campaign_refused()
                refusal = first_campaign_refused().refusal
            ministries, funds = _catalog(configuration, source, {})
            form = CampaignForm(
                request.POST if action == "preview" else None,
                initial={
                    "timezone": configuration.active_configuration.parish.timezone,
                    "census": True,
                    "additional_information": True,
                    "base_digest": configuration.active_configuration.digest
                    if digest is None
                    else digest,
                },
                previous={},
                ministries=ministries,
                funds=funds,
            )
            # Same plain-language field help as Campaign settings.
            setup_help.apply(form, setup_help.ADMIN_CAMPAIGN, replace=True)
            if refusal is not None:
                response = _page(
                    request, configuration, form, status=409, refusal=refusal
                )
            elif action == "preview":
                try:
                    response = _preview(request, service, actor, state, form)
                except UserFacingStale as error:
                    response = _page(
                        request,
                        configuration,
                        form,
                        status=409,
                        refusal=error.refusal,
                    )
            else:
                response = _page(request, configuration, form, receipt=receipt)
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
