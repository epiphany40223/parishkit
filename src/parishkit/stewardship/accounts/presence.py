"""Presence-only Family heartbeats and bounded Admin session indicators.

These timestamps are not activity, credential validity or submission evidence.
The common authentication owner checks every Family heartbeat; SQL also prevents
using a presence update to extend the authenticated session's deadlines.
"""

from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_models import (
    PRESENCE_SECTIONS,
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilySession,
)
from parishkit.stewardship.campaigns.lifecycle import portal_admitted
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotFamily
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime as admin_runtime
from .cryptography import CryptographicError
from .family_authentication import (
    authenticated_family,
    denied,
)
from .family_authentication import (
    runtime as family_runtime,
)
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import FAMILY_IDLE, authenticated_admin, database_now

SECTIONS = frozenset(PRESENCE_SECTIONS)
INTERVAL = timedelta(seconds=30)
VISIBLE = timedelta(seconds=90)


@require_POST
def heartbeat(request):
    """Accept one closed section name, never answers, timestamps or credentials."""
    try:
        data = filters(request.POST, allowed={"section", "csrfmiddlewaretoken"})
        section = data.get("section")
        if section not in SECTIONS or request.FILES or request.GET:
            raise ValueError("Invalid presence fields.")
        service = family_runtime()
        with transaction.atomic():
            actor = authenticated_family(request, service=service, activity=False)
            if actor is None:
                return denied()
            row = request.family_session
            now = database_now()
            if row.presence_at is None or now >= row.presence_at + INTERVAL:
                FamilySession.objects.filter(pk=row.pk, version=row.version).update(
                    presence_at=now, presence_section=section, version=F("version") + 1
                )
            # The second admission catches an eligibility/epoch transition while
            # preparing the response, without extending idle or absolute expiry.
            if authenticated_family(request, service=service, read_only=True) is None:
                return denied()
        response = JsonResponse({"recorded": True})
        response["Cache-Control"] = "no-store"
        return response
    except ValueError:
        return denied(status=400)
    except (ConfigError, CryptographicError, DatabaseError, LimiterUnavailable):
        return denied(status=503, retry=5)


def visible_sessions(configuration, instant):
    """Filter current campaign/mode/epoch/eligibility and both session deadlines."""
    query = FamilySession.objects.none()
    campaign = configuration.current_campaign
    if campaign is None or not portal_admitted(
        campaign_facts(campaign, configuration), _now()
    ):
        return query
    scope = (
        CampaignCredentialState.objects.filter(
            campaign=campaign, go_live_gate=False, population_dirty=False
        )
        .select_related("rehearsal_epoch")
        .first()
    )
    deployment = DeploymentCredentialState.objects.first()
    if scope is None or deployment is None:
        return query
    query = FamilySession.objects.filter(
        family__campaign=campaign,
        family__portal_eligible=True,
        mode=configuration.mode,
        revoked_at__isnull=True,
        expires_at__gt=instant,
        last_activity_at__gt=instant - FAMILY_IDLE,
        presence_at__gt=instant - VISIBLE,
        presence_at__lte=instant,
        credential_epoch=deployment.family_link_epoch,
    )
    if configuration.mode == "testing":
        if scope.rehearsal_epoch_id is None or scope.rehearsal_epoch.state != "active":
            return query.none()
        query = query.filter(rehearsal_epoch_id=scope.rehearsal_epoch_id)
    return query


def _names(configuration, rows):
    """Resolve names only, from one current snapshot in the configured tenant."""
    current = SourceCurrent.objects.first()
    organization = next(
        (
            row["values"]["settings"]["organization_id"]
            for row in configuration.active_configuration.canonical_document[
                "sections"
            ].get("integrations", [])
            if row["values"]["kind"] == "parishsoft"
        ),
        None,
    )
    if (
        current is None
        or current.snapshot_id is None
        or str(current.organization_id) != organization
    ):
        return {}
    duids = [str(row.family.family_duid) for row in rows]
    result = {}
    for record in SnapshotFamily.objects.filter(
        snapshot_id=current.snapshot_id, source_key__in=duids
    ).select_related("payload"):
        value = record.payload.payload
        result[int(record.source_key)] = (
            value.get("mailingName")
            or " ".join(
                str(value.get(name) or "") for name in ("firstName", "lastName")
            ).strip()
        )
    return result


@require_safe
def active_families(request):
    """Admin-only passive read with one coherent eligibility/source observation.

    The work lock prevents population reconciliation, mode/epoch selection and
    source promotion between session filtering and name lookup. A plain READ
    COMMITTED transaction does not provide that multi-query invariant. Keep the
    page bounded; no provider IO or unbounded roster is loaded under the lock.
    Count-only polling does not join names and needs no global work lock.
    """
    try:
        service = admin_runtime()
        actor = principal(request, service, passive=True)
        selected = filters(request.GET, allowed={"page", "format"})
        if selected.get("format", "html") not in {"html", "json", "count"}:
            raise ValueError("Invalid presence format.")
        window = PageWindow(expected_version(selected.get("page", "1")), 50)
        boundary = (
            transaction.atomic
            if selected.get("format") == "count"
            else work_transaction
        )
        with boundary():
            configuration = editable_configuration(service)
            instant = database_now()
            query = visible_sessions(configuration, instant)
            rows, has_next = (
                ([], False)
                if selected.get("format") == "count"
                else window.rows(
                    query.select_related("family").order_by("-presence_at", "id")
                )
            )
            names = _names(configuration, rows) if rows else {}
            data = {
                "count": query.count(),
                "page": window.page,
                "has_next": has_next,
                "as_of": instant,
                "sessions": [
                    {
                        "name": names.get(row.family.family_duid)
                        or str(_("Name unavailable")),
                        "duid": row.family.family_duid,
                        "started_at": row.authenticated_at,
                        "last_activity_at": row.last_activity_at,
                        "presence_at": row.presence_at,
                        "section": row.presence_section,
                    }
                    for row in rows
                ],
            }
            if selected.get("format") == "count":
                data = {"count": data["count"], "as_of": instant}
            if not allows(
                authenticated_admin(request, store=service.store, read_only=True),
                Capability.CONFIGURE,
            ):
                raise PermissionError("Presence access was revoked.")
            response = (
                JsonResponse(data)
                if selected.get("format") in {"json", "count"}
                else render(
                    request,
                    "stewardship/presence.html",
                    {"presence": data, "next_page": window.page + 1},
                )
            )
            # Passive header polling discloses no identity list. Audit actual
            # roster views, not every 30-second count observation in every tab.
            if selected.get("format") != "count":
                record_action(
                    Action.PRESENCE_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    context={"outcome": Outcome.SUCCEEDED, "count": len(rows)},
                )
            response["Cache-Control"] = "no-store"
            return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
    ) as error:
        return error_response(error)
