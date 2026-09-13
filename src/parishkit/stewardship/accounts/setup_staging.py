"""Authenticated setup-attempt admission and idempotent metadata expiry.

This owns no configured marker and installs no credentials. Later setup stages
must use this exact attempt/session binding, including in external-page and
final promotion checks. Expiry is a fence, not proof that artifact cleanup has
already finished; target-specific owners retain that responsibility.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .configuration_installation import coherent_configuration
from .models import PortalSession
from .policy import Capability, allows
from .policy_models import AddressRule, PortalUser
from .runtime_models import SystemConfiguration
from .sessions import authenticated_admin, database_now
from .setup_models import SetupAttempt
from .setup_policy import ExpiryReason, SetupState, SetupWindow


@dataclass(frozen=True)
class SetupStatus:
    """Safe attempt receipt; IDs are correlation, never authority to access staging."""

    attempt_id: UUID
    version: int
    state: SetupState


def _status(row):
    """Copy only immutable result values instead of exposing a mutable ORM instance."""
    return SetupStatus(row.pk, row.version, SetupState(row.state))


def _admit(request, service, *, activity):
    """Only a current Admin in unconfigured Testing can use the wizard owner."""
    actor = authenticated_admin(request, store=service.store, activity=activity)
    if not allows(actor, Capability.CONFIGURE):
        raise PermissionError("Initial setup requires an Administrator.")
    configuration = coherent_configuration(service.store)
    if (
        service.configured()
        or configuration.restore_review_required
        or configuration.mode != "testing"
        or configuration.current_campaign_id is not None
        or configuration.active_configuration.validation_schema != "bootstrap-policy-v1"
    ):
        raise ConfigError("Initial setup is unavailable.")
    return actor, configuration


def _window(row, session):
    """Resolve original Task creation, never a caller's claimed watchdog deadline."""
    task = row.source_task
    return SetupWindow(
        session_id=row.session_id,
        state=SetupState(row.state),
        activity_at=session.last_activity_at,
        absolute_at=session.expires_at,
        task_id=task.pk if task else None,
        task_created_at=task.created_at if task else None,
        renewed_at=row.renewed_at,
    )


def _expiry(row, now):
    """A missing or revoked original session is never replaced by another login."""
    session = (
        PortalSession.objects.only(
            "id", "principal_id", "revoked_at", "expires_at", "last_activity_at"
        )
        .filter(pk=row.session_id, principal_id=row.owner_id)
        .first()
    )
    owner = PortalUser.objects.filter(pk=row.owner_id, disabled=False).values("email")
    current = SystemConfiguration.objects.filter(
        mode="testing", restore_review_required=False
    ).values("active_configuration_id")
    authorized = AddressRule.objects.filter(
        configuration_id__in=current, email__in=owner, roles__contains=["administrator"]
    ).exists()
    if session is None or not authorized:
        return ExpiryReason.SESSION
    return _window(row, session).expiry(now, session_live=session.revoked_at is None)


def _expire(row, *, actor_id, reason):
    """Commit the fence once; SQL owns its exact reason and expiry timestamp."""
    if row.state == SetupState.EXPIRED:
        return row
    SetupAttempt.objects.filter(pk=row.pk, version=row.version).update(
        state=SetupState.EXPIRED,
        expired_at=database_now(),
        expiry_reason=reason,
        actor_id=actor_id,
        correlation_id=current_correlation(),
        version=F("version") + 1,
    )
    row.refresh_from_db()
    return row


def begin_setup(request, service):
    """Start one session-owned attempt or return that session's current receipt."""
    with work_transaction():
        actor, configuration = _admit(request, service, activity=True)
        session_id = request.portal_session.pk
        existing = (
            SetupAttempt.objects.select_for_update()
            .filter(session_id=session_id)
            .first()
        )
        if existing is not None:
            reason = _expiry(existing, database_now())
            if reason is not None and existing.state != SetupState.COMPLETED:
                _expire(existing, actor_id=actor.identity, reason=reason)
            return _status(existing)
        pending = (
            SetupAttempt.objects.select_for_update()
            .filter(
                state__in=[SetupState.COLLECTING, SetupState.LOADING, SetupState.FROZEN]
            )
            .first()
        )
        if pending is not None:
            reason = _expiry(pending, database_now())
            if reason is None:
                raise StaleRecordError("Initial setup is already in progress.")
            _expire(pending, actor_id=None, reason=reason)
        row = SetupAttempt.objects.create(
            session_id=session_id,
            owner_id=actor.identity,
            actor_id=actor.identity,
            base_id=configuration.active_configuration_id,
        )
        return _status(row)


def cancel_setup(request, service, attempt_id):
    """Cancel only this login's attempt; repeats return its retained expired receipt."""
    if not isinstance(attempt_id, UUID):
        raise TypeError("An exact setup attempt UUID is required.")
    with work_transaction():
        actor, _ = _admit(request, service, activity=False)
        row = (
            SetupAttempt.objects.select_for_update()
            .filter(
                pk=attempt_id,
                session_id=request.portal_session.pk,
                owner_id=actor.identity,
            )
            .first()
        )
        if row is None:
            raise LookupError("Initial setup is unavailable.")
        reason = _expiry(row, database_now()) or ExpiryReason.CANCELLED
        return _status(_expire(row, actor_id=actor.identity, reason=reason))


def expire_setup_attempts():
    """Fence the single abandoned attempt before its target owners scrub artifacts."""
    with work_transaction():
        if not SystemConfiguration.objects.filter(
            restore_review_required=False
        ).exists():
            return 0
        row = (
            SetupAttempt.objects.select_for_update()
            .filter(
                state__in=[SetupState.COLLECTING, SetupState.LOADING, SetupState.FROZEN]
            )
            .first()
        )
        if row is None:
            return 0
        reason = _expiry(row, database_now())
        if reason is None:
            return 0
        _expire(row, actor_id=None, reason=reason)
        return 1


def produce_setup_expiry(guard):
    """The actual scheduler fences abandoned staging before artifact owners scan it."""
    from django.db import connection

    from parishkit.stewardship.jobs.scheduler import SchedulerGuard
    from parishkit.stewardship.storage import StorageInvariantError

    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Setup expiry requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Setup expiry owns its short transaction.")
    guard.check()
    count = expire_setup_attempts()
    guard.check()
    return count
