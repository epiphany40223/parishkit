"""Target-owned transfer from frozen wizard input to ordinary file installation.

Private bytes never pass through the web or general worker. Initial requests
retain their rollback files until the atomic configured marker, not merely until
the first consumer acknowledgement. No method here activates configuration.
"""

from uuid import uuid4, uuid5

from django.db import connection

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

from .credential_database import admit_installer_database
from .credential_files import CredentialFiles
from .key_files import file_fingerprint
from .models import PortalSession
from .provider_models import ProviderValidationContext
from .secret_models import (
    SECRET_PENDING,
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from .secret_requests import _receipt
from .setup_install_models import SetupCredentialInstallation
from .setup_models import SetupAttempt
from .setup_readiness_models import SetupReadinessBinding
from .setup_secret_models import SetupSealedCredential

TARGETS = frozenset({"parishsoft", "google_workspace", "slack"})


def ready_live(readiness_id):
    """Repeat original-login/frozen/base admission under the current SQL identity."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_install_ready_live_v1(%s)", [readiness_id]
        )
        return cursor.fetchone() == (True,)


def stage_initial_credential(files):
    """Copy only the target's exact sealed input in an atomic installation intake.

    The target file lock pins the private predecessor while work/secret locks
    serialize the public journal. Keeping the original credential UUID as the
    installation request UUID preserves the envelope's authenticated namespace;
    copying ciphertext is not a decryption or an authority bypass.
    """
    if not isinstance(files, CredentialFiles) or files.private.target not in TARGETS:
        raise TypeError("An isolated setup credential target is required.")
    target = files.private.target
    admit_installer_database(target)
    with files.lock(), work_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(736213,1)")
        readiness = (
            SetupReadinessBinding.objects.only("id", "intent_id")
            .filter(intent__attempt__state="frozen")
            .first()
        )
        if readiness is None or not ready_live(readiness.pk):
            return None
        credential = SetupSealedCredential.objects.filter(
            attempt_id=readiness.intent.attempt_id, target=target, scrubbed_at=None
        ).first()
        if credential is None:
            return None
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.stewardship_setup_install_selected_v1(%s,%s,%s,%s)",
                [
                    readiness.pk,
                    credential.pk,
                    credential.version,
                    credential.fingerprint,
                ],
            )
            if cursor.fetchone() != (True,):
                return None  # Disabled optional Slack input is deliberately inert.
        existing = SetupCredentialInstallation.objects.filter(
            readiness=readiness, target=target
        ).first()
        if existing is not None:
            return _receipt(
                SecretReplacementRequest.objects.get(pk=existing.request_id)
            )
        if SecretReplacementRequest.objects.filter(
            target=target, state__in=SECRET_PENDING
        ).exists():
            return None
        attempt = SetupAttempt.objects.only("owner_id", "session_id").get(
            pk=credential.attempt_id
        )
        login = PortalSession.objects.only("authenticated_at", "expires_at").get(
            pk=attempt.session_id
        )
        predecessor = files._selected()
        consumers = sorted(
            role.value for role, names in ALLOWED_SECRETS.items() if target in names
        )
        correlation_id = uuid4()
        request = SecretReplacementRequest.objects.create(
            id=credential.pk,
            target=target,
            staging_reference=uuid5(credential.pk, "initial-install-staging"),
            requested_by_id=attempt.owner_id,
            actor_id=attempt.owner_id,
            correlation_id=correlation_id,
            reauthenticated_at=login.authenticated_at,
            expires_at=login.expires_at,
            expected_fingerprint=file_fingerprint(predecessor)
            if predecessor is not None
            else None,
            required_consumers=consumers,
        )
        SetupCredentialInstallation.objects.create(
            readiness=readiness,
            credential=credential,
            request=request,
            target=target,
            credential_version=credential.version,
            fingerprint=credential.fingerprint,
            actor_id=attempt.owner_id,
            correlation_id=correlation_id,
        )
        SealedCredentialStaging.objects.create(
            reference=request.staging_reference,
            request=request,
            target=target,
            ciphertext=credential.ciphertext,
            fingerprint=credential.fingerprint,
        )
        ProviderValidationContext.objects.create(
            request=request,
            target=target,
            settings=credential.settings,
            actor_id=attempt.owner_id,
            correlation_id=correlation_id,
        )
        return _receipt(request)


def initial_state(request):
    """Return bound/live/completion evidence without acquiring locks out of order.

    Completed and expired setup states are irreversible. The independently
    guarded secret transition repeats their proof; this observation only chooses
    a branch of the installer's existing short transaction.
    """
    if request.target not in TARGETS:
        return None
    binding = SetupCredentialInstallation.objects.filter(request_id=request.pk).first()
    if binding is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_install_completed_v1(%s)", [request.pk]
        )
        completed_at = cursor.fetchone()[0]
    return ready_live(binding.readiness_id), completed_at
