"""Exercise delivery SQL independently of the validating service inputs."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.db.models.functions import Now

from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessTokenGeneration,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import (
    SealedSubstitutions,
    substitution_context,
)
from parishkit.stewardship.jobs.storage import change_run, enqueue, retry_failed
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import prepared_tokens
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_outbox_boundaries_postgresql import sealed_inputs  # noqa: F401
from .test_outbox_postgresql import (  # noqa: F401
    change,
    claim,
    inputs,
    permit,
    provider_evidence,
    submit,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "values,error",
    [
        ({"subject": "Header\nInjection"}, "Invalid delivery render"),
        ({"html": " "}, "Invalid delivery render"),
        ({"text": "x" * 1_048_577}, "Invalid delivery render"),
        ({"sender": "not-an-address"}, "Invalid delivery render"),
        ({"intended_recipients": {}}, "Invalid delivery recipients"),
        ({"routed_recipients": []}, "Invalid delivery recipients"),
        ({"intended_recipients": [True]}, "Invalid delivery recipients"),
        (
            {"routed_recipients": ["x@example.org", "X@example.org"]},
            "Invalid delivery recipients",
        ),
        ({"routed_recipients": ["x\n@example.org"]}, "Invalid delivery recipients"),
        ({"template_id": uuid4()}, "Invalid delivery template binding"),
    ],
)
def test_raw_render_guard_rejects_invalid_payloads(inputs, values, error):  # noqa: F811
    """Bypassing RenderInput does not bypass database shape or template checks."""
    first = create_message(**inputs)
    with pytest.raises(IntegrityError, match=error), transaction.atomic():
        OutboxRender.objects.create(
            message_id=first.message_id,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            **(inputs["render"].fields() | values),
        )
    assert OutboxRender.objects.count() == 1


@pytest.mark.parametrize(
    "binding,error",
    [
        ("task", "Invalid delivery task binding"),
        ("scope", "Invalid delivery scope"),
        ("pause", "Invalid delivery pause binding"),
    ],
)
def test_raw_message_binding_guards(inputs, binding, error):  # noqa: F811
    """Fresh ORM inserts reach the binding guard, not an immutable-field update."""
    first = create_message(**inputs)
    message_id = uuid4()
    with pytest.raises(IntegrityError, match=error), transaction.atomic():
        task = enqueue(
            task_type="outbox_delivery",
            domain_request_id=message_id,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=permit,
        )
        values = dict(
            **asdict(replace(inputs["identity"], semantic_key=uuid4())),
            task_id=first.task_id if binding == "task" else task.run_id,
            render_id=first.render_id,
            command_id=uuid4(),
            command_digest="a" * 64,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            action="created",
        )
        if binding == "scope":
            values["scope_id"] = uuid4()
        if binding == "pause":
            values.update(pause_hold_id=uuid4(), pause_version=1)
        OutboxMessage.objects.create(id=message_id, **values)
    assert OutboxMessage.objects.count() == 1


@pytest.mark.parametrize("action", ["hold", "release_hold"])
def test_raw_empty_hold_operation_is_rejected(inputs, action):  # noqa: F811
    """A well-versioned action must actually attach or release a hold."""
    first = create_message(**inputs)
    with (
        pytest.raises(IntegrityError, match="Invalid delivery hold operation"),
        transaction.atomic(),
    ):
        OutboxMessage.objects.filter(pk=first.message_id).update(
            action=action,
            command_id=uuid4(),
            version=F("version") + 1,
        )


@pytest.mark.parametrize(
    "action,values,error",
    [
        (
            "retry_unaccepted",
            {"state": "retry_wait", "not_before": Now() - timedelta(seconds=1)},
            "future schedule",
        ),
        (
            "accept",
            {"state": "delivered", "finished_at": Now() - timedelta(seconds=1)},
            "database clock",
        ),
    ],
)
def test_raw_outcome_must_obey_database_clock(inputs, action, values, error):  # noqa: F811
    """Python timing validation is not the only control over retry/completion."""
    first = submit(create_message(**inputs))
    with pytest.raises(IntegrityError, match=error), transaction.atomic():
        OutboxMessage.objects.filter(pk=first.message_id).update(
            action=action,
            command_id=uuid4(),
            version=F("version") + 1,
            actor_id=first.worker_id,
            **asdict(provider_evidence()),
            **values,
        )


@pytest.mark.parametrize("wrong", [None, "generation", "epoch"])
def test_production_credential_binding_uses_actual_family_generation(
    sealed_inputs,  # noqa: F811
    family_service,  # noqa: F811
    wrong,
):
    """Synthetic sealed Production input must reference a real Family token."""
    options, _ = sealed_inputs
    generation_id = prepared_tokens(family_service.campaign, uuid4())
    generation = FamilyAccessTokenGeneration.objects.get(pk=generation_id)
    identity = replace(
        options["identity"],
        mode="production",
        routing="production",
        credential_namespace="production",
        rehearsal_epoch_id=None,
    )
    sealed = SealedSubstitutions(
        family_service.rings.public.encrypt(
            b"synthetic-token",
            context=substitution_context(identity, options["render"]),
        ),
        token_generation_id=uuid4() if wrong == "generation" else generation_id,
        credential_epoch_id=uuid4()
        if wrong == "epoch"
        else generation.credential_epoch,
    )
    values = options | {"identity": identity, "sealed": sealed}
    if wrong is None:
        assert create_message(**values).identity.credential_namespace == "production"
    else:
        with pytest.raises(IntegrityError, match="Invalid delivery credential binding"):
            create_message(**values)
        assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize("child_claim", [False, True])
def test_transition_admission_receives_exact_frozen_proof_and_locked_task(
    inputs,  # noqa: F811
    child_claim,
):
    """A transition and its replay pass proposed proof after acquiring task locks."""
    first = create_message(**inputs)
    if child_claim:
        failed = change(
            submit(first), Action.FAIL_UNACCEPTED, evidence=provider_evidence()
        )
        original = TaskRun.objects.get(pk=failed.task_id)
        change_run(
            run_id=original.pk,
            expected_version=original.version,
            action="permanent_failure",
            actor_id=original.worker_id,
            fence=original.fence,
            correlation_id=uuid4(),
            admit=permit,
        )
        with work_transaction():
            retry = retry_failed(
                run_id=original.pk,
                command_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=permit,
            )
            first = change(failed, Action.RETRY_FAILED, render=inputs["render"])
        task = claim(first, run_id=retry.run_id)
    else:
        task = claim(first)
    calls = []
    evidence = provider_evidence()

    def admitted(action, identity, status, proposal):
        """Observe both the old status and proposed fields without private output."""
        calls.append((action, proposal))
        assert proposal.evidence == evidence
        assert proposal.run_id == task.run_id and proposal.task_fence == task.fence
        assert proposal.provider_seconds == 30 and proposal.actor_id == task.worker_id
        assert evidence.evidence_note not in repr(proposal)
        with pytest.raises(FrozenInstanceError):
            proposal.provider_seconds = 60
        # Capture the ORM query stream below to verify locking precedes admission;
        # PostgreSQL row locks held by one's own transaction aren't all pg_locks rows.
        assert any(
            "FOR UPDATE" in sql and str(task.run_id) in str(params)
            for sql, params in queries
        )
        return True

    queries = []

    def record(execute, sql, params, many, context):
        """Preserve exact SQL order without substituting for PostgreSQL execution."""
        queries.append((sql, params))
        return execute(sql, params, many, context)

    options = dict(
        command_id=uuid4(),
        actor_id=task.worker_id,
        run_id=task.run_id,
        task_fence=task.fence,
        provider_seconds=30,
        evidence=evidence,
        admit=admitted,
    )
    for replay in (False, True):
        queries.clear()
        with connection.execute_wrapper(record):
            status = change(first, Action.SUBMIT, **options)
        assert status.state == "submitting"
        assert calls[-1][0] == ("transition_replay" if replay else Action.SUBMIT)
    assert calls[0][1] == calls[1][1]
    with pytest.raises(PermissionError):
        change(first, Action.SUBMIT, **(options | {"admit": lambda *args: False}))
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def test_delivery_claim_cannot_lock_an_unrelated_root(inputs):  # noqa: F811
    """A foreign claim is rejected before callback admission."""
    first = create_message(**inputs)
    other = create_message(
        **(inputs | {"identity": replace(inputs["identity"], semantic_key=uuid4())})
    )
    foreign = claim(other)
    with pytest.raises(StorageInvariantError, match="does not belong"):
        change(
            first,
            Action.SUBMIT,
            run_id=foreign.run_id,
            task_fence=foreign.fence,
            actor_id=foreign.worker_id,
            provider_seconds=30,
        )
