"""Persist public recipient metadata, never a mail worker's ephemeral private key."""

from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .credential_database import _identity, admit_installer_database
from .credential_handoff import PrivateHandoff
from .cryptography import CryptographicError
from .sessions import database_now
from .setup_delivery_models import SetupMailDelivery
from .setup_mail_exchange_models import SetupMailExchange
from .setup_mail_handoff import (
    EphemeralMailRecipient,
    MailCredentialRecipient,
    MailCredentialScope,
    SealedMailCredential,
    relay_mail_credential,
)
from .setup_secret_models import SetupSealedCredential


def _live(row):
    """Repeat the original setup, journal revision and exact live Task proof."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_mail_exchange_live_v1(%s,%s,%s,%s)",
            [row.delivery_id, row.run_id, row.task_fence, row.worker_id],
        )
        if cursor.fetchone() != (True,):
            raise PermissionError("Setup mail credential ownership has expired.")


def _recipient(row):
    """Use immutable journal bindings instead of arbitrary caller credential fields."""
    delivery = SetupMailDelivery.objects.only(
        "id", "credential_id", "credential_version"
    ).get(pk=row.delivery_id)
    return MailCredentialRecipient(
        MailCredentialScope(
            delivery.pk,
            delivery.credential_id,
            delivery.credential_version,
            TaskClaim(row.run_id, row.task_fence, row.worker_id),
        ),
        bytes(row.public_key),
    )


def publish_recipient(recipient):
    """Only the claimed mail worker may publish one fresh recipient for its run."""
    if not isinstance(recipient, MailCredentialRecipient):
        raise TypeError("A typed ephemeral mail recipient is required.")
    _identity("pk_stewardship_mail_dispatch")
    scope = recipient.scope
    with work_transaction():
        row = SetupMailExchange.objects.filter(
            run_id=scope.claim.run_id, task_fence=scope.claim.fence
        ).first()
        if row is None:
            row = SetupMailExchange(
                delivery_id=scope.delivery_id,
                run_id=scope.claim.run_id,
                task_fence=scope.claim.fence,
                worker_id=scope.claim.worker_id,
                public_key=recipient.public_key,
                actor_id=scope.claim.worker_id,
            )
            _live(row)
            if _recipient(row) != recipient:
                raise StaleRecordError("The mail relay credential revision differs.")
            row.save(force_insert=True)
        else:
            _live(row)
            if _recipient(row) != recipient:
                raise StaleRecordError("This mail claim already has another recipient.")
        return row.pk


def relay_pending(private):
    """Reply only as the Workspace installer, without installing a working key."""
    if not isinstance(private, PrivateHandoff) or private.target != "google_workspace":
        raise CryptographicError("Only Workspace's target may relay setup mail input.")
    admit_installer_database(private.target)
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.stewardship_setup_mail_exchange exchange "
                "WHERE exchange.replied_at IS NULL AND exchange.scrubbed_at IS NULL "
                "AND public.stewardship_setup_mail_exchange_live_v1("
                "exchange.delivery_id,exchange.run_id,"
                "exchange.task_fence,exchange.worker_id) "
                "ORDER BY exchange.created_at,exchange.id LIMIT 1"
            )
            selected = cursor.fetchone()
        if selected is None:
            return False
        row = SetupMailExchange.objects.get(pk=selected[0])
        _live(row)
        recipient = _recipient(row)
        candidate = SetupSealedCredential.objects.only(
            "id", "ciphertext", "fingerprint"
        ).get(pk=recipient.scope.credential_id)
        sealed = relay_mail_credential(
            private,
            recipient=recipient,
            ciphertext=candidate.ciphertext,
            fingerprint=candidate.fingerprint,
        )
        SetupMailExchange.objects.filter(pk=row.pk, version=row.version).update(
            ciphertext=sealed.ciphertext,
            replied_at=database_now(),
            actor_id=None,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        return True


def receive_credential(recipient):
    """Only the original in-memory private recipient can open the reply envelope."""
    if not isinstance(recipient, EphemeralMailRecipient):
        raise TypeError("The original ephemeral private mail recipient is required.")
    _identity("pk_stewardship_mail_dispatch")
    public = recipient.public
    with work_transaction():
        row = SetupMailExchange.objects.get(
            run_id=public.scope.claim.run_id, task_fence=public.scope.claim.fence
        )
        _live(row)
        if _recipient(row) != public:
            raise StaleRecordError("The mail credential recipient differs.")
        if row.replied_at is None:
            return None
        fingerprint = SetupMailDelivery.objects.values_list(
            "fingerprint", flat=True
        ).get(pk=row.delivery_id)
        result = recipient.open(SealedMailCredential(row.ciphertext, fingerprint))
        _live(row)
        return result
