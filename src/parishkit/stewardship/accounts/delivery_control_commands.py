"""Current-Admin delivery controls, without transport keys or provider effects."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import connection

from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import _now, campaign_transaction
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .content_models import ContentVersion
from .delivery_forecast import next_due
from .sessions import require_fresh

SALT = "stewardship-delivery-control-v1"


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
            "inventory": inventory(campaign_id),
            "next_due": next_due(campaign, _now()),
            "health": health(campaign_id),
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
    if (
        type(reason) is not str
        or not reason.strip()
        or len(reason) > 1024
        or "\x00" in reason
    ):
        raise ValueError("Enter a pause reason of at most 1,024 characters.")
    with work_transaction():
        actor, runtime, campaign = _current(request, service, campaign_id)
        if not _available(campaign, runtime) or campaign.delivery_paused:
            raise StaleRecordError("Delivery pause is not currently available.")
        now = _now()
        binding = {
            "key": str(uuid4()),
            "actor": str(actor.identity),
            "campaign": str(campaign_id),
            "action": "pause",
            "campaign_version": campaign.version,
            "runtime_version": runtime.version,
            "observed": now.isoformat(),
            "expires": (now + timedelta(minutes=5)).isoformat(),
            "inventory": inventory(campaign_id),
            "selection": {},
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
            if any(getattr(previous, name) != value for name, value in values.items()):
                raise PermissionError("Delivery command belongs to another intent.")
            return previous
        _binding(token, max_age=300)
        if (
            binding["action"] != "pause"
            or campaign.delivery_paused
            or not _available(campaign, runtime)
            or campaign.version != binding["campaign_version"]
            or runtime.version != binding["runtime_version"]
            or _now() >= values["expires_at"]
            or any(
                values[name].utcoffset() != timedelta(0)
                for name in ("preview_at", "expires_at")
            )
            or inventory(campaign_id) != binding["inventory"]
        ):
            raise StaleRecordError("Delivery inputs changed; review a new preview.")
        return DeliveryControlCommand.objects.create(
            id=key,
            **values,
            control_id=uuid4(),
            session_id=request.portal_session.pk,
            authenticated_at=require_fresh(request),
            correlation_id=current_correlation(),
        )
