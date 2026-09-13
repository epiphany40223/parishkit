"""Admin draft creation and structural editing through exact YAML requests."""

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
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import CampaignState, SystemMode
from parishkit.stewardship.campaigns.lifecycle import (
    draft_creation_admitted,
    structural_edit_admitted,
)
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .campaign_forms import CampaignForm, initial_fields
from .campaign_preview import describe_changes
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-campaign-structure-preview-v1"
MULTIPLE_FIELDS = frozenset({"ministry_duids", "fund_duids", "comparison_fund_duids"})


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
                name = payload["name"]
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
    """Creation and structural mutation use the canonical current-campaign guards."""
    if campaign_id is None:
        if held or not draft_creation_admitted(
            mode=SystemMode(configuration.mode),
            current_id=configuration.current_campaign_id,
            states=[CampaignState(row.state) for row in campaigns],
            # BG-11/ADM-10 own the future durable purge-request fact. The
            # existing work/credential hold above is a separate prerequisite.
            nonterminal_purge=False,
        ):
            raise StaleRecordError("A new campaign is not currently admitted.")
        return None, True
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


def _page(request, configuration, campaign, form, *, editable, status=200):
    """Keep locked structural values visible without rendering mutation controls."""
    response = render(
        request,
        "stewardship/campaign-settings.html",
        {
            "configuration": configuration,
            "campaign": campaign,
            "form": form,
            "editable": editable,
            "clone_sources": list(
                Campaign.objects.filter(state="archived")
                .select_related("active_configuration")
                .order_by("active_configuration__name", "id")
            )
            if campaign is None
            else [],
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, state, campaign, form):
    """Show complete schema validation errors before enqueueing any durable intent."""
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return _page(request, configuration, campaign, form, editable=True, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the campaign before editing it.")
    values = form.values()
    if (
        campaign is None
        and values["timezone"] != configuration.active_configuration.parish.timezone
    ):
        form.add_error(
            "timezone",
            _(
                "A new campaign starts in the Parish timezone. "
                "Edit the draft afterward to change it."
            ),
        )
        return _page(request, configuration, campaign, form, editable=True, status=400)
    previous = campaign.active_configuration.values if campaign else {}
    changed = {
        key: value for key, value in values.items() if previous.get(key) != value
    }
    if not changed:
        form.add_error(None, _("No settings have changed."))
        return _page(request, configuration, campaign, form, editable=True, status=400)
    window_fields = {"start_date", "end_date", "timezone"}
    if (
        campaign
        and window_fields.intersection(changed)
        and any(
            row["values"]["campaign_id"] == str(campaign.pk)
            for row in configuration.active_configuration.canonical_document[
                "sections"
            ].get("schedules", [])
        )
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
            reverse("admin:schedule_settings", args=[campaign.pk])
            + "?"
            + urlencode({name: values[name] for name in sorted(window_fields)})
        )
    patch = [
        {
            "operation": "update" if campaign else "add",
            "section": "campaigns",
            "id": str(campaign.pk if campaign else uuid4()),
            "values": changed if campaign else values,
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
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
    return render(
        request,
        "stewardship/campaign-preview.html",
        {
            "creating": campaign is None,
            "removes_share_options": bool(previous.get("share_options"))
            and "financial" not in values["modules"],
            "changes": describe_changes(
                previous,
                values,
                ministries=form.fields["ministry_duids"].choices,
                funds=form.fields["fund_duids"].choices,
            ),
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT,
                snapshot=fingerprint,
            ),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_settings(request, campaign_id=None):
    """Read, preview and confirm a draft without direct runtime/configuration writes."""
    try:
        service = runtime()
        actor = principal(request, service)
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=set(CampaignForm.base_fields),
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                response = confirm(
                    request, service, actor, salt=SALT, current_scope=_scope
                )
                response["Cache-Control"] = "no-store"
                return response
        else:
            filters(request.GET, allowed=set())
        with work_transaction():
            state = _state(service)
            configuration, campaigns, source, held, _ = state
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            previous = campaign.active_configuration.values if campaign else {}
            ministries, funds = _catalog(configuration, source, previous)
            initial = (
                initial_fields(
                    previous, digest=configuration.active_configuration.digest
                )
                if campaign
                else {
                    "timezone": configuration.active_configuration.parish.timezone,
                    "census": True,
                    "additional_information": True,
                    "base_digest": configuration.active_configuration.digest,
                }
            )
            form = CampaignForm(
                request.POST if request.method == "POST" else None,
                initial=initial,
                previous=previous,
                ministries=ministries,
                funds=funds,
            )
            if request.method == "POST":
                if not editable:
                    raise StaleRecordError("Campaign structural settings are locked.")
                response = _preview(request, service, actor, state, campaign, form)
            else:
                response = _page(
                    request, configuration, campaign, form, editable=editable
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
