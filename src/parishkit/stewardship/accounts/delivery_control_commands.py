"""Current-Admin delivery controls, without transport keys or provider effects."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import connection

from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand, Campaign
from parishkit.stewardship.campaigns.runtime import _now, campaign_transaction
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .content_models import ContentVersion
from .delivery_forecast import next_due
from .sessions import require_fresh

SALT = "stewardship-delivery-control-v1"
RESOLVABLE_TYPES = frozenset({"receipt", "daily_digest", "weekly_digest"})


def _current(request, service, campaign_id, *, passive=False):
    """Recheck current scope/authority before private reads, including replay."""
    actor = principal(request, service, passive=passive)
    runtime = editable_configuration(service)
    if runtime.current_campaign_id != campaign_id:
        raise PermissionError("Delivery controls belong to the current campaign.")
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    return actor, runtime, campaign


def inventory(campaign_id):
    """Read count-only metadata; the SQL command rechecks the exact same snapshot."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT inventory FROM stewardship_delivery_control_inventory "
            "WHERE campaign_id=%s",
            [campaign_id],
        )
        row = cursor.fetchone()
    if row is None:
        raise PermissionError("Delivery controls are unavailable.")
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def _available(campaign, runtime):
    """Pause is independent of lifecycle and never changes global routing."""
    return runtime.mode == "production" and campaign.state in {
        "scheduled",
        "active",
        "closed",
    }


def health(campaign_id):
    """Expose only current proof metadata, never provider responses or recipients."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT health FROM stewardship_delivery_control_health "
            "WHERE campaign_id=%s",
            [campaign_id],
        )
        row = cursor.fetchone()
    if row is None:
        return {"ready": False, "proof": None, "checked_at": None, "expires_at": None}
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def _prestart_resume(campaign):
    """Distinguish an empty pre-start plan from ordinary overdue recovery."""
    return (
        campaign.delivery_paused
        and campaign.state == "scheduled"
        and _now() < campaign.active_configuration.starts_at
    )


def _family_resume(campaign):
    """Ordinary overdue recovery never substitutes for post-close resolution."""
    return (
        campaign.delivery_paused
        and campaign.state in {"scheduled", "active"}
        and campaign.active_configuration.starts_at
        <= _now()
        < campaign.active_configuration.ends_at
        and not ActivationCatchUpDemand.objects.filter(
            campaign=campaign, completed_at__isnull=True
        ).exists()
    )


def _resume_selection(campaign):
    """Bind the same complete count-only plan which the SQL effect will recheck."""
    proof = health(campaign.pk)
    if _prestart_resume(campaign):
        return {"plan": "before_start", "health": proof}
    return {
        "plan": "family",
        "health": proof,
        "family": family_recovery_impact(campaign.pk),
        "digests": digest_recovery_impact(campaign.pk),
    }


def _action_available(campaign, action):
    """Keep post-close resolution separate from ordinary live resume."""
    if action == "pause":
        return not campaign.delivery_paused
    if action == "resume":
        return _prestart_resume(campaign) or _family_resume(campaign)
    return (
        action == "resolve" and campaign.delivery_paused and campaign.state == "closed"
    )


def _clears_pause(current, types):
    """Selected held rows are released/cancelled; uncertainty cannot be cleared.

    Stranded Family rows (held on the closed campaign with no task left) are
    cancelled by every closed resolution, so they never block the clear.
    """
    selected = sum(current["types"].get(kind, {}).get("held", 0) for kind in types)
    return (
        current["held"] == selected + current["stranded"]
        and not current["submitting"]
        and not current["unknown"]
    )


def _closed_selection(campaign, current, *, decision, types):
    """Bind only selected held types; cancellation never requires provider health."""
    if (
        type(decision) is not str
        or decision not in {"release", "cancel", "clear"}
        or type(types) is not list
        or any(type(kind) is not str or kind not in RESOLVABLE_TYPES for kind in types)
        or types != sorted(set(types))
    ):
        raise ValueError("Select valid held-message types and a resolution.")
    count = sum(current["types"].get(kind, {}).get("held", 0) for kind in types)
    if (decision == "clear" and (types or not _clears_pause(current, types))) or (
        decision != "clear" and count == 0
    ):
        raise StaleRecordError("Select held messages, or clear a fully resolved pause.")
    proof = health(campaign.pk) if decision == "release" else None
    if proof is not None and not proof["ready"]:
        raise StaleRecordError(
            "Releasing held messages requires a current sender check."
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coverage,preparing "
            "FROM stewardship_delivery_closed_coverage_summary "
            "WHERE campaign_id=%s",
            [campaign.pk],
        )
        coverage, preparing = cursor.fetchone()
    if preparing or (
        decision == "cancel"
        and any(
            current["types"].get(kind, {}).get("submitting", 0)
            or current["types"].get(kind, {}).get("unknown", 0)
            for kind in types
        )
    ):
        raise StaleRecordError(
            "Wait for report preparation and reconcile selected uncertain mail first."
        )
    coverage = json.loads(coverage) if isinstance(coverage, str) else coverage
    return {
        "plan": "closed",
        "decision": decision,
        "types": types,
        "health": proof,
        "coverage": {kind: coverage[kind] for kind in types if kind in coverage},
    }


def family_recovery_impact(campaign_id):
    """Count complete due groups, including obligations not yet in the outbox."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT impact FROM stewardship_delivery_family_recovery_summary "
            "WHERE campaign_id=%s",
            [campaign_id],
        )
        row = cursor.fetchone()
    if row is None:
        raise PermissionError("Delivery recovery is unavailable.")
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def digest_recovery_impact(campaign_id):
    """Count complete original-day/aggregate groups without exposing reports."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT impact FROM stewardship_delivery_digest_recovery_summary "
            "WHERE campaign_id=%s",
            [campaign_id],
        )
        row = cursor.fetchone()
    if row is None:
        raise PermissionError("Digest recovery is unavailable.")
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def page(request, service, campaign_id):
    """Passive status does not refresh idle expiry or perform provider requests."""
    with work_transaction():
        _, runtime, campaign = _current(request, service, campaign_id, passive=True)
        try:
            require_fresh(request)
            fresh = True
        except PermissionError:
            fresh = False
        return {
            "campaign": campaign,
            "available": _available(campaign, runtime),
            "fresh": fresh,
            "resume_available": _prestart_resume(campaign) or _family_resume(campaign),
            "resolve_available": _action_available(campaign, "resolve"),
            "inventory": inventory(campaign_id),
            "next_due": next_due(campaign, _now()),
            "health": health(campaign_id),
            "family_recovery": (
                family_recovery_impact(campaign_id)
                if campaign.delivery_paused
                else None
            ),
            "digest_recovery": (
                digest_recovery_impact(campaign_id)
                if campaign.delivery_paused
                else None
            ),
            "test_template": ContentVersion.objects.filter(
                configuration_id=runtime.active_configuration_id,
                campaign_id=campaign_id,
                kind="email",
                slot="initial",
            )
            .order_by("record_id")
            .values_list("record_id", flat=True)
            .first(),
        }


def preview_pause(request, service, campaign_id, *, reason):
    """Sign the exact affected work, not permission to change a later inventory."""
    return _preview(request, service, campaign_id, reason=reason, action="pause")


def preview_resume(request, service, campaign_id, *, reason):
    """Select the complete supported recovery plan without changing any work."""
    return _preview(request, service, campaign_id, reason=reason, action="resume")


def preview_resolution(request, service, campaign_id, *, reason, decision, types):
    """Preview selected closed-campaign mail without changing Family access."""
    return _preview(
        request,
        service,
        campaign_id,
        reason=reason,
        action="resolve",
        decision=decision,
        types=types,
    )


def _preview(
    request, service, campaign_id, *, reason, action, decision=None, types=None
):
    """Bind current health when release is requested; pause never requires it."""
    if (
        type(reason) is not str
        or not reason.strip()
        or len(reason) > 1024
        or "\x00" in reason
    ):
        raise ValueError("Enter a delivery-control reason of at most 1,024 characters.")
    with work_transaction():
        actor, runtime, campaign = _current(request, service, campaign_id)
        if not _available(campaign, runtime) or not _action_available(campaign, action):
            raise StaleRecordError("This delivery control is not currently available.")
        selected = {}
        current = inventory(campaign_id)
        if action == "resume":
            proof = health(campaign_id)
            if not proof["ready"] or current["submitting"] or current["unknown"]:
                raise StaleRecordError(
                    "A current sender check and resolved delivery are required."
                )
            selected = _resume_selection(campaign)
            if selected.get("family", {}).get("blocked"):
                raise StaleRecordError("Resolve blocked Family groups before resuming.")
            if selected.get("digests", {}).get("blocked"):
                raise StaleRecordError(
                    "Wait for report preparation and resolve uncertain digests first."
                )
        elif action == "resolve":
            selected = _closed_selection(
                campaign, current, decision=decision, types=types
            )
        now = _now()
        binding = {
            "key": str(uuid4()),
            "actor": str(actor.identity),
            "campaign": str(campaign_id),
            "action": action,
            "campaign_version": campaign.version,
            "runtime_version": runtime.version,
            "observed": now.isoformat(),
            "expires": (now + timedelta(minutes=5)).isoformat(),
            "inventory": current,
            "selection": selected,
            "reason": reason.strip(),
        }
        return binding, signing.dumps(binding, salt=SALT)


def _binding(token, *, max_age=None):
    """Reject open-ended command envelopes even when signed by this service."""
    if type(token) is not str or len(token) > 16384:
        raise ValueError("Invalid delivery-control preview.")
    value = signing.loads(token, salt=SALT, max_age=max_age)
    fields = {
        "key",
        "actor",
        "campaign",
        "action",
        "campaign_version",
        "runtime_version",
        "observed",
        "expires",
        "inventory",
        "selection",
        "reason",
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("Invalid delivery-control preview.")
    for name, item in value.items():
        valid = (
            type(item) is dict
            if name in {"inventory", "selection"}
            else type(item) is int and item > 0
            if name in {"campaign_version", "runtime_version"}
            else type(item) is str
        )
        if not valid:
            raise ValueError("Invalid delivery-control preview.")
    return value


def confirm(request, service, campaign_id, *, token):
    """Commit control and holds together; current authority still gates exact replay."""
    binding = _binding(token)
    with campaign_transaction(campaign_id, correlation_id=current_correlation()) as (
        campaign,
        runtime,
    ):
        actor, _, _ = _current(request, service, campaign_id)
        if (binding["actor"], binding["campaign"]) != (
            str(actor.identity),
            str(campaign_id),
        ):
            raise PermissionError("Delivery preview belongs to another scope.")
        values = {
            "campaign_id": campaign_id,
            "actor_id": actor.identity,
            "action": binding["action"],
            "preview_at": datetime.fromisoformat(binding["observed"]),
            "expires_at": datetime.fromisoformat(binding["expires"]),
            "expected_campaign_version": binding["campaign_version"],
            "expected_runtime_version": binding["runtime_version"],
            "inventory": binding["inventory"],
            "selection": binding["selection"],
            "reason": binding["reason"],
        }
        key = UUID(binding["key"])
        previous = DeliveryControlCommand.objects.filter(pk=key).first()
        if previous:
            if binding["action"] == "pause":
                # Pause captures the live inventory at commit, not the earlier
                # informational counts. All other signed intent stays exact.
                values["inventory"] = previous.inventory
            if any(getattr(previous, name) != value for name, value in values.items()):
                raise PermissionError("Delivery command belongs to another intent.")
            return previous
        _binding(token, max_age=300)
        current_inventory = inventory(campaign_id)
        if (
            not _action_available(campaign, binding["action"])
            or not _available(campaign, runtime)
            or campaign.version != binding["campaign_version"]
            or runtime.version != binding["runtime_version"]
            or _now() >= values["expires_at"]
            or any(
                values[name].utcoffset() != timedelta(0)
                for name in ("preview_at", "expires_at")
            )
            or (
                binding["action"] != "pause"
                and current_inventory != binding["inventory"]
            )
            or (
                binding["action"] == "resume"
                and (
                    binding["selection"] != _resume_selection(campaign)
                    or not binding["selection"].get("health", {}).get("ready")
                )
            )
            or (
                binding["action"] == "resolve"
                and binding["selection"]
                != _closed_selection(
                    campaign,
                    binding["inventory"],
                    decision=binding["selection"].get("decision"),
                    types=binding["selection"].get("types"),
                )
            )
        ):
            raise StaleRecordError("Delivery inputs changed; review a new preview.")
        if binding["action"] == "pause":
            values["inventory"] = current_inventory
        return DeliveryControlCommand.objects.create(
            id=key,
            **values,
            control_id=(
                None
                if binding["action"] == "resolve"
                and not _clears_pause(
                    binding["inventory"], binding["selection"]["types"]
                )
                else uuid4()
            ),
            session_id=request.portal_session.pk,
            authenticated_at=require_fresh(request),
            correlation_id=current_correlation(),
        )
