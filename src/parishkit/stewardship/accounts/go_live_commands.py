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
from parishkit.stewardship.campaigns.go_live_sequencing import begin_attempt
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.production_storage import _status
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import DeploymentProfile, _origin
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.origin_check import check_public_origin
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.refusals import load_preview, return_link

from .admin_editing import editable_configuration, principal
from .go_live_inputs import collect_inputs
from .sessions import database_now, sign_in_instant

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


def origin_check(origin, profile):
    """Run the DNS and origin check outside SQL: no transaction waits on it.

    Used by Check readiness and public web address, and again by Start
    go-live's Apply (``go_live_views._start``), which never trusts an
    earlier result.
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Origin verification cannot hold a transaction.")
    connections.close_all()
    return check_public_origin(origin, profile)


def verify_preview(request, service, campaign_id):
    """An explicit Admin action checks DNS outside SQL, then snapshots current data."""
    if connection.in_atomic_block:
        raise StorageInvariantError("Origin verification cannot hold a transaction.")
    actor = principal(request, service)
    editable_configuration(service)
    origin, profile = configured_origin()
    verified = origin_check(origin, profile)
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


def _load(request, preview_token):
    """Check the preview's signature and five-minute lifetime.

    An expired preview is the plain "This preview is out of date" refusal
    linking back to the Check step, not a generic invalid value.
    """
    return load_preview(
        preview_token,
        salt=SALT,
        link=return_link(request),
        max_age=PREVIEW_SECONDS,
    )


def start_cleanup(
    request, service, campaign_id, *, preview_token, acknowledge, attempt=False
):
    """Recheck one exact preview; retry returns its original durable receipt.

    With ``attempt`` (the Go live page's Start go-live, #1585 slice 3), the
    same transaction also begins go-live attempt 1, queuing its full
    ParishSoft refresh, so the scheduler's go-live producer prepares the
    Family links by itself once cleanup and that refresh are done
    (background-processing spec, "Go-live sequencing"). A replay begins
    nothing more. The local seeding tool starts cleanup without one: it
    prepares the links itself, with the links owner, and confirms at once.

    The preview's five-minute lifetime (``_load``) bounds how old its DNS
    and origin proof may be when the preview comes straight from
    ``verify_preview`` (the local seeding tool). The Go live page's Start
    go-live signs the preview again just before this call, so for it the
    lifetime bounds nothing: its Apply runs ``origin_check`` again first
    (``go_live_views._start``), and the deployment origin and profile the
    preview binds are compared here.

    The acknowledgement records the admitted session's sign-in instant as
    ``reauthenticated_at`` (a sign-in of any age, #1547); the SQL guard
    requires it to equal the live session's ``authenticated_at``, which is
    exactly what ``sign_in_instant`` returns. The later activation owner
    independently requires typed intent.
    """
    if (
        acknowledge is not True
        or type(preview_token) is not str
        or len(preview_token) > 4096
    ):
        raise ValueError("Explicit irreversible cleanup acknowledgement is required.")
    binding = _load(request, preview_token)
    if (
        type(binding) is not dict
        or set(binding) != {"actor", "campaign", "key", "digest", "origin", "profile"}
        or any(type(value) is not str for value in binding.values())
    ):
        raise ValueError("Invalid go-live preview.")
    with work_transaction():
        # Waiting for another operation's lock must not take a preview past
        # its lifetime (which, for Start go-live's re-signed preview, is not
        # its DNS proof: its Apply re-ran that check just before).
        _load(request, preview_token)
        actor = principal(request, service)
        reauthenticated_at = sign_in_instant(request)
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
        _load(request, preview_token)
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

        status = begin_cleanup(
            campaign_id=campaign_id,
            request_key=UUID(binding["key"]),
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            readiness_digest=inputs.digest,
            acknowledged_at=database_now(),
            reauthenticated_at=reauthenticated_at,
            admit=admit,
        )
        if attempt:
            _begin_first_attempt(request, service, actor, status.request_id)
        return status


def _begin_first_attempt(request, service, actor, request_id):
    """Begin go-live attempt 1 of ``request_id`` as ``actor``, in the caller's lock.

    Its refresh is an ordinary manual refresh (``begin_attempt``), admitted
    only while the same Administrator is still signed in, rechecked under
    the refresh's own lock.
    """

    def authorize(scope):
        """The Administrator who applied Start, rechecked under the lock."""
        return principal(request, service, read_only=True).identity == actor.identity

    begin_attempt(
        ProductionTransitionRequest.objects.get(pk=request_id),
        number=1,
        actor_id=actor.identity,
        correlation_id=current_correlation(),
        authorize=authorize,
    )
