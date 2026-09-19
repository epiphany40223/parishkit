"""Exact current-Admin pre-start withdrawal; no provider calls or restored data."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import connection

from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.runtime import _now, campaign_transaction
from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .sessions import require_fresh

SALT = "stewardship-production-withdrawal-v1"


def _current(request, service, campaign_id, *, passive=False):
    """Authorize before inspecting private history, including identical replays."""
    actor = principal(request, service, passive=passive)
    configuration = editable_configuration(service)
    if configuration.current_campaign_id != campaign_id:
        raise PermissionError("Withdrawal belongs to the current campaign.")
    confirmation = (
        ProductionConfirmation.objects.filter(request__campaign_id=campaign_id)
        .select_related("request__campaign__active_configuration")
        .latest("created_at")
    )
    return actor, configuration, confirmation


def available(campaign, runtime, now):
    """Do not offer a late withdrawal just because the boundary worker is delayed."""
    return (
        runtime.mode == "production"
        and campaign.state == "scheduled"
        and not campaign.ever_active
        and not campaign.delivery_paused
        and now < campaign.active_configuration.starts_at
    )


def _inventory(campaign_id):
    """Read the same count-only SQL projection enforced by the private effect."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT inventory FROM stewardship_withdrawal_inventory "
            "WHERE campaign_id=%s",
            [campaign_id],
        )
        value = cursor.fetchone()[0]
    return json.loads(value) if isinstance(value, str) else value


def page(request, service, campaign_id):
    """Passive history/status cannot create a command or extend session activity."""
    with work_transaction():
        _, runtime, confirmation = _current(request, service, campaign_id, passive=True)
        campaign = confirmation.request.campaign
        try:
            require_fresh(request)
            fresh = True
        except PermissionError:
            fresh = False
        return {
            "campaign": campaign,
            "available": available(campaign, runtime, _now()),
            "fresh": fresh,
            "withdrawal": ProductionWithdrawal.objects.filter(
                confirmation=confirmation
            ).first(),
        }


def preview(request, service, campaign_id, *, reason, acknowledged):
    """Bind one reason, irreversible-cleanup acknowledgement and current work set."""
    if type(reason) is not str or not reason.strip() or len(reason) > 2000:
        raise ValueError("Enter a withdrawal reason of at most 2,000 characters.")
    if acknowledged is not True:
        raise ValueError("Acknowledge that completed Testing cleanup cannot be undone.")
    with work_transaction():
        actor, runtime, confirmation = _current(request, service, campaign_id)
        campaign, now = confirmation.request.campaign, _now()
        if not available(campaign, runtime, now):
            raise StaleRecordError(
                "Withdrawal is available only before the campaign starts."
            )
        inventory = _inventory(campaign_id)
        binding = {
            "actor": str(actor.identity),
            "campaign": str(campaign_id),
            "confirmation": str(confirmation.pk),
            "key": str(uuid4()),
            "campaign_version": campaign.version,
            "runtime_version": runtime.version,
            "observed": now.isoformat(),
            "expires": min(
                now + timedelta(minutes=5), campaign.active_configuration.starts_at
            ).isoformat(),
            "inventory": inventory,
            "reason": reason.strip(),
        }
        return binding, signing.dumps(binding, salt=SALT) if not inventory[
            "blocking"
        ] else None


def _binding(token, *, max_age=None):
    """A signature binds intent; current SQL/session authorization remains required."""
    if type(token) is not str or len(token) > 16384:
        raise ValueError("Invalid withdrawal preview.")
    value = signing.loads(token, salt=SALT, max_age=max_age)
    fields = {
        "actor",
        "campaign",
        "confirmation",
        "key",
        "campaign_version",
        "runtime_version",
        "observed",
        "expires",
        "inventory",
        "reason",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or type(value["inventory"]) is not dict
    ):
        raise ValueError("Invalid withdrawal preview.")
    for key, item in value.items():
        if key in {"campaign_version", "runtime_version"}:
            if type(item) is not int or item < 1:
                raise ValueError("Invalid withdrawal version.")
        elif key != "inventory" and type(item) is not str:
            raise ValueError("Invalid withdrawal preview.")
    return value


def withdraw(request, service, campaign_id, *, token):
    """Cancel safe work and withdraw atomically, or leave every record unchanged.

    The compiled web owner enforces signed browser intent; SQL independently
    checks scope, session, clocks, versions and the exact work inventory. SQL
    credentials identify this trusted service, not an arbitrary browser user.
    """
    binding = _binding(token)
    with campaign_transaction(campaign_id, correlation_id=current_correlation()) as (
        campaign,
        runtime,
    ):
        actor, _, confirmation = _current(request, service, campaign_id)
        if (binding["actor"], binding["campaign"]) != (
            str(actor.identity),
            str(campaign_id),
        ):
            raise PermissionError("Withdrawal preview belongs to another scope.")
        values = {
            "confirmation_id": UUID(binding["confirmation"]),
            "request_key": UUID(binding["key"]),
            "preview_at": datetime.fromisoformat(binding["observed"]),
            "expires_at": datetime.fromisoformat(binding["expires"]),
            "expected_campaign_version": binding["campaign_version"],
            "expected_runtime_version": binding["runtime_version"],
            "inventory": binding["inventory"],
            "reason": binding["reason"],
            "cleanup_acknowledged": True,
            "actor_id": actor.identity,
        }
        previous = ProductionWithdrawal.objects.filter(
            request_key=values["request_key"]
        ).first()
        if previous:
            if any(getattr(previous, key) != value for key, value in values.items()):
                raise PermissionError("Withdrawal replay belongs to another intent.")
            return previous
        _binding(token, max_age=300)
        if (
            values["confirmation_id"] != confirmation.pk
            or campaign.version != values["expected_campaign_version"]
            or runtime.version != values["expected_runtime_version"]
            or not available(campaign, runtime, _now())
            or _now() >= values["expires_at"]
            or any(
                values[key].utcoffset() != timedelta(0)
                for key in ("preview_at", "expires_at")
            )
        ):
            raise StaleRecordError(
                "Withdrawal changed or expired; review a new preview."
            )
        inventory = _inventory(campaign_id)
        if inventory != binding["inventory"] or inventory["blocking"]:
            raise StaleRecordError("Withdrawal work changed; review a new preview.")
        return ProductionWithdrawal.objects.create(
            **values,
            transition_id=uuid4(),
            session_id=request.portal_session.pk,
            authenticated_at=require_fresh(request),
            correlation_id=current_correlation(),
        )
