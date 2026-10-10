"""Admin structural editing of the current campaign through exact YAML requests.

Creating another campaign is retired until the single-campaign change (#145):
the one campaign is created in the setup wizard (admin-portal spec, decision
11), the old New campaign address only redirects here, and ``_target``
refuses a new draft for every caller.
"""

import hashlib
import json
from urllib.parse import urlencode
from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted
from parishkit.stewardship.campaigns.live_ministries import live_ministries_editable
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.campaigns.single_campaign import creation_refused
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.source.catalog_names import (
    fund_display_name,
    ministry_display_name,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation, setup_help
from .admin_editing import (
    confirm,
    editable_configuration,
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
from .campaign_family_test import chosen_family_test_url
from .campaign_forms import CampaignForm, initial_fields
from .campaign_preview import describe_changes
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-campaign-structure-preview-v1"
# The review's note when the change turns financial stewardship off.
REMOVES_SHARE_OPTIONS = _(
    "Disabling financial stewardship removes this draft's sharing options. "
    "Re-enabling it starts with the default options, not your customized "
    "labels. The previous configuration remains in retained history."
)
MULTIPLE_FIELDS = frozenset({"ministry_duids", "fund_duids", "comparison_fund_duids"})
# What a live campaign's end-date Review posts (#912).
END_FIELDS = frozenset({"action", "csrfmiddlewaretoken", "end_date", "base_digest"})


def _state(service):
    """Pin current source, campaign lifecycle and all work gates beside applied YAML.

    A later lifecycle/source change invalidates the preview even when YAML has
    not changed. The caller owns the common work lock so the read is coherent
    with source promotion, lifecycle commands, and configuration installation.
    """
    configuration = editable_configuration(service)
    campaigns = list(
        Campaign.objects.select_related("active_configuration").order_by("id")
    )
    source = SourceCurrent.objects.first()
    gates = list(
        CampaignWorkGate.objects.filter(state__in=("preparing", "running"))
        .order_by("id")
        .values_list("id", "version")
    )
    cleanup = CampaignCredentialState.objects.filter(go_live_gate=True).exists()
    scope = {
        "runtime": configuration.version,
        "campaigns": [(str(row.pk), row.version) for row in campaigns],
        "source": str(source.snapshot_id) if source else None,
        "gates": [(str(key), version) for key, version in gates],
        "cleanup": cleanup,
    }
    fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return configuration, campaigns, source, bool(gates) or cleanup, fingerprint


def _scope(service):
    """Confirmation rechecks the same domain version under intake's owning lock."""
    configuration, _, _, _, fingerprint = _state(service)
    return configuration, fingerprint


def _catalog(configuration, source, previous):
    """Expose Ministry/fund names only from the configured tenant's current corpus."""
    from .ministry_activity import active_ministries

    selected = set(previous.get("ministry_duids", []))
    financial = previous.get("financial") or {}
    retained_funds = set(financial.get("fund_duids", [])) | set(
        financial.get("comparison_fund_duids", [])
    )

    def missing_choices(identifiers, kind):
        """Retain saved IDs without borrowing names from an unavailable tenant."""
        label = (
            _("Ministry %(duid)s (unavailable; retained selection)")
            if kind == "ministry"
            else _("Fund %(duid)s (unavailable; retained selection)")
        )
        return [(str(duid), label % {"duid": duid}) for duid in sorted(identifiers)]

    integrations = configuration.active_configuration.canonical_document[
        "sections"
    ].get("integrations", [])
    organization = next(
        (
            row["values"]["settings"]["organization_id"]
            for row in integrations
            if row["values"]["kind"] == "parishsoft"
        ),
        None,
    )
    if (
        source is None
        or source.snapshot_id is None
        or str(source.organization_id) != organization
    ):
        return missing_choices(selected, "ministry"), missing_choices(
            retained_funds, "fund"
        )
    catalogs = []
    for model in (SnapshotMinistry, SnapshotFund):
        choices = []
        rows = list(
            model.objects.filter(snapshot_id=source.snapshot_id).select_related(
                "payload"
            )
        )
        ministry_ids = (
            active_ministries(
                configuration.active_configuration.canonical_document,
                organization_id=source.organization_id,
                catalog_duids=frozenset(int(row.source_key) for row in rows),
            )
            if model is SnapshotMinistry
            else frozenset()
        )
        for row in rows:
            duid, payload = int(row.source_key), row.payload.payload
            active = payload.get("active", True) is not False
            if model is SnapshotMinistry:
                # Upstream Ministry activity is not reliable; the local override
                # alone owns activity for a Ministry present in this snapshot.
                active = duid in ministry_ids
            retained = selected if model is SnapshotMinistry else retained_funds
            if active or duid in retained:
                # A blank, null or odd ParishSoft name must not break
                # campaign settings (#341).
                name = (
                    ministry_display_name
                    if model is SnapshotMinistry
                    else fund_display_name
                )(duid, payload.get("name"))
                if not active:
                    name = _("%(name)s (inactive; retained selection)") % {"name": name}
                choices.append((str(duid), name))
        retained = selected if model is SnapshotMinistry else retained_funds
        choices.extend(
            missing_choices(
                retained - {int(row.source_key) for row in rows},
                "ministry" if model is SnapshotMinistry else "fund",
            )
        )
        choices.sort(key=lambda row: (row[1].casefold(), int(row[0])))
        catalogs.append(choices)
    return catalogs


def _target(configuration, campaigns, held, campaign_id):
    """Structural mutation uses the canonical current-campaign guards.

    ``campaign_id`` None asks for a new draft (Copy campaign passes it), which
    is refused until the single-campaign change (#145; navigation rule 10).
    """
    if campaign_id is None:
        raise creation_refused()
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    editable = (
        not held
        and campaign.pk == configuration.current_campaign_id
        and configuration.mode == "testing"
        and structural_edit_admitted(
            CampaignState(campaign.state),
            ever_active=campaign.ever_active,
            locked=campaign.structural_locked,
        )
    )
    return campaign, editable


def _page(
    request,
    configuration,
    campaign,
    form,
    *,
    editable,
    end_form=None,
    end_held=False,
    status=200,
    **region,
):
    """Keep locked structural values visible without rendering mutation controls.

    ``region`` is ``review_region``'s ``review``, ``receipt``, ``refusal`` or
    ``link`` for the editor's in-place review region (#532). ``end_form`` is
    a live campaign's end-date form (#912), whose review uses that region;
    ``end_held`` says background work alone holds that form back (#944).
    """
    if editable or end_form is not None:
        # Edit, review, apply (#196), all on this page; a locked campaign's
        # read-only page is not part of any flow.
        step = "review" if region.get("review") else "edit"
        if region.get("receipt"):
            step = "apply"
        admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/campaign-settings.html",
        {
            "configuration": configuration,
            "campaign": campaign,
            "form": form,
            "editable": editable,
            "end_form": end_form,
            "end_held": end_held,
            # A live campaign's Ministries keep their own editor (#342).
            "ministries_live": live_ministries_editable(
                campaign, configuration.current_campaign_id
            ),
            "family_test_url": chosen_family_test_url(configuration, campaign),
            "production_progress_available": configuration.current_campaign_id
            == campaign.pk
            and configuration.mode == "production"
            and ProductionConfirmation.objects.filter(
                request__campaign=campaign
            ).exists(),
            **review_region(
                "campaign_settings",
                end_form if end_form is not None else form,
                **region,
            ),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, state, campaign, form):
    """Show complete schema validation errors before enqueueing any durable intent."""
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return _page(request, configuration, campaign, form, editable=True, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    values = form.values()
    previous = campaign.active_configuration.values
    changed = {
        key: value for key, value in values.items() if previous.get(key) != value
    }
    if not changed:
        form.add_error(None, _("No settings have changed."))
        return _page(request, configuration, campaign, form, editable=True, status=400)
    window_fields = {"start_date", "end_date", "timezone"}
    if window_fields.intersection(changed) and any(
        row["values"]["campaign_id"] == str(campaign.pk)
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
    ):
        if set(changed) - window_fields:
            form.add_error(
                None,
                _(
                    "No settings have been saved. Reconcile dates and mail schedules "
                    "first using the schedule editor, then save other draft settings."
                ),
            )
            return _page(
                request, configuration, campaign, form, editable=True, status=400
            )
        return HttpResponseRedirect(
            reverse("admin:schedule_settings")
            + "?"
            + urlencode({name: values[name] for name in sorted(window_fields)})
        )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": changed,
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except ConfigError:
        form.add_error(
            None,
            _(
                "Check the campaign name and dates. Existing invitation and "
                "reminder times must remain within the campaign."
            ),
        )
        return _page(request, configuration, campaign, form, editable=True, status=400)
    removes_share_options = (
        bool(previous.get("share_options")) and "financial" not in values["modules"]
    )
    review = {
        "changes": describe_changes(
            previous,
            values,
            ministries=form.fields["ministry_duids"].choices,
            funds=form.fields["fund_duids"].choices,
        ),
        "notes": [REMOVES_SHARE_OPTIONS] if removes_share_options else [],
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT,
            snapshot=fingerprint,
        ),
    }
    return _page(request, configuration, campaign, form, editable=True, review=review)


def _end_form(request, action, state, campaign, editable, digest):
    """A live campaign's end-date form and the instant it opened at (#912).

    Returns ``(None, None)`` unless only the end date may change now
    (``schedule_reads.live_end_at``). The form is bound to a Review's post;
    otherwise it starts at the saved date and, as the settings form does,
    at the version a shown change was reviewed at (``digest``) or else the
    current one.
    """
    from datetime import date

    from .campaign_end_date import LiveEndDateForm
    from .schedule_reads import live_end_at

    live_at = None if editable else live_end_at(state, campaign)
    if live_at is None:
        return None, None
    configuration = state[0]
    return (
        LiveEndDateForm(
            request.POST if action == "preview" else None,
            initial={
                "end_date": date.fromisoformat(
                    campaign.active_configuration.values["end_date"]
                ),
                "base_digest": configuration.active_configuration.digest
                if digest is None
                else digest,
            },
        ),
        live_at,
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_settings(request, campaign_id):
    """Read, preview and confirm a draft without direct runtime/configuration writes.

    Review, apply and the change's status all happen on this page (#532). A
    stale page or an out-of-date preview is refused in place: the page again
    (status 409) with the explanation in its review region.
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
        action, refusal, digest = None, None, None
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=set(CampaignForm.base_fields),
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                from .campaign_end_date import signed_for
                from .schedule_changes import confirm_scope, preview_salt

                # A live campaign's end date (#912) is reviewed and signed
                # as the Dates and mail schedules change it is.
                live_end = signed_for(request.POST.get("preview", ""), campaign_id)
                try:
                    response = confirm(
                        request,
                        service,
                        actor,
                        salt=preview_salt(campaign_id) if live_end else SALT,
                        current_scope=(
                            (lambda service: confirm_scope(service, campaign_id))
                            if live_end
                            else _scope
                        ),
                        in_place=True,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    # Drawn below with the current settings' form, kept at
                    # the version the change was reviewed at, so a page
                    # changed elsewhere needs a reload before the next
                    # Review (admin_editing.reviewed_base).
                    refusal = error.refusal
                    digest = reviewed_base(
                        request.POST, preview_salt(campaign_id) if live_end else SALT
                    )
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
                # In place, the form keeps the reader's values
                # (admin_editing.receipt_base); a full load uses the current
                # version, matching the current values it draws.
                digest = receipt_base(request, receipt)
            state = _state(service)
            configuration, campaigns, source, held = state[:4]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            previous = campaign.active_configuration.values
            end_form, live_at = _end_form(
                request, action, state, campaign, editable, digest
            )
            ministries, funds = _catalog(configuration, source, previous)
            initial = initial_fields(
                previous,
                digest=configuration.active_configuration.digest
                if digest is None
                else digest,
            )
            form = CampaignForm(
                # A live campaign's page shows these settings read-only; its
                # Review posts only the end date (end_form).
                request.POST if action == "preview" and end_form is None else None,
                initial=initial,
                previous=previous,
                ministries=ministries,
                funds=funds,
            )
            # Same plain-language field help as the setup wizard (setup_help.py).
            setup_help.apply(form, setup_help.ADMIN_CAMPAIGN, replace=True)
            if refusal is not None and (editable or end_form is not None):
                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=editable,
                    end_form=end_form,
                    status=409,
                    refusal=refusal,
                )
            elif (
                request.method == "POST"
                and end_form is not None
                # Any other setting stays locked, refused below as before.
                and set(request.POST) <= END_FIELDS
            ):
                from .campaign_end_date import end_review

                try:
                    region = end_review(
                        service, actor, state, campaign, end_form, live_at
                    )
                    status = 200 if "review" in region else 400
                except UserFacingStale as error:
                    region, status = {"refusal": error.refusal}, 409
                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=False,
                    end_form=end_form,
                    status=status,
                    **region,
                )
            elif request.method == "POST":
                if not editable:
                    raise UserFacingStale(
                        _("These campaign settings are locked."),
                        fix=_(
                            "They can be changed only for the current draft "
                            "campaign in Testing mode, before it has ever been "
                            "active, and while no background work is running. "
                            "A live campaign's end date alone can still change "
                            "here, while no background work is running."
                        ),
                    )
                try:
                    response = _preview(request, service, actor, state, campaign, form)
                except UserFacingStale as error:
                    response = _page(
                        request,
                        configuration,
                        campaign,
                        form,
                        editable=True,
                        status=409,
                        refusal=error.refusal,
                    )
            else:
                from .schedule_reads import live_end_held

                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=editable,
                    end_form=end_form,
                    end_held=(
                        end_form is None
                        and not editable
                        and live_end_held(state, campaign)
                    ),
                    receipt=receipt,
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
