"""Session-owned logo staging and recoverable cleanup, subordinate to YAML selection."""

from contextlib import nullcontext
from datetime import timedelta
from uuid import UUID

from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .branding_files import BrandingFile, create_bundle, media_lock, remove_bundle
from .branding_models import BrandingAsset, BrandingBundle
from .sessions import database_now


def _admit(request, service, setup_attempt_id=None, *, passive=False):
    """Keep setup/configuration gates distinct; an optional ID is not authority."""
    if setup_attempt_id is None:
        actor = principal(request, service, passive=passive)
        return actor, editable_configuration(service)
    if not isinstance(setup_attempt_id, UUID):
        raise ValueError("Invalid setup attempt identity.")
    from .setup_models import SetupAttempt
    from .setup_staging import _admit as setup_admission
    from .setup_staging import _expiry

    actor, configuration = setup_admission(request, service, activity=not passive)
    attempt = (
        SetupAttempt.objects.select_related("source_task")
        .filter(
            pk=setup_attempt_id,
            owner_id=actor.identity,
            session_id=request.portal_session.pk,
            state__in=["collecting", "loading"],
        )
        .first()
    )
    if attempt is None or _expiry(attempt, database_now()) is not None:
        raise PermissionError("Setup upload is unavailable.")
    return actor, configuration


def _owned(request, service, bundle_id, *, setup_attempt_id=None, passive=False):
    """Recheck the original session and applied base before previewing or completing."""
    actor, configuration = _admit(request, service, setup_attempt_id, passive=passive)
    row = (
        BrandingBundle.objects.select_for_update()
        .filter(
            pk=bundle_id,
            owner_id=actor.identity,
            session_id=request.portal_session.pk,
            setup_attempt_id=setup_attempt_id,
        )
        .first()
    )
    if row is None:
        raise LookupError("Branding upload is unavailable.")
    if (
        row.base_id != configuration.active_configuration_id
        or row.expires_at <= database_now()
    ):
        raise StaleRecordError("Branding upload expired or its configuration changed.")
    return row


def stage_branding(
    request, service, media_root, graphics, *, base_digest, setup_attempt_id=None
):
    """Record ownership before writing files; publish receipts only after full fsync.

    Decode the bounded image before invoking this service. Failure leaves an
    unserved writing receipt for explicit or expiry cleanup, never an ambiguous
    partially applied logo. Filesystem exclusion spans both short SQL admissions.
    """
    with media_lock(media_root):
        with work_transaction():
            actor, configuration = _admit(request, service, setup_attempt_id)
            if configuration.active_configuration.digest != base_digest:
                raise StaleRecordError("The branding form's configuration changed.")
            row = BrandingBundle.objects.create(
                owner_id=actor.identity,
                actor_id=actor.identity,
                session_id=request.portal_session.pk,
                setup_attempt_id=setup_attempt_id,
                base_id=configuration.active_configuration_id,
                expires_at=database_now() + timedelta(hours=24),
                correlation_id=current_correlation(),
            )
        files = create_bundle(media_root, row.pk, graphics)
        with work_transaction():
            row = _owned(request, service, row.pk, setup_attempt_id=setup_attempt_id)
            if row.state != "writing":
                raise StaleRecordError("Branding upload is no longer writable.")
            for item in files:
                BrandingAsset.objects.create(
                    id=item.reference,
                    bundle=row,
                    label=item.label,
                    width=item.width,
                    height=item.height,
                    size=item.size,
                    sha256=item.sha256,
                    actor_id=row.owner_id,
                    correlation_id=current_correlation(),
                )
            _transition(row, "ready", actor_id=row.owner_id)
        return row.pk


def _transition(row, state, *, actor_id):
    """SQL enforces legal edges and prevents cleanup of retained branding files."""
    BrandingBundle.objects.filter(pk=row.pk, version=row.version).update(
        state=state,
        version=F("version") + 1,
        actor_id=actor_id,
        correlation_id=current_correlation(),
    )
    row.refresh_from_db()


def staged_bundle(request, service, bundle_id, *, setup_attempt_id=None):
    """Only the original Admin session can preview an unapplied normalized upload."""
    with work_transaction():
        row = _owned(
            request, service, bundle_id, setup_attempt_id=setup_attempt_id, passive=True
        )
        if row.state != "ready":
            raise ConfigError("Branding upload is not ready.")
        return row, tuple(row.assets.order_by("label"))


def file_receipt(asset):
    """Do not derive file names or metadata from request parameters."""
    return BrandingFile(
        asset.pk, asset.label, asset.width, asset.height, asset.size, asset.sha256
    )


def cleanup_branding(
    media_root, bundle_id, *, admit, actor_id=None, effect=nullcontext
):
    """Internal expiry/cancellation port; the caller must authorize the exact row.

    An owning callback runs inside both short metadata transactions. It must
    enforce expiry or a current Admin's cancellation and original session scope.
    Files are removed only after the database durably rejects future publication.
    """
    if not isinstance(bundle_id, UUID) or not callable(admit) or not callable(effect):
        raise ValueError("Branding cleanup requires explicit owning admission.")
    with media_lock(media_root):
        with effect(), work_transaction():
            row = BrandingBundle.objects.select_for_update().get(pk=bundle_id)
            if admit(row) is not True:
                raise PermissionError("Branding cleanup is not admitted.")
            if row.state == "scrubbed":
                return
            if row.state != "cleanup_pending":
                _transition(row, "cleanup_pending", actor_id=actor_id)
        remove_bundle(media_root, row.pk)
        with effect(), work_transaction():
            row = BrandingBundle.objects.select_for_update().get(pk=bundle_id)
            if admit(row) is not True:
                raise PermissionError("Branding cleanup is not admitted.")
            _transition(row, "scrubbed", actor_id=actor_id)
