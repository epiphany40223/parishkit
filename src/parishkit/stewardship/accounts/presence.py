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
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters
from parishkit.stewardship.web.tables import Sorting, clamp_window, window_table

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
# Every column sorts on the server over the visible set, which the
# family_session_presence index (presence_at, id) bounds to sessions seen in
# the last 90 seconds before any sort runs; only Last heartbeat is itself
# that index's order. Family is the name from the current source snapshot,
# not a session column, so a Family sort names every visible session and
# sorts them in memory (see _by_name); the others order in SQL. The session
# id is the unique tiebreak.
PRESENCE_SORTING = Sorting.by_column(
    {
        "name": (),
        "duid": ("family__family_duid",),
        "started": ("authenticated_at",),
        "activity": ("last_activity_at",),
        "heartbeat": ("presence_at",),
        "section": ("presence_section",),
    },
    default="-heartbeat",
    descending_first={"started", "activity", "heartbeat"},
    tiebreak=("id",),
)


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
    """Name each listed Family from one current snapshot in the configured tenant."""
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
    # "Squyres, Tracy and Jeff", as on the Family codes directory; a Family
    # with no name fields is "Family", as on the send page and directory.
    return snapshot_family_names(
        current.snapshot_id, [row.family.family_duid for row in rows], "Family"
    )


def _by_name(configuration, query, window, token):
    """One page of visible sessions sorted by Family name, with their names.

    Names come from the source snapshot, so every visible session is named
    and sorted here; the 90-second presence window keeps that set small.
    Session id order first makes equal names deterministic.
    """
    rows = list(query.select_related("family").order_by("id"))
    names = _names(configuration, rows) if rows else {}
    ordered = sorted(
        rows,
        key=lambda row: (names.get(row.family.family_duid) or "").casefold(),
        reverse=PRESENCE_SORTING.tokens[token][1],
    )
    start = (window.page - 1) * window.size
    page = ordered[start : start + window.size]
    return page, len(ordered) > start + window.size, names


@require_safe
def active_families(request):
    """Admin-only passive read with one coherent eligibility/source observation.

    Session filtering and name lookup must see the same population, mode/epoch
    and promoted source. A plain READ COMMITTED transaction does not give that
    multi-query invariant; one REPEATABLE READ snapshot does, because every
    writer of those rows (population reconciliation, mode/epoch selection,
    source promotion) commits atomically. The snapshot takes no work-order
    lock, so a promotion or installer never delays this page (#147). Keep the
    page bounded; no provider IO or unbounded roster is loaded here.
    """
    try:
        service = admin_runtime()
        actor = principal(request, service, passive=True)
        selected = filters(request.GET, allowed={"page", "size", "sort", "format"})
        sort = PRESENCE_SORTING.parse(selected)
        if selected.get("format", "html") not in {"html", "json", "count"}:
            raise ValueError("Invalid presence format.")
        count_only = selected.get("format") == "count"
        window = PageWindow(
            expected_version(selected.get("page", "1")),
            expected_version(selected.get("size", "50")),
        )
        with read_transaction():
            configuration = editable_configuration(service)
            instant = database_now()
            query = visible_sessions(configuration, instant)
            count = query.count()
            # A page past the end of the (small, exact) count shows the last.
            window = clamp_window(window, (count, False))
            if count_only:
                rows, has_next, names = [], False, {}
            elif PRESENCE_SORTING.tokens[sort][0] == "name":
                rows, has_next, names = _by_name(configuration, query, window, sort)
            else:
                rows, has_next = window.rows(
                    PRESENCE_SORTING.order(query.select_related("family"), sort)
                )
                names = _names(configuration, rows) if rows else {}
            data = {
                "count": count,
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
        if count_only:
            data = {"count": data["count"], "as_of": instant}
        response = (
            JsonResponse(data)
            if selected.get("format") in {"json", "count"}
            else render(
                request,
                "stewardship/presence.html",
                {
                    "presence": data,
                    "table": window_table(
                        window,
                        data["sessions"],
                        has_next,
                        # The visible set is small and already counted
                        # exactly above, so no bounded count is needed.
                        total=(data["count"], False),
                        carry=[("format", selected.get("format", ""))],
                        sorting=PRESENCE_SORTING,
                        sort=sort,
                    ),
                },
            )
        )
        # Recheck access after the snapshot ends, so a read-only observation
        # cannot hide a revocation committed while it ran. The audit row is an
        # append that needs no work lock; it commits with the recheck.
        with transaction.atomic():
            if not allows(
                authenticated_admin(request, store=service.store, read_only=True),
                Capability.CONFIGURE,
            ):
                raise PermissionError("Presence access was revoked.")
            # Passive header polling discloses no identity list. Audit actual
            # roster views, not every 30-second count observation in every tab.
            if not count_only:
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
