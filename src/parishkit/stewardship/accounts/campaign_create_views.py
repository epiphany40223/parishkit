"""Create the campaign: the deployment's first campaign, after system setup (#142).

System setup creates no campaign. While the deployment has never had one, an
Administrator creates it here: the campaign's basics (name, modules, dates,
Ministries and funds) in the ordinary campaign form, then a review, then one
versioned configuration request that adds the draft campaign with the
built-in default text for every applicable page and email. This is not a
multi-campaign control: ``single_campaign.first_campaign_admitted`` refuses
it as soon as any campaign row exists (admin-portal spec, "Create the
campaign").

The form's Create the campaign button posts its Review without a page load
and is answered with a confirmation in the shared review dialog (UX
conventions, "Review dialog", #750, #1356): one sentence saying what Apply
does, the number of pages and emails it adds with the default text (the one
fact the page above does not show), and the signed preview, which the
dialog's Apply posts back here. It lists no values again: every one of them
is in the filled-in form behind the dialog. A Review refused for the form's
values is the page again with status 400, so the errors stay on the form
and the dialog stays shut; any other refusal (no longer admitted, a stale
page, an out-of-date preview) is a plain refusal shown in the dialog.

Apply is answered with this page naming the request (``?request=``), whose
region holds the change's live status; the dialog follows it there and,
once the change is applied, redraws the page's region from that address,
because the page's own address could only be refused by then (the campaign
exists, so the form has nothing left to create).

The campaign's Families and Family codes are not created here: a refresh reads
the current campaign when it is requested, so it can only follow the
request's activation. The change's live status then offers "Load the
campaign's ParishSoft data" (``created_campaign_refresh``),
in this page's region and on Change status alike. Otherwise the new giving
window makes the next scheduled quick update run as a full refresh (see
``source.cursors.delta_dates``).
"""

from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
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
from parishkit.stewardship.web.refusals import stale_page

from . import admin_navigation, setup_help
from .admin_editing import (
    confirm,
    error_response,
    form_action,
    principal,
    receipt_status,
    requested_status,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .campaign_forms import CampaignForm
from .campaign_views import MULTIPLE_FIELDS, _catalog, _state
from .content_forms import default_content
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin
from .timezone_field import browser_default

# A salt of its own, so a creation preview can never be confirmed on Campaign
# settings (whose confirmation refuses any added campaign) or the reverse.
SALT = "stewardship-campaign-create-preview-v1"
# The page region that Apply's answer, its status and the redraw after it
# share (campaign-create.html).
REGION = "campaign-create-region"


def created_campaign_refresh(receipt):
    """A refresh key when this applied change created the current campaign.

    Create the campaign (#142) adds the deployment's first campaign; its
    Families and Family codes come from a full ParishSoft refresh, which can
    only be requested once the campaign is current (a refresh reads the
    current campaign when it is requested). The status page then offers the
    one-click refresh, posted to Refresh from ParishSoft's own action, until
    a refresh has added the campaign's Families. The page itself already
    required the Administrator capability.
    """
    from uuid import uuid4

    from .request_models import ConfigurationChangeRequest
    from .runtime_models import SystemConfiguration

    if receipt.state != "applied":
        return None
    patch = (
        ConfigurationChangeRequest.objects.filter(pk=receipt.request_id)
        .values_list("patch", flat=True)
        .first()
    ) or []
    # The stored patch is canonical JSON written by the request path, but
    # this read must never fail on an unexpected item shape.
    added = {
        item.get("id")
        for item in patch
        if type(item) is dict
        and item.get("section") == "campaigns"
        and item.get("operation") == "add"
    }
    current = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    if current is None or str(current) not in added:
        return None
    # Once a refresh has given the campaign its Families, there is nothing
    # left to load from here; scheduled refreshes keep it current.
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    if FamilyCampaign.objects.filter(campaign_id=current).exists():
        return None
    return uuid4()


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


def _page(request, configuration, form, *, status=200, receipt=None):
    """The creation page: its form, or the status of a confirmed creation.

    With ``receipt`` (Apply's answer, the dialog's polls of it, the redraw
    once it is applied, or a reload of that address) the page's region holds
    the change's live status instead of the form, which by then could only
    create a second campaign or be refused; once a campaign exists it points
    to Campaign settings, saying the campaign was created only if this change
    was applied. A Review refused for its values (400) draws its errors'
    summary, which the page script places above the heading.
    """
    admin_navigation.place(request, flow="change", step="apply" if receipt else "edit")
    response = render(
        request,
        "stewardship/campaign-create.html",
        {
            "configuration": configuration,
            "form": form,
            "created": receipt is not None
            and configuration.current_campaign_id is not None,
            # The status polls Change status; nothing follows up on this
            # page once it is applied.
            **receipt_status(receipt),
            "refresh_key": created_campaign_refresh(receipt) if receipt else None,
            "errors": summary_errors(form) if status == 400 else [],
            # Review and the dialog's Apply post here.
            "page_url": request.path,
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def creation_form(configuration, source, data=None):
    """The creation form over the current source's Ministries and funds.

    It is drawn at the applied configuration's version. The local seeder
    (``seed_web``) fills in the same form.
    """
    ministries, funds = _catalog(configuration, source, {})
    form = CampaignForm(
        data,
        initial={
            "timezone": configuration.active_configuration.parish.timezone,
            "census": True,
            "additional_information": True,
            "base_digest": configuration.active_configuration.digest,
        },
        previous={},
        ministries=ministries,
        funds=funds,
    )
    # Same plain-language field help as Campaign settings.
    setup_help.apply(form, setup_help.ADMIN_CAMPAIGN, replace=True)
    return any_zone(form)


def any_zone(form):
    """Start the form's zone on the browser's; any zone may be chosen (#1330).

    The menu starts on the browser's zone when it offers it, else on the
    form's initial (parish) zone, and its help says so. The browser
    component pages build their form with this too.
    """
    form.fields["timezone"].help_text = setup_help.CREATION_TIMEZONE
    browser_default(form)
    return form


def creation_review(service, actor, state, form):
    """Validate the whole new campaign and sign its review; record nothing yet.

    Returns the confirmation's ``review`` (the signed ``preview`` and the
    number of default pages and emails it adds, ``content``), or None with
    the reason on the form's errors. The page shows either; the local seeder
    (``seed_web``) confirms the signed ``preview`` the way the dialog's Apply
    does.
    """
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return None
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    # Any catalog zone is accepted (TimezoneField, campaign_values), not only
    # the parish zone: the campaign's dates and schedules are resolved in its
    # own zone (Administrator, 2026-10-15, #1330).
    values = form.values()
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
        form.add_error(
            None,
            ValidationError(
                _("Check the campaign name and dates."), code="candidate_refused"
            ),
        )
        return None
    return {
        # Worked out here, from the modules chosen: the page does not show it.
        "content": len(content),
        # Once applied, the dialog opens the next Campaign setup step
        # (#1404): Campaign settings is completed by the creation, so it is
        # Share options with Financial stewardship, else Pages and emails.
        "next_url": reverse(
            "admin:share_settings"
            if "financial" in values.get("modules", ())
            else "admin:content_catalog"
        ),
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT,
            snapshot=fingerprint,
        ),
    }


def _preview(request, service, actor, state, form):
    """The review dialog's confirmation, or the page with the form's errors."""
    review = creation_review(service, actor, state, form)
    if review is None:
        return _page(request, state[0], form, status=400)
    return render(
        request, "stewardship/campaign-create-review.html", {"review": review}
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_create(request):
    """Read the creation form, confirm the new campaign in the dialog, or apply it.

    The page, its Review and its Apply each require admission
    (``first_campaign_admitted``, and no campaign work or credential hold);
    Apply rechecks it under the work lock that records the request
    (``_scope``). A refused Review or Apply raises its refusal, which the
    dialog shows; a plain load of the page is refused whole. One read skips
    admission, because it shows a change already recorded rather than
    offering a new one: the page naming one of this Administrator's own
    changes (``?request=``, Apply's answer, by which time the campaign may
    exist).
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
        action = None
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=set(CampaignForm.base_fields),
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                response = confirm(
                    request,
                    service,
                    actor,
                    salt=SALT,
                    current_scope=_scope,
                    first_campaign=True,
                    region=REGION,
                )
                response["Cache-Control"] = "no-store"
                return response
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
            configuration, source = state[0], state[2]
            if receipt is None and not _admitted(state):
                raise first_campaign_refused()
            form = creation_form(
                configuration,
                source,
                request.POST if action == "preview" else None,
            )
            if action == "preview":
                response = _preview(request, service, actor, state, form)
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
