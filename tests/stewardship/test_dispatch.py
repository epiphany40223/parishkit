"""The internal dispatcher never accepts dynamic handlers or loose identities."""

from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.dispatch import (
    Handler,
    RecoveryPlan,
    WorkQueue,
    claim_hint,
    recover_hint,
)


@pytest.mark.parametrize(
    "queue,admit,execute,recover",
    [
        ("general", lambda *args: True, lambda *args: None, None),
        (WorkQueue.GENERAL, True, lambda *args: None, None),
        (WorkQueue.GENERAL, lambda *args: True, "module.callable", None),
        (WorkQueue.GENERAL, lambda *args: True, lambda *args: None, "reconcile"),
    ],
)
def test_handler_rejects_runtime_strings_and_incomplete_callbacks(
    queue, admit, execute, recover
):
    """A broker payload cannot supply import names or manufacture authority."""
    with pytest.raises(ValueError):
        Handler(queue, admit, execute, recover)


@pytest.mark.parametrize(
    "action,delay",
    [
        ("complete", None),
        ("recovery_retry", None),
        ("recovery_retry", True),
        ("recovery_retry", 0),
        ("recovery_retry", 86401),
        ("recovery_complete", 1),
    ],
)
def test_recovery_disposition_is_strict_and_bounded(action, delay):
    """Invalid recovery plans fail before any durable transition or provider work."""
    with pytest.raises(ValueError):
        RecoveryPlan(action, delay)


@pytest.mark.parametrize("function", [claim_hint, recover_hint])
@pytest.mark.parametrize(
    "field,value", [("run_id", "bad"), ("worker_id", None), ("queue", "general")]
)
def test_hint_boundary_rejects_uncanonical_identity_before_query(
    function, field, value
):
    """The transport adapter must validate its single opaque UUID and service role."""
    arguments = dict(
        run_id=uuid4(), worker_id=uuid4(), queue=WorkQueue.GENERAL, handlers={}
    )
    arguments[field] = value
    with pytest.raises(ValueError):
        function(**arguments)
