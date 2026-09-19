"""Server-owned readiness preview and irreversible Testing-cleanup admission.

Signed previews bind reviewed inputs, not authority. The final cleanup command
re-reads current inputs under the common work lock before invalidating rehearsal
access and publishing the existing bounded cleanup task. No network I/O occurs
under that lock and no browser supplies target rows, counts or callbacks.
"""

from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.db import connection, connections

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.cleanup_requests import begin_cleanup
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.production_storage import _status
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import DeploymentProfile, _origin
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.origin_check import check_public_origin
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .admin_editing import editable_configuration, principal
from .go_live_inputs import collect_inputs
from .sessions import database_now

SALT = "stewardship-go-live-cleanup-preview-v1"
PREVIEW_SECONDS = 300


def configured_origin():
    """Use the assembled deployment, never Host or forwarded/browser form fields."""
    try:
        profile = DeploymentProfile(settings.STEWARDSHIP_DEPLOYMENT_PROFILE)
        origin = _origin(settings.STEWARDSHIP_PUBLIC_ORIGIN, profile)
    except (AttributeError, ValueError):
        raise ConfigError("Deployment origin verification is unavailable.") from None
    return origin, profile


def verify_preview(request, service, campaign_id):
    """An explicit Admin action checks DNS outside SQL, then snapshots current data."""
    if connection.in_atomic_block:
        raise StorageInvariantError("Origin verification cannot hold a transaction.")
    actor = principal(request, service)
    editable_configuration(service)
    origin, profile = configured_origin()
    connections.close_all()
    verified = check_public_origin(origin, profile)
    # The originating login can expire or lose its role during the bounded
    # resolver call. collect_inputs rechecks it before reading private inputs.
    inputs = collect_inputs(request, service, campaign_id)
    token = None
    if verified and not inputs.problems:
        token = signing.dumps(
            {
                "actor": str(actor.identity),
                "campaign": str(campaign_id),
                "key": str(uuid4()),
                "digest": inputs.digest,
                "origin": origin,
                "profile": profile.value,
            },
            salt=SALT,
        )
    return inputs, verified, token


def start_cleanup(request, service, campaign_id, *, preview_token, acknowledge):
    """Recheck one exact preview; retry returns its original durable receipt.

    Authentication is current for every command. Initial acknowledgement records
    that login's actual Google-authenticated timestamp; the later activation
    owner independently requires fresh Google authentication and typed intent.
    """
    if (
        acknowledge is not True
        or type(preview_token) is not str
        or len(preview_token) > 4096
    ):
        raise ValueError("Explicit irreversible cleanup acknowledgement is required.")
    binding = signing.loads(preview_token, salt=SALT, max_age=PREVIEW_SECONDS)
    if (
        type(binding) is not dict
        or set(binding) != {"actor", "campaign", "key", "digest", "origin", "profile"}
        or any(type(value) is not str for value in binding.values())
    ):
        raise ValueError("Invalid go-live preview.")
    with work_transaction():
        # Waiting for another operation's lock must not extend the DNS proof.
        signing.loads(preview_token, salt=SALT, max_age=PREVIEW_SECONDS)
        actor = principal(request, service)
        if binding["actor"] != str(actor.identity) or binding["campaign"] != str(
            campaign_id
        ):
            raise PermissionError("Readiness preview belongs to another scope.")
        origin, profile = configured_origin()
        if binding["origin"] != origin or binding["profile"] != profile.value:
            raise StaleRecordError("Deployment origin changed after preview.")
        configuration = editable_configuration(service)
        previous = ProductionTransitionRequest.objects.filter(
            campaign_id=campaign_id, request_key=UUID(binding["key"])
        ).first()
        if previous is not None:
            if (
                previous.initiated_by_id != actor.identity
                or previous.aggregate.readiness_digest != binding["digest"]
            ):
                raise PermissionError("Cleanup replay belongs to another intent.")
            return _status(previous)
        inputs = collect_inputs(request, service, campaign_id)
        if inputs.problems or inputs.digest != binding["digest"]:
            raise StaleRecordError("Review current readiness and inventory again.")
        signing.loads(preview_token, salt=SALT, max_age=PREVIEW_SECONDS)
        instant = database_now()
        if (
            inputs.source.expires_at is None
            or instant >= inputs.source.expires_at
            or instant >= inputs.campaign.active_configuration.ends_at
        ):
            raise StaleRecordError(
                "Source readiness or the campaign interval expired during preview."
            )

        def admit(action, campaign, status):
            """Gate acquisition's own changes do not invalidate already locked proof."""
            current = principal(request, service, passive=True)
            live = editable_configuration(service)
            return (
                action in {"create", "invalidate_rehearsal", "create_task"}
                and current.identity == actor.identity
                and campaign.pk == campaign_id
                and live.active_configuration_id
                == configuration.active_configuration_id
                and live.current_campaign_id == campaign_id
                and live.mode == "testing"
            )

        return begin_cleanup(
            campaign_id=campaign_id,
            request_key=UUID(binding["key"]),
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            readiness_digest=inputs.digest,
            acknowledged_at=database_now(),
            reauthenticated_at=request.portal_session.authenticated_at,
            admit=admit,
        )
