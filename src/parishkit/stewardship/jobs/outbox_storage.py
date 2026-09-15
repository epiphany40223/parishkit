"""Internal delivery transactions; not a worker, provider adapter, or public API.

Owning services must supply live admission for every operation and replay while
the shared work/task/outbox locks are held. No external work belongs inside the
callback. Restricted runtime roles have no generic write port for these tables;
later compiled dispatch and cleanup owners supply their real permission proof.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

from django.db.models.functions import Now

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .delivery_states import (
    TERMINAL_DELIVERY_STATES,
    DeliveryAction,
    DeliveryState,
    delivery_target,
)
from .models import TaskRun
from .outbox_models import DeliveryPauseHold, OutboxEvent, OutboxMessage, OutboxRender
from .outbox_validation import (
    DeliveryEvidence,
    DeliveryIdentity,
    RenderInput,
    SealedSubstitutions,
    identifier,
)
from .storage import enqueue


@dataclass(frozen=True)
class DeliveryStatus:
    """A callback/status view deliberately omitting content and credentials."""

    message_id: UUID
    task_id: UUID
    identity: DeliveryIdentity
    state: DeliveryState
    version: int
    attempt: int
    render_id: UUID
    run_id: UUID | None
    task_fence: int | None
    worker_id: UUID | None
    pause_hold_id: UUID | None


@dataclass(frozen=True)
class DeliveryCommand:
    """Exact proposed transition proof; never includes content or ciphertext.

    The admission owner receives this fourth argument for transitions and their
    replays. The private reconciliation note is omitted from representations.
    Replay metadata describes caller input, not a replacement of retained data;
    dispatch must inspect the retained envelope/render after an accepted replay.
    """

    action: DeliveryAction
    command_id: UUID
    expected_version: int
    actor_id: UUID
    evidence: DeliveryEvidence = field(repr=False)
    run_id: UUID | None
    task_fence: int | None
    provider_seconds: int | None
    retry_seconds: int | None
    render_digest: str | None
    configuration_id: UUID | None
    template_id: UUID | None
    sealed_key_id: str | None
    token_generation_id: UUID | None
    credential_epoch_id: UUID | None


def _status(message):
    """Detach operation identity from the mutable ORM row."""
    return DeliveryStatus(
        message.pk,
        message.task_id,
        DeliveryIdentity(
            **{
                name: getattr(message, name)
                for name in DeliveryIdentity.__dataclass_fields__
            }
        ),
        DeliveryState(message.state),
        message.version,
        message.attempt,
        message.render_id,
        message.run_id,
        message.task_fence,
        message.worker_id,
        message.pause_hold_id,
    )


def _admit(callback, action, identity, status, proposal=None):
    """A truthy placeholder is not owning-service admission, including on replay."""
    if not callable(callback):
        raise TypeError("Delivery admission callback is required.")
    args = (action, identity, status)
    if proposal is not None:
        args += (proposal,)
    if callback(*args) is not True:
        raise PermissionError("Delivery operation is not admitted.")


def _sealed_fields(identity, sealed):
    """No credential-bearing delivery may silently fall back across namespaces."""
    if identity.credential_namespace == "none":
        if sealed is not None:
            raise ValueError("Unexpected delivery credentials.")
        return dict(
            sealed_substitutions=None,
            sealed_key_id=None,
            token_generation_id=None,
            credential_epoch_id=None,
        )
    if not isinstance(sealed, SealedSubstitutions):
        raise ValueError("Sealed delivery credentials are required.")
    production = identity.credential_namespace == "production"
    if (sealed.token_generation_id is not None) != production or (
        sealed.credential_epoch_id is not None
    ) != production:
        raise ValueError("Delivery credential namespace does not match.")
    return sealed.fields()


def _operation_ids(command_id, actor_id, correlation_id):
    """Validate attributed journal identity before any SQL or callback."""
    for value in (command_id, actor_id, correlation_id):
        identifier(value)


def _command_digest(*values):
    """Fingerprint validated command options without retaining sealed payloads."""
    converted = []
    for value in values:
        if isinstance(value, RenderInput):
            value = value.fields()
        elif isinstance(value, SealedSubstitutions):
            # SealedBox is randomized: a lost-response retry may reseal the same
            # credential. Bind its stable identity, never the random ciphertext.
            # Replays keep the first envelope; admission still verifies the owner.
            value = value.fields()
            value.pop("sealed_substitutions")
            value.pop("sealed_key_id")
        elif isinstance(value, DeliveryIdentity | DeliveryEvidence):
            value = asdict(value)
        converted.append(value)
    return hashlib.sha256(
        json.dumps(
            converted, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def create_message(
    *, identity, render, actor_id, correlation_id, command_id, admit, sealed=None
):
    """Atomically allocate one message, root task, first render and history.

    Concurrent producers serialize with lifecycle work. A repeated semantic
    identity must also match the original render and initiator; it does not
    rewrite an earlier delivery or silently change its recipient selection.
    """
    _operation_ids(command_id, actor_id, correlation_id)
    if not isinstance(identity, DeliveryIdentity) or not isinstance(
        render, RenderInput
    ):
        raise TypeError("Typed delivery identity and render are required.")
    credentials = _sealed_fields(identity, sealed)
    content = render.fields()
    digest = _command_digest("created", identity, render, sealed)
    with correlation(correlation_id), work_transaction():
        existing = OutboxMessage.objects.filter(
            scope_id=identity.scope_id,
            mode=identity.mode,
            semantic_key=identity.semantic_key,
        ).first()
        if existing is not None:
            TaskRun.objects.select_for_update().get(pk=existing.task_id)
            existing = OutboxMessage.objects.select_for_update().get(pk=existing.pk)
            first = OutboxEvent.objects.select_related("render").get(
                message=existing, version=1
            )
            if (
                _status(existing).identity != identity
                or first.actor_id != actor_id
                or first.render.payload_digest != content["payload_digest"]
                or first.render.configuration_id != render.configuration_id
                or first.render.template_id != render.template_id
                or first.command_digest != digest
            ):
                raise ValueError("Delivery semantic identity is already bound.")
            _admit(admit, "create_replay", identity, _status(existing))
            return _status(existing)
        _admit(admit, "create", identity, None)
        message_id, render_id = uuid4(), uuid4()
        task = enqueue(
            task_type="outbox_delivery",
            domain_request_id=message_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=message_id,
            admit=lambda action, status: admit("create_task", identity, None),
        )
        message = OutboxMessage.objects.create(
            id=message_id,
            **asdict(identity),
            task_id=task.run_id,
            render_id=render_id,
            command_id=command_id,
            command_digest=digest,
            action="created",
            actor_id=actor_id,
            correlation_id=correlation_id,
            **credentials,
        )
        OutboxRender.objects.create(
            id=render_id,
            message=message,
            actor_id=actor_id,
            correlation_id=correlation_id,
            **content,
        )
        return _status(message)


def change_message(
    *,
    message_id,
    action,
    command_id,
    expected_version,
    actor_id,
    correlation_id,
    admit,
    evidence=None,
    run_id=None,
    task_fence=None,
    provider_seconds=None,
    retry_seconds=None,
    render=None,
    sealed=None,
):
    """Apply a numbered attempt or evidence-backed resolution under owning locks.

    Admission must validate provider evidence, current mode/epoch, eligibility,
    holds and authorization using its fourth argument, a frozen DeliveryCommand
    describing the exact proposed proof/options. SUBMIT pins its proposed task
    before admission, including replay. An elapsed provider deadline cannot
    establish acceptance/nonacceptance. Explicit failed retry requires the owning
    caller to enter work_transaction before allocating its linked TaskRun retry
    and invoking this function in that same ordered transaction.
    """
    _operation_ids(command_id, actor_id, correlation_id)
    identifier(message_id)
    if not isinstance(action, DeliveryAction):
        raise TypeError("A typed delivery action is required.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("Invalid delivery version.")
    evidence = DeliveryEvidence() if evidence is None else evidence
    if not isinstance(evidence, DeliveryEvidence):
        raise TypeError("Typed delivery evidence is required.")
    _validate_options(
        action, run_id, task_fence, provider_seconds, retry_seconds, render, sealed
    )
    digest = _command_digest(
        action,
        expected_version,
        evidence,
        run_id,
        task_fence,
        provider_seconds,
        retry_seconds,
        render,
        sealed,
    )
    credentials = sealed.fields() if sealed is not None else {}
    proposal = DeliveryCommand(
        action=action,
        command_id=command_id,
        expected_version=expected_version,
        actor_id=actor_id,
        evidence=evidence,
        run_id=run_id,
        task_fence=task_fence,
        provider_seconds=provider_seconds,
        retry_seconds=retry_seconds,
        render_digest=render.fields()["payload_digest"] if render is not None else None,
        configuration_id=render.configuration_id if render is not None else None,
        template_id=render.template_id if render is not None else None,
        sealed_key_id=credentials.get("sealed_key_id"),
        token_generation_id=credentials.get("token_generation_id"),
        credential_epoch_id=credentials.get("credential_epoch_id"),
    )
    with correlation(correlation_id), work_transaction():
        initial = OutboxMessage.objects.get(pk=message_id)
        TaskRun.objects.select_for_update().get(pk=initial.task_id)
        if action is DeliveryAction.SUBMIT and not (
            TaskRun.objects.select_for_update()
            .filter(pk=run_id, root_id=initial.task_id)
            .first()
        ):
            raise StorageInvariantError(
                "Delivery claim does not belong to this message."
            )
        message = OutboxMessage.objects.select_for_update().get(pk=message_id)
        status = _status(message)
        previous = OutboxEvent.objects.filter(
            message=message, command_id=command_id
        ).first()
        if previous is not None:
            if (
                previous.action != action.value
                or previous.actor_id != actor_id
                or previous.command_digest != digest
                or any(
                    getattr(previous, name) != value
                    for name, value in asdict(evidence).items()
                )
            ):
                raise ValueError("Delivery command is already bound.")
            _admit(admit, "transition_replay", status.identity, status, proposal)
            return status
        if message.version != expected_version:
            raise StaleRecordError("Delivery version changed.")
        target = delivery_target(status.state, action)
        _admit(admit, action, status.identity, status, proposal)
        values = dict(
            state=target.value,
            action=action.value,
            command_id=command_id,
            command_digest=digest,
            actor_id=actor_id,
            correlation_id=correlation_id,
            version=message.version + 1,
            **asdict(evidence),
        )
        if action is DeliveryAction.SUBMIT:
            values.update(
                attempt=message.attempt + 1,
                run_id=run_id,
                task_fence=task_fence,
                worker_id=actor_id,
                submitted_at=Now(),
                provider_deadline=Now() + timedelta(seconds=provider_seconds),
            )
        if retry_seconds is not None:
            values["not_before"] = Now() + timedelta(seconds=retry_seconds)
        if action is DeliveryAction.RETRY_FAILED:
            values.update(finished_at=None, **_sealed_fields(status.identity, sealed))
            rendering = OutboxRender.objects.create(
                message=message,
                actor_id=actor_id,
                correlation_id=correlation_id,
                **render.fields(),
            )
            values["render_id"] = rendering.pk
        if target in TERMINAL_DELIVERY_STATES:
            values.update(
                finished_at=Now(),
                sealed_substitutions=None,
                sealed_key_id=None,
                pause_hold_id=None,
            )
        OutboxMessage.objects.filter(pk=message_id).update(**values)
        message.refresh_from_db()
        return _status(message)


def _validate_options(
    action, run_id, fence, provider_seconds, retry_seconds, render, sealed
):
    """Reject ignored options so callers cannot mistake them for enforced proof."""
    if action is DeliveryAction.SUBMIT:
        identifier(run_id)
        if (
            type(fence) is not int
            or fence < 1
            or type(provider_seconds) is not int
            or not 1 <= provider_seconds <= 300
        ):
            raise ValueError("Invalid delivery attempt bounds.")
    elif any(value is not None for value in (run_id, fence, provider_seconds)):
        raise ValueError("Unexpected delivery attempt options.")
    if action in (DeliveryAction.RETRY_UNACCEPTED, DeliveryAction.RETRY_IDEMPOTENT):
        if type(retry_seconds) is not int or not 1 <= retry_seconds <= 86400:
            raise ValueError("Invalid delivery retry bounds.")
    elif retry_seconds is not None:
        raise ValueError("Unexpected delivery retry schedule.")
    if action is DeliveryAction.RETRY_FAILED:
        require_work_order()
        if not isinstance(render, RenderInput):
            raise StorageInvariantError(
                "Failed delivery retry requires a fresh render."
            )
        if sealed is not None and not isinstance(sealed, SealedSubstitutions):
            raise TypeError("Typed sealed delivery credentials are required.")
    elif render is not None or sealed is not None:
        raise ValueError("Unexpected delivery preparation.")


def prepare_message(
    *,
    message_id,
    expected_version,
    command_id,
    actor_id,
    correlation_id,
    render,
    sealed=None,
    admit,
):
    """Select a fresh immutable render for unsent work after current owner checks."""
    if not isinstance(render, RenderInput):
        raise TypeError("A typed delivery render is required.")

    def prepare(message, previous):
        """Compare replay with its own historical render, not a later selection."""
        content = render.fields()
        if previous is not None:
            recorded = OutboxRender.objects.get(pk=previous.render_id)
            if (
                recorded.payload_digest != content["payload_digest"]
                or recorded.configuration_id != render.configuration_id
                or recorded.template_id != render.template_id
            ):
                raise ValueError("Delivery command is already bound.")
            return {}
        credentials = _sealed_fields(_status(message).identity, sealed)
        rendering = OutboxRender.objects.create(
            message=message, actor_id=actor_id, correlation_id=correlation_id, **content
        )
        return dict(render_id=rendering.pk, **credentials)

    return _prepare_or_hold(
        message_id=message_id,
        expected_version=expected_version,
        command_id=command_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        action="prepared",
        digest=_command_digest("prepared", expected_version, render, sealed),
        admit=admit,
        prepare=prepare,
    )


def hold_message(
    *,
    message_id,
    expected_version,
    command_id,
    actor_id,
    correlation_id,
    pause_version,
    admit,
):
    """Attach one campaign pause version without manufacturing a delivery outcome."""
    if type(pause_version) is not int or pause_version < 1:
        raise ValueError("Invalid delivery pause version.")

    def prepare(message, previous):
        """Reuse a campaign hold across both scheduled mail and direct receipts."""
        if message.routing != "production" or message.campaign_id is None:
            raise ValueError("Only Production campaign mail can be held.")
        if previous is not None:
            # The immutable command reason binds this version even after resume.
            if previous.reason != f"pause_{pause_version}":
                raise ValueError("Delivery command is already bound.")
            return {}
        if message.pause_version is not None and pause_version <= message.pause_version:
            raise StaleRecordError("Delivery pause version is not newer.")
        hold, _ = DeliveryPauseHold.objects.get_or_create(
            campaign_id=message.campaign_id,
            pause_version=pause_version,
            defaults=dict(actor_id=actor_id, correlation_id=correlation_id),
        )
        return dict(
            pause_hold_id=hold.pk,
            pause_version=pause_version,
            reason=f"pause_{pause_version}",
        )

    return _prepare_or_hold(
        message_id=message_id,
        expected_version=expected_version,
        command_id=command_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        action="hold",
        digest=_command_digest("hold", expected_version, pause_version),
        admit=admit,
        prepare=prepare,
    )


def release_message_hold(
    *, message_id, expected_version, command_id, actor_id, correlation_id, admit
):
    """Release only after the owning resume plan has resolved all related work."""
    return _prepare_or_hold(
        message_id=message_id,
        expected_version=expected_version,
        command_id=command_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        action="release_hold",
        digest=_command_digest("release_hold", expected_version),
        admit=admit,
        prepare=lambda message, previous: dict(pause_hold_id=None),
    )


def _prepare_or_hold(
    *,
    message_id,
    expected_version,
    command_id,
    actor_id,
    correlation_id,
    action,
    digest,
    admit,
    prepare,
):
    """Share ordered locking and journal replay for orthogonal unsent operations."""
    _operation_ids(command_id, actor_id, correlation_id)
    identifier(message_id)
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("Invalid delivery version.")
    with correlation(correlation_id), work_transaction():
        initial = OutboxMessage.objects.get(pk=message_id)
        TaskRun.objects.select_for_update().get(pk=initial.task_id)
        message = OutboxMessage.objects.select_for_update().get(pk=message_id)
        status = _status(message)
        previous = OutboxEvent.objects.filter(
            message=message, command_id=command_id
        ).first()
        if previous is not None:
            if (
                previous.action != action
                or previous.actor_id != actor_id
                or previous.command_digest != digest
            ):
                raise ValueError("Delivery command is already bound.")
            _admit(admit, action + "_replay", status.identity, status)
            prepare(message, previous)
            return status
        if message.version != expected_version:
            raise StaleRecordError("Delivery version changed.")
        if status.state not in (DeliveryState.PENDING, DeliveryState.RETRY_WAIT):
            raise StorageInvariantError("Only unsent delivery can be prepared or held.")
        _admit(admit, action, status.identity, status)
        values = prepare(message, None)
        OutboxMessage.objects.filter(pk=message_id).update(
            **(
                dict(
                    action=action,
                    command_id=command_id,
                    command_digest=digest,
                    version=message.version + 1,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    **asdict(DeliveryEvidence()),
                )
                | values
            )
        )
        message.refresh_from_db()
        return _status(message)
