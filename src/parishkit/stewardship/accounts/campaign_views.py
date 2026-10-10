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
from parishkit.stewardship.campaigns.configuration import MINISTRY_LEADER_ROLES
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.leader_roles import effective_roles, role_choices
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
from .campaign_forms import CampaignSettingsForm, LeaderRolesForm, initial_fields
from .campaign_preview import describe_changes
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .runtime_models import SystemConfiguration
from .sessions import authenticated_admin

SALT = "stewardship-campaign-structure-preview-v1"
# The review's note when the change turns financial stewardship off.
REMOVES_SHARE_OPTIONS = _(
    "Disabling financial stewardship removes this draft's sharing options. "
    "Re-enabling it starts with the default options, not your customized "
    "labels. The previous configuration remains in retained history."
)
MULTIPLE_FIELDS = frozenset(
    {"ministry_duids", "fund_duids", "comparison_fund_duids", "ministry_leader_roles"}
)
# A live campaign's Ministry leader roles (#922) keep their own small form,
# which names itself with this hidden field.
LEADER_EDITOR = "leader_roles"
LEADER_FIELDS = frozenset({"editor", "ministry_leader_roles", "base_digest"})


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


def _leaders_editable(configuration, campaign, editable):
    """Whether the live leader-role form is shown: a locked current campaign
    that is not archived and asks about Ministries (#922). A draft edits the
    roles in its own form instead.
    """
    return (
        not editable
        and campaign.pk == configuration.current_campaign_id
        and campaign.state != "archived"
        and "ministry" in campaign.active_configuration.values["modules"]
    )


def _leader_form(configuration, campaign, data=None, *, digest=None):
    """The live leader-role form, ticked with the roles in effect now."""
    roles = effective_roles(campaign.active_configuration.values)
    form = LeaderRolesForm(
        data,
        initial={
            "ministry_leader_roles": roles,
            "base_digest": digest or configuration.active_configuration.digest,
        },
        roles=role_choices(roles),
    )
    # No shared text replaces its own help; this only moves the long help
    # into the field's tip, as on every Admin form.
    setup_help.apply(form, {})
    return form


def _page(
    request,
    configuration,
    campaign,
    form,
    *,
    editable,
    status=200,
    leader_form=None,
    **region,
):
    """Keep locked structural values visible without rendering mutation controls.

    ``region`` is ``review_region``'s ``review``, ``receipt`` or ``refusal``
    for the editor's in-place review region (#532). A live campaign that asks
    about Ministries shows its leader-role form (``leader_form``, #922) with
    that region under it.
    """
    leaders = _leaders_editable(configuration, campaign, editable)
    if leaders:
        if leader_form is None:
            leader_form = _leader_form(
                configuration, campaign, digest=form["base_digest"].value()
            )
        # The live form edits the roles; the read-only settings do not
        # repeat them.
        form.fields.pop("ministry_leader_roles", None)
    if editable or leaders:
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
            "leader_form": leader_form if leaders else None,
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
            **review_region("campaign_settings", form, **region),
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
    previous = _with_leader_roles(campaign.active_configuration.values)
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


def _with_leader_roles(values):
    """A campaign's values with its effective leader roles filled in (#922).

    A campaign without the key uses the default roles, so a review compares
    a change with the roles really in effect, never with "Not set".
    """
    if "ministry" not in values["modules"] or MINISTRY_LEADER_ROLES in values:
        return values
    return {**values, MINISTRY_LEADER_ROLES: effective_roles(values)}


def _leaders_preview(request, service, actor, state, campaign, settings, form):
    """Review a live campaign's leader-role change; nothing is requested yet.

    The change is one campaign update setting ``ministry_leader_roles``,
    which the installer and the SQL structural lock both admit while live.
    ``settings`` is the page's read-only settings form; ``form`` the bound
    leader-role form.
    """
    configuration, fingerprint = state[0], state[-1]

    def again(status):
        """This page again, with the leader form as the reader sent it."""
        return _page(
            request,
            configuration,
            campaign,
            settings,
            editable=False,
            status=status,
            leader_form=form,
        )

    if not form.is_valid():
        return again(400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    previous = _with_leader_roles(campaign.active_configuration.values)
    roles = form.cleaned_data["ministry_leader_roles"]
    if roles == previous[MINISTRY_LEADER_ROLES]:
        form.add_error(None, _("No settings have changed."))
        return again(400)
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {MINISTRY_LEADER_ROLES: roles},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    review = {
        "changes": describe_changes(previous, {MINISTRY_LEADER_ROLES: roles}),
        "notes": [],
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT,
            snapshot=fingerprint,
        ),
    }
    return _page(
        request,
        configuration,
        campaign,
        settings,
        editable=False,
        leader_form=form,
        review=review,
    )


def _settings_form(configuration, campaign, data=None, *, digest=None, source=None):
    """The campaign's settings form, drawn from its applied values.

    ``digest`` is the version the form is drawn at (the current one unless
    an in-place answer keeps the reader's); ``source`` the promoted source
    for the Ministry and fund names.
    """
    previous = campaign.active_configuration.values
    ministries, funds = _catalog(configuration, source, previous)
    initial = initial_fields(
        previous,
        digest=configuration.active_configuration.digest if digest is None else digest,
    )
    leader_roles = (
        effective_roles(previous) if "ministry" in previous["modules"] else []
    )
    # A draft without Ministry stewardship still offers the default roles,
    # ticked, for when it is turned on.
    shown = leader_roles or effective_roles({})
    initial["ministry_leader_roles"] = shown
    form = CampaignSettingsForm(
        data,
        initial=initial,
        previous=previous,
        ministries=ministries,
        funds=funds,
        leader_choices=role_choices(shown),
        leader_roles=shown,
    )
    # Same plain-language field help as the setup wizard (setup_help.py).
    setup_help.apply(form, setup_help.ADMIN_CAMPAIGN, replace=True)
    return form


@require_http_methods(["GET", "HEAD", "POST"])
def retired_new(request):
    """The retired New campaign address: go to the current campaign's settings.

    New campaign is removed (admin-portal spec, decision 11). Every method,
    including a form left open on the old page, is sent to Campaign settings
    without recording anything, so a creation preview posted here is never
    confirmed. With no current campaign the reader lands on Home, which
    explains that. Only an Administrator who may open Campaign settings is
    redirected, so the address never reveals the campaign's identifier to
    anyone else. A temporary redirect, because the target follows whichever
    campaign is current.
    """
    try:
        principal(request, runtime())
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
    except (ConfigError, DatabaseError, LimiterUnavailable, PermissionError) as error:
        return error_response(error)
    response = HttpResponseRedirect(
        reverse("admin:campaign_settings") if current else reverse("admin:index")
    )
    response["Cache-Control"] = "no-store"
    return response


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
        leaders = (
            request.method == "POST" and request.POST.get("editor") == LEADER_EDITOR
        )
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=LEADER_FIELDS
                if leaders
                else set(CampaignSettingsForm.base_fields),
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
                # In place, the form keeps the reader's values
                # (admin_editing.receipt_base); a full load uses the current
                # version, matching the current values it draws.
                digest = receipt_base(request, receipt)
            state = _state(service)
            configuration, campaigns, source, held = state[:4]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            form = _settings_form(
                configuration,
                campaign,
                request.POST if action == "preview" and not leaders else None,
                digest=digest,
                source=source,
            )
            if leaders and action == "preview":
                if not _leaders_editable(configuration, campaign, editable):
                    raise UserFacingStale(
                        _("These Ministry leader roles can't be changed here now."),
                        fix=_(
                            "A draft campaign changes them with its other "
                            "settings; an archived campaign or one that does "
                            "not ask about Ministries has none to change."
                        ),
                    )
                leader_form = _leader_form(configuration, campaign, request.POST)
                try:
                    response = _leaders_preview(
                        request, service, actor, state, campaign, form, leader_form
                    )
                except UserFacingStale as error:
                    response = _page(
                        request,
                        configuration,
                        campaign,
                        form,
                        editable=False,
                        status=409,
                        leader_form=leader_form,
                        refusal=error.refusal,
                    )
            elif refusal is not None and (
                editable or _leaders_editable(configuration, campaign, editable)
            ):
                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=editable,
                    status=409,
                    refusal=refusal,
                )
            elif request.method == "POST":
                if not editable:
                    raise UserFacingStale(
                        _("These campaign settings are locked."),
                        fix=_(
                            "They can be changed only for the current draft "
                            "campaign in Testing mode, before it has ever been "
                            "active, and while no background work is running."
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
                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=editable,
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
