"""Fresh exact-preview Production confirmation, without sending mail."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import connection, connections

from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.production_models import ProductionTransitionEvent
from parishkit.stewardship.campaigns.runtime import _now, campaign_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from . import go_live_commands
from .admin_editing import editable_configuration, principal
from .confirmation_preview import collect_preview
from .confirmation_readiness import collect_readiness
from .sessions import require_fresh

SALT = "stewardship-production-confirmation-v1"


def verify_preview(request, service, campaign_id, transition_id, preparation_id):
    """Verify the public origin outside SQL, then collect exact current impact."""
    if connection.in_atomic_block:
        raise StorageInvariantError(
            "Confirmation verification must own its transaction."
        )
    actor = principal(request, service)
    editable_configuration(service)
    origin, profile = go_live_commands.configured_origin()
    connections.close_all()
    verified = go_live_commands.check_public_origin(origin, profile)
    preview = collect_preview(
        request, service, campaign_id, transition_id, preparation_id
    )
    token = None
    if verified and not preview.problems:
        families, digests = preview.families.counts, preview.digests
        token = signing.dumps(
            {
                "actor": str(actor.identity),
                "campaign": str(campaign_id),
                "request": str(transition_id),
                "preparation": str(preparation_id),
                "key": str(uuid4()),
                "digest": preview.readiness.digest,
                "origin": origin,
                "profile": profile.value,
                "expires": preview.expires_at.isoformat(),
                "observed": preview.readiness.observed_at.isoformat(),
                "target": preview.readiness.target_state,
                "counts": {
                    "active_families": families.active,
                    "eligible_families": families.email_eligible,
                    "no_email_families": families.no_eligible_email,
                    "family_messages": families.messages,
                    "daily_messages": digests.daily_messages,
                    "weekly_messages": digests.weekly_messages,
                    "coalesced_slots": families.coalesced_slots
                    + digests.coalesced_slots,
                },
            },
            salt=SALT,
        )
    return preview, verified, token


def _binding(token, *, max_age=None):
    """Parse only the server's closed preview shape; signatures do not authorize."""
    if type(token) is not str or len(token) > 8192:
        raise ValueError("Invalid Production confirmation preview.")
    value = signing.loads(token, salt=SALT, max_age=max_age)
    if (
        type(value) is not dict
        or set(value)
        != {
            "actor",
            "campaign",
            "request",
            "preparation",
            "key",
            "digest",
            "origin",
            "profile",
            "expires",
            "observed",
            "target",
            "counts",
        }
        or any(type(item) is not str for key, item in value.items() if key != "counts")
        or type(value["counts"]) is not dict
    ):
        raise ValueError("Invalid Production confirmation preview.")
    return value


def confirm(
    request, service, campaign_id, transition_id, preparation_id, *, token, typed
):
    """Atomically consume one exact preview under installation/work/lifecycle order.

    Current authority also gates replays. No external checks, impact enumeration,
    token creation or per-Family mail writes occur in this final transaction.
    The private confirmation effect owns all privileged database changes.

    As with the existing privileged web command owners, this compiled adapter
    is trusted to enforce browser intent, DNS verification and configuration
    readiness. The SQL login identifies the web service, not a browser user;
    its guard independently enforces durable scope/session/version invariants,
    but is not a second verifier of Django signatures or external DNS evidence.
    """
    if type(typed) is not str or typed.strip() != "Production":
        raise ValueError("Type Production to confirm this transition.")
    binding = _binding(token)
    with campaign_transaction(campaign_id, correlation_id=current_correlation()) as (
        campaign,
        runtime,
    ):
        actor = principal(request, service)
        configuration = editable_configuration(service)
        if (
            binding["actor"],
            binding["campaign"],
            binding["request"],
            binding["preparation"],
        ) != (
            str(actor.identity),
            str(campaign_id),
            str(transition_id),
            str(preparation_id),
        ) or configuration.current_campaign_id != campaign_id:
            raise PermissionError("Production confirmation belongs to another scope.")
        previous = ProductionConfirmation.objects.filter(
            request_key=UUID(binding["key"])
        ).first()
        if previous is not None:
            if (
                previous.request_id != transition_id
                or previous.preparation_id != preparation_id
                or previous.actor_id != actor.identity
                or previous.readiness_digest != binding["digest"]
                or previous.preview_counts != binding["counts"]
                or previous.target_state != binding["target"]
                or previous.preview_at.isoformat() != binding["observed"]
                or previous.expires_at.isoformat() != binding["expires"]
            ):
                raise PermissionError("Confirmation replay belongs to another intent.")
            return previous
        # A committed replay needs current authority, not renewed DNS evidence.
        origin, profile = go_live_commands.configured_origin()
        if (origin, profile.value) != (binding["origin"], binding["profile"]):
            raise StaleRecordError("Public origin changed after preview.")
        _binding(token, max_age=300)
        state = collect_readiness(
            request, service, campaign_id, transition_id, preparation_id
        )
        if (
            state.problems
            or state.digest != binding["digest"]
            or state.target_state != binding["target"]
        ):
            raise StaleRecordError("Review a fresh Production confirmation preview.")
        authenticated = require_fresh(request)
        completed = (
            ProductionTransitionEvent.objects.filter(
                request_id=transition_id, action="complete"
            )
            .latest("created_at")
            .created_at
        )
        if authenticated < completed:
            raise PermissionError("Authenticate with Google after cleanup finishes.")
        expires = datetime.fromisoformat(binding["expires"])
        observed = datetime.fromisoformat(binding["observed"])
        if any(value.utcoffset() != timedelta(0) for value in (expires, observed)):
            raise ValueError("Confirmation evidence requires UTC instants.")
        if _now() >= expires:
            raise StaleRecordError("Confirmation preview expired; verify it again.")
        return ProductionConfirmation.objects.create(
            request_id=transition_id,
            preparation_id=preparation_id,
            activation_id=uuid4(),
            generation_id=state.generation_id,
            request_key=UUID(binding["key"]),
            session_id=request.portal_session.pk,
            authenticated_at=authenticated,
            expires_at=expires,
            preview_at=observed,
            preview_counts=binding["counts"],
            expected_request_version=state.transition.version,
            expected_campaign_version=campaign.version,
            expected_runtime_version=runtime.version,
            impact_revision=state.impact_revision,
            readiness_digest=state.digest,
            target_state=state.target_state,
            actor_id=actor.identity,
            correlation_id=current_correlation(),
        )
