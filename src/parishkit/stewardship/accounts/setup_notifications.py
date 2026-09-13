"""Explicit Slack test intake and target-isolated, non-retrying consumption."""

from uuid import UUID, uuid4, uuid5

from django.db import connection, connections
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_notification import ReadinessNotification
from parishkit.stewardship.readiness_notification_process import submit_notification
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .credential_database import _identity, admit_installer_database
from .credential_handoff import PrivateHandoff
from .cryptography import CryptographicError
from .delivery_results import settle_result
from .key_files import file_fingerprint
from .sessions import authenticated_admin, database_now
from .setup_notification_models import SetupSlackDelivery
from .setup_preview import verify_preview
from .setup_secret_models import SetupSealedCredential


def live(row):
    """The SQL guard proves current original login, draft, key and enabled channel."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_setup_slack_live_v1(%s,%s,%s,%s,%s)",
            [
                row.attempt_id,
                row.attempt_version,
                row.credential_id,
                row.credential_version,
                row.fingerprint,
            ],
        )
        return cursor.fetchone() == (True,)


def request_notification(
    request, service, *, preview_token, request_key, acknowledge_unknown=False
):
    """Store one explicit current-preview request; never deliver in an HTTP view."""
    if not isinstance(request_key, UUID) or type(acknowledge_unknown) is not bool:
        raise ValueError("Invalid notification request.")
    with work_transaction():
        preview = verify_preview(request, service, preview_token)
        attempt_id = preview.draft.status.attempt_id
        if not preview.draft.sections["slack"]["enabled"]:
            raise ValueError("Slack is disabled for this setup.")
        rows = SetupSlackDelivery.objects.filter(attempt_id=attempt_id)
        previous = rows.filter(request_key=request_key).first()
        if previous is not None:
            return previous.pk
        if rows.filter(state__in=["queued", "submitting"]).exists():
            raise StaleRecordError("A Slack test is already pending.")
        if not acknowledge_unknown and rows.filter(state="delivery_unknown").exists():
            raise ValueError(
                "A previous test may have arrived; acknowledge another send."
            )
        credential = SetupSealedCredential.objects.only(
            "id", "version", "fingerprint"
        ).get(attempt_id=attempt_id, target="slack", scrubbed_at=None)
        row = SetupSlackDelivery.objects.create(
            id=uuid5(attempt_id, "setup-slack:" + str(request_key)),
            attempt_id=attempt_id,
            attempt_version=preview.draft.status.version,
            request_key=request_key,
            candidate_digest=preview.compiled.candidate.digest,
            credential_id=credential.pk,
            credential_version=credential.version,
            fingerprint=credential.fingerprint,
            actor_id=request.portal_session.principal_id,
        )
        authenticated_admin(request, store=service.store, activity=True)
        return row.pk


def _change(row, state, *, actor_id=None, **values):
    """Keep every state/version/audit transition in the already-owned transaction."""
    require_work_order()
    SetupSlackDelivery.objects.filter(pk=row.pk, version=row.version).update(
        state=state,
        actor_id=actor_id,
        correlation_id=current_correlation(),
        version=F("version") + 1,
        **values,
    )
    row.refresh_from_db()
    return row


def recover_pending():
    """Bounded metadata-only recovery never resubmits an uncertain notification."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        name = cursor.fetchone()[0]
    if name not in {"pk_stewardship_credential_slack", "pk_stewardship_scheduler"}:
        raise PermissionError("Notification recovery requires its isolated owner.")
    _identity(name)
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.stewardship_setup_slack_delivery d "
                "WHERE (d.state='submitting' AND d.deadline_at<=clock_timestamp()) "
                "OR (d.state='queued' AND NOT public.stewardship_setup_slack_live_v1("
                "d.attempt_id,d.attempt_version,d.credential_id,d.credential_version,"
                "d.fingerprint)) ORDER BY d.created_at,d.id LIMIT 100 FOR UPDATE"
            )
            identifiers = [row[0] for row in cursor.fetchall()]
        for row in SetupSlackDelivery.objects.filter(pk__in=identifiers):
            _change(
                row,
                "cancelled" if row.state == "queued" else "delivery_unknown",
                finished_at=database_now(),
            )
        return len(identifiers)


def _begin(worker_id):
    """Commit one irreversible marker before returning any provider-bound input."""
    if connection.in_atomic_block:
        raise StorageInvariantError(
            "Notification submission needs an independent commit."
        )
    admit_installer_database("slack")
    with work_transaction():
        row = (
            SetupSlackDelivery.objects.filter(state="queued")
            .order_by("created_at", "id")
            .first()
        )
        if row is None or not live(row):
            return None
        credential = SetupSealedCredential.objects.only(
            "id", "ciphertext", "settings"
        ).get(pk=row.credential_id)
        _change(
            row,
            "submitting",
            actor_id=worker_id,
            worker_id=worker_id,
            submitted_at=database_now(),
            deadline_at=database_now(),
        )
        return row, credential.ciphertext, credential.settings


def _check(row, check):
    """Loss of original setup authority or service ownership drains the helper."""
    try:
        check()
        admit_installer_database("slack")
        with work_transaction():
            current = SetupSlackDelivery.objects.get(pk=row.pk)
            if (
                current.state != "submitting"
                or current.worker_id != row.worker_id
                or database_now() >= current.deadline_at
                or not live(current)
            ):
                raise PermissionError("Notification ownership has ended.")
    finally:
        connections.close_all()


def run_pending(private, *, check):
    """The existing isolated installer loop consumes one explicit test at a time."""
    if (
        not isinstance(private, PrivateHandoff)
        or private.target != "slack"
        or not callable(check)
    ):
        raise TypeError("An isolated Slack target and owner check are required.")
    check()
    admit_installer_database("slack")
    recover_pending()
    selected = _begin(uuid4())
    if selected is None:
        return False
    row, ciphertext, settings = selected
    outcome = DeliveryOutcome.NOT_SENT
    try:
        value = private.open(row.credential_id, ciphertext)
        if file_fingerprint(value) != row.fingerprint:
            raise CryptographicError("Notification credential differs.")
        notification = ReadinessNotification(row.pk, settings["channel_id"])
    except (CryptographicError, ValueError, KeyError):
        pass  # The helper was never invoked, so local invalid input is unsent.
    else:
        remaining = (row.deadline_at - database_now()).total_seconds()
        connections.close_all()
        outcome = DeliveryOutcome.UNKNOWN
        if remaining > 0:
            try:
                outcome = submit_notification(
                    value,
                    notification,
                    seconds=min(30, remaining),
                    check=lambda: _check(row, check),
                )
            except Exception:
                outcome = DeliveryOutcome.UNKNOWN
    # Fatal ownership/drain errors escape, leaving the marker for timed recovery.
    check()
    admit_installer_database("slack")
    with work_transaction():
        current = SetupSlackDelivery.objects.get(pk=row.pk)
        if current.state == "submitting" and current.worker_id == row.worker_id:
            settle_result(
                outcome,
                deadline=current.deadline_at,
                write=lambda result: _change(
                    current,
                    result.value,
                    actor_id=row.worker_id,
                    finished_at=database_now(),
                ),
            )
    return True
