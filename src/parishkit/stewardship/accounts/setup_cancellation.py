"""Original-login cancellation remains available across setup's YAML select gap.

This is the sole read-only exception to ordinary configuration coherence. The
still-applied bootstrap policy authenticates only cancellation of its exact
bound setup successor. It never admits editing, another session, a new attempt,
an applied successor, or an arbitrary mismatched manifest.
"""

from dataclasses import dataclass
from uuid import UUID

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction

from .models import PortalSession
from .policy import Capability, allows
from .runtime_models import ConfigurationActivation, SystemConfiguration
from .sessions import authenticated_admin, database_now
from .setup_models import SetupConfigurationIntent
from .setup_policy import ExpiryReason, SetupState
from .setup_staging import _expire, _expiry, _status


@dataclass(frozen=True)
class _CancellationAuthority:
    """Use applied bootstrap policy only while both real identities stay exact.

    This object never leaves this module or enters the shared auth runtime. Each
    read rechecks actual authority; the enclosing work lock prevents concurrent
    activation, and a manifest change cannot silently expand the exception.
    """

    store: object
    base: object
    candidate_id: UUID
    candidate_digest: str

    def _verify(self):
        """The installer may select only this exact base or bound successor."""
        selected = self.store.active()
        if selected is None or (selected.version_id, selected.digest) not in {
            (self.base.version_id, self.base.digest),
            (self.candidate_id, self.candidate_digest),
        }:
            raise ConfigError(
                "Setup cancellation has unrelated configuration authority."
            )

    def active(self):
        """Return only the verified still-applied predecessor's complete policy."""
        self._verify()
        return self.base

    def manifest_reference(self):
        """Perform the second real manifest check required by policy admission."""
        self._verify()
        return self.base.version_id, self.base.digest


def _owned(request, service, attempt_id=None):
    """Resolve original session binding before considering predecessor authority."""
    if service.configured():
        raise PermissionError("Completed setup cannot be cancelled.")
    session = PortalSession.objects.filter(
        session_id=request.session.session_key
    ).first()
    if session is None:
        raise PermissionError("Setup cancellation requires its original login.")
    rows = SetupConfigurationIntent.objects.select_related(
        "request__base", "attempt"
    ).filter(attempt__session_id=session.pk, attempt__owner_id=session.principal_id)
    if attempt_id is not None:
        rows = rows.filter(attempt_id=attempt_id)
    intent = rows.first()
    if intent is None:
        raise LookupError("Setup finalization is unavailable.")
    runtime = SystemConfiguration.objects.get()
    if (
        runtime.mode != "testing"
        or runtime.restore_review_required
        or runtime.current_campaign_id is not None
        or runtime.active_configuration_id != intent.attempt.base_id
        or intent.request.base_id != intent.attempt.base_id
        or intent.request.base.validation_schema != "bootstrap-policy-v1"
        or intent.attempt.state not in {SetupState.FROZEN, SetupState.EXPIRED}
        or ConfigurationActivation.objects.filter(request=intent.request).exists()
    ):
        raise PermissionError("Setup finalization cannot be cancelled.")
    authority = _CancellationAuthority(
        service.store,
        service.store.read_version(intent.request.base_id),
        intent.request.candidate_version_id,
        intent.request.candidate_digest,
    )
    actor = authenticated_admin(request, store=authority)
    if (
        not allows(actor, Capability.CONFIGURE)
        or actor.identity != intent.attempt.owner_id
        or request.portal_session.pk != intent.attempt.session_id
    ):
        raise PermissionError("Setup cancellation requires its original Administrator.")
    return actor, intent.attempt


def cancellation_status(request, service):
    """Return only safe attempt metadata; a GET cannot cancel or renew activity."""
    with work_transaction():
        _, attempt = _owned(request, service)
        return _status(attempt)


def cancel_finalizing_setup(request, service, attempt_id):
    """Fence the exact attempt; only the installer may journal and restore files."""
    if not isinstance(attempt_id, UUID):
        raise TypeError("An exact setup attempt UUID is required.")
    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        # Expiry scrubs public drafts in this same transaction. Prepared source,
        # secret and file cleanup remains with its distinct fenced owner.
        reason = _expiry(attempt, database_now()) or ExpiryReason.CANCELLED
        return _status(_expire(attempt, actor_id=actor.identity, reason=reason))
