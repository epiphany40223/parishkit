"""Target-isolated relay of a staged key to one ephemeral live source worker."""

from django.db import connection
from django.db.models import F

from parishkit.stewardship.accounts.credential_database import (
    _identity,
    admit_installer_database,
)
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.accounts.setup_exchange_models import SetupSourceExchange
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .setup_handoff import (
    EphemeralSetupRecipient,
    SealedSetupCredential,
    SetupCredentialRecipient,
    SetupCredentialScope,
    relay_setup_credential,
)


def _live(row):
    """One invoker-owned SQL proof repeats original login, Task and source fences."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_exchange_live_v1(%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                row.attempt_id,
                row.credential_id,
                row.credential_version,
                row.fingerprint,
                row.task_id,
                row.task_fence,
                row.worker_id,
                row.source_fence,
            ],
        )
        if cursor.fetchone() != (True,):
            raise PermissionError("Setup source credential ownership has expired.")


def publish_recipient(recipient):
    """Publish a fresh public recipient only as the actual claimed source worker."""
    if not isinstance(recipient, SetupCredentialRecipient):
        raise TypeError("An ephemeral setup recipient is required.")
    _identity("pk_stewardship_worker")
    scope = recipient.scope
    with work_transaction():
        candidate = (
            SetupSealedCredential.objects.filter(
                pk=scope.request_id, attempt_id=scope.attempt_id, target="parishsoft"
            )
            .only("id", "version", "fingerprint")
            .get()
        )
        existing = SetupSourceExchange.objects.filter(
            task_id=scope.claim.run_id,
            task_fence=scope.claim.fence,
            source_fence=scope.source_fence,
        ).first()
        if existing is not None:
            _live(existing)
            if (
                existing.attempt_id != scope.attempt_id
                or existing.credential_id != scope.request_id
                or existing.worker_id != scope.claim.worker_id
                or bytes(existing.public_key) != recipient.public_key
            ):
                raise StaleRecordError("This source claim already has a recipient.")
            return existing.pk
        row = SetupSourceExchange(
            attempt_id=scope.attempt_id,
            credential_id=scope.request_id,
            credential_version=candidate.version,
            fingerprint=candidate.fingerprint,
            task_id=scope.claim.run_id,
            task_fence=scope.claim.fence,
            worker_id=scope.claim.worker_id,
            source_fence=scope.source_fence,
            public_key=recipient.public_key,
            actor_id=scope.claim.worker_id,
        )
        _live(row)
        row.save(force_insert=True)
        return row.pk


def _recipient(row):
    """Reconstruct immutable public scope without a private key or caller overrides."""
    return SetupCredentialRecipient(
        SetupCredentialScope(
            row.attempt_id,
            row.credential_id,
            TaskClaim(row.task_id, row.task_fence, row.worker_id),
            row.source_fence,
        ),
        bytes(row.public_key),
    )


def relay_pending(private):
    """Reply to one live exchange as the isolated target, without touching files.

    The SQL filter excludes expired original sessions and stale task/source
    fences, so an old waiting recipient cannot starve the current source load.
    The insert/update guards independently repeat the complete live proof.
    """
    if not isinstance(private, PrivateHandoff) or private.target != "parishsoft":
        raise CryptographicError("Only the ParishSoft target can relay setup input.")
    admit_installer_database(private.target)
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.stewardship_setup_source_exchange e "
                "WHERE e.replied_at IS NULL AND e.scrubbed_at IS NULL "
                "AND public.stewardship_setup_exchange_live_v1("
                "e.attempt_id,e.credential_id,e.credential_version,e.fingerprint,"
                "e.task_id,e.task_fence,e.worker_id,e.source_fence) "
                "ORDER BY e.created_at,e.id LIMIT 1"
            )
            found = cursor.fetchone()
        if found is None:
            return False
        row = SetupSourceExchange.objects.get(pk=found[0])
        _live(row)
        candidate = SetupSealedCredential.objects.only("id", "ciphertext").get(
            pk=row.credential_id
        )
        sealed = relay_setup_credential(
            private,
            recipient=_recipient(row),
            ciphertext=candidate.ciphertext,
            fingerprint=row.fingerprint,
        )
        SetupSourceExchange.objects.filter(pk=row.pk, version=row.version).update(
            ciphertext=sealed.ciphertext,
            replied_at=database_now(),
            actor_id=None,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        return True


def receive_credential(recipient):
    """Return decrypted bytes only to the original in-memory worker recipient."""
    if not isinstance(recipient, EphemeralSetupRecipient):
        raise TypeError("The original ephemeral private recipient is required.")
    _identity("pk_stewardship_worker")
    public = recipient.public
    with work_transaction():
        row = SetupSourceExchange.objects.get(
            task_id=public.scope.claim.run_id,
            task_fence=public.scope.claim.fence,
            source_fence=public.scope.source_fence,
        )
        _live(row)
        if _recipient(row) != public:
            raise StaleRecordError("The source credential recipient differs.")
        if row.replied_at is None:
            return None
        result = recipient.open(SealedSetupCredential(row.ciphertext, row.fingerprint))
        _live(row)
        return result
