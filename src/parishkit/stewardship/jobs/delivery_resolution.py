"""Admin intent and current-credential preparation, without SMTP or private keys."""

from uuid import UUID

from parishkit.stewardship.accounts.cryptography import (
    GeneralKeyring,
    TokenPublicKeyring,
)
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_schedule_planning import _planning_scope
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from .delivery_admin import authorize, evidence_note
from .delivery_metadata import PURPOSES
from .delivery_resolution_models import ACTIONS, DeliveryResolution
from .family_dispatch_grants import METADATA_FIELDS
from .family_mail_credentials import seal_current_credentials
from .family_mail_inputs import load_family_mail_source
from .family_mail_rendering import current_render
from .models import TaskRun
from .outbox_models import OutboxMessage
from .outbox_validation import DeliveryIdentity
from .storage import retry_failed


def _identity(message):
    """Construct the existing immutable scope without reading sealed payloads."""
    return DeliveryIdentity(
        scope_id=message.campaign_id,
        **{
            field: getattr(message, field)
            for field in DeliveryIdentity.__dataclass_fields__
            if field != "scope_id"
        },
    )


def _prepare(message, *, general, public, public_origin):
    """Re-read source, template and retained credential references inside the lock."""
    if message is not None and message.purpose == "receipt":
        return _prepare_receipt(message)
    if (
        not isinstance(general, GeneralKeyring)
        or not isinstance(public, TokenPublicKeyring)
        or not isinstance(public_origin, str)
        or not public_origin
    ):
        raise ValueError(
            "Retry preparation requires current public/general keys and origin."
        )
    scope, epoch = _planning_scope(message.campaign_id)
    if scope.runtime.mode != message.mode or (
        message.mode == "testing" and epoch.pk != message.rehearsal_epoch_id
    ):
        raise PermissionError("This delivery belongs to an earlier scope.")
    if message.mode == "production" and scope.campaign.delivery_paused:
        raise PermissionError("Resume campaign delivery before authorizing a retry.")
    family = FamilyCampaign.objects.get(pk=message.family_id)
    source = load_family_mail_source(family)
    if not source.recipients.status.email_deliverable:
        raise PermissionError("No current deliverable recipient is available.")
    occurrence = ScheduleOccurrence.objects.select_related(
        "revision", "definition"
    ).get(pk=message.semantic_key)
    if occurrence.revision_id != occurrence.definition.current_revision_id:
        raise PermissionError("This delivery schedule has been replaced.")
    identity = _identity(message)
    render = current_render(
        identity,
        occurrence,
        scope,
        source,
        public_origin=public_origin,
    )
    sealed = seal_current_credentials(
        identity=identity,
        render=render,
        campaign=scope.campaign,
        family=family,
        general=general,
        public=public,
    )
    # JSON transports UUID metadata as canonical strings; nothing here is logged
    # or sent to a browser. SQL consumes/scrubs the ephemeral input before INSERT.
    return {
        "render": {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in render.fields().items()
        },
        "sealed": {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in sealed.fields().items()
        },
    }


def _prepare_receipt(message):
    """Request a database-owned seed; Web cannot author a receipt delivery body."""
    from .receipt_dispatch import receipt_disposition

    if receipt_disposition(message) is not None:
        raise PermissionError("Receipt retry is not currently admitted.")
    return {"receipt": True}


def resolve_delivery(
    store,
    user_id,
    *,
    message_id,
    command_id,
    expected_version,
    action,
    note,
    duplicate_acknowledged=False,
    general=None,
    public=None,
    public_origin=None,
    preparation_inputs=None,
):
    """Bind one explicit command and all of its domain effects in a short commit.

    The Web service has no outbox UPDATE grant. A closed insert trigger validates
    this intent independently and owns its exact message/occurrence changes.
    Replays compare retained intent before any random re-sealing or retry work.
    """
    if any(not isinstance(value, UUID) for value in (user_id, message_id, command_id)):
        raise ValueError("Resolution requires canonical identities.")
    if (
        type(expected_version) is not int
        or not 1 <= expected_version <= 2**63 - 1
        or type(action) is not str
        or action not in ACTIONS
    ):
        raise ValueError("Invalid resolution version or action.")
    if type(duplicate_acknowledged) is not bool or duplicate_acknowledged != (
        action == "resend"
    ):
        raise ValueError(
            "Resending requires an explicit duplicate-risk acknowledgement."
        )
    if preparation_inputs is not None and (
        not callable(preparation_inputs)
        or any(value is not None for value in (general, public, public_origin))
    ):
        raise ValueError("Use one unambiguous preparation input source.")
    intent = dict(
        actor_id=user_id,
        message_id=message_id,
        expected_version=expected_version,
        action=action,
        evidence_note=evidence_note(note),
        duplicate_acknowledged=duplicate_acknowledged,
    )
    with work_transaction():
        authorize(store, user_id)
        previous = DeliveryResolution.objects.filter(pk=command_id).first()
        if previous is not None:
            if any(
                getattr(previous, field) != value for field, value in intent.items()
            ):
                raise ValueError("Resolution command is already bound.")
            return previous
        message = OutboxMessage.objects.only(*METADATA_FIELDS).get(
            pk=message_id, purpose__in=PURPOSES
        )
        if message.version != expected_version:
            raise StaleRecordError("Delivery changed; review it again.")
        latest = (
            TaskRun.objects.filter(root_id=message.task_id)
            .order_by("-retry_sequence")
            .first()
        )
        preparation, next_task_id = None, None
        retry = action in {"resend", "retry_failed", "retry_unsent"}
        if action != "note" and (latest is None or latest.state != "failed"):
            raise StaleRecordError(
                "Wait for the delivery worker to finish recording its result."
            )
        expected_states = {
            "note": {
                "pending",
                "retry_wait",
                "submitting",
                "delivery_unknown",
                "delivered",
                "permanent_failure",
                "cancelled",
            },
            "accept": {"delivery_unknown"},
            "resend": {"delivery_unknown"},
            "retry_failed": {"permanent_failure"},
            # Includes an attempt definitively not accepted by the provider;
            # delivery_unknown always requires explicit duplicate-risk consent.
            "retry_unsent": {"pending", "retry_wait"},
        }
        if message.state not in expected_states[action]:
            raise StaleRecordError(
                "This action does not match the current delivery state."
            )
        correlation_id = command_id
        if retry:
            inputs = (
                preparation_inputs(message.purpose)
                if preparation_inputs is not None
                else dict(
                    general=general,
                    public=public,
                    public_origin=public_origin,
                )
            )
            preparation = _prepare(
                message,
                **inputs,
            )

            def admit_retry(candidate, status):
                """Retry this immutable root under current Admin authority."""
                authorize(store, user_id)
                return candidate == "explicit_retry" and status.run_id == latest.pk

            next_task_id = retry_failed(
                run_id=latest.pk,
                command_id=command_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                admit=admit_retry,
            ).run_id
        receipt = DeliveryResolution.objects.create(
            id=command_id,
            correlation_id=correlation_id,
            **intent,
            previous_task_id=latest.pk if action != "note" else None,
            retry_task_id=next_task_id,
            preparation=preparation,
        )
        receipt.preparation = None
        return receipt
