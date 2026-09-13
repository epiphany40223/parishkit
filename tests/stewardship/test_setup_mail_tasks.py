"""Bounded relay waiting and closed scheduler execution, without provider IO."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import setup_mail_tasks as tasks
from parishkit.stewardship.accounts.setup_mail_handoff import WorkspaceCandidate
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.storage import StorageInvariantError


def invocation(monkeypatch=None):
    """A synthetic execution carries only typed original journal/Task identifiers."""
    execution = SimpleNamespace(
        claim=TaskClaim(uuid4(), 1, uuid4()),
        check=Mock(),
        control=SimpleNamespace(finished=Mock(), active=True),
        effect=nullcontext,
    )
    row = SimpleNamespace(
        pk=uuid4(), credential_id=uuid4(), credential_version=7, state="queued"
    )
    if monkeypatch is not None:
        monkeypatch.setattr(tasks, "lock_task_claim", Mock())
        monkeypatch.setattr(tasks, "_status", Mock())
        monkeypatch.setattr(tasks, "bound_delivery", Mock(return_value=row))
        monkeypatch.setattr(tasks, "live", Mock(return_value=True))
    return execution, row


def test_bounded_wait_uses_exact_ephemeral_recipient_and_closes_idle_sql(monkeypatch):
    """One public recipient is reused while waiting, never recreated or persisted."""
    execution, row = invocation(monkeypatch)
    candidate = WorkspaceCandidate(b"synthetic")
    publish, close = Mock(), Mock()
    receive = Mock(side_effect=[None, candidate])
    monkeypatch.setattr(tasks, "publish_recipient", publish)
    monkeypatch.setattr(tasks, "receive_credential", receive)
    monkeypatch.setattr(tasks.connections, "close_all", close)
    assert tasks._receive(execution, row) is candidate
    public = publish.call_args.args[0]
    assert public.scope.claim == execution.claim
    assert public.scope.delivery_id == row.pk
    assert public.scope.credential_id == row.credential_id
    assert public.scope.credential_version == 7
    assert receive.call_args_list[0].args[0] is receive.call_args_list[1].args[0]
    assert receive.call_args.args[0].public == public
    close.assert_called_once()
    execution.control.finished.wait.assert_called_once_with(1)


def test_target_timeout_stops_without_generating_another_exchange(monkeypatch):
    """The original two-minute relay wait cannot be extended by repeated polling."""
    execution, row = invocation(monkeypatch)
    publish = Mock()
    monkeypatch.setattr(tasks, "monotonic", Mock(side_effect=[0, 0, 121]))
    monkeypatch.setattr(tasks, "publish_recipient", publish)
    monkeypatch.setattr(tasks, "receive_credential", Mock(return_value=None))
    monkeypatch.setattr(tasks.connections, "close_all", Mock())
    with pytest.raises(TimeoutError):
        tasks._receive(execution, row)
    publish.assert_called_once()
    execution.control.finished.wait.assert_called_once_with(1)


def test_unmaintained_worker_and_scheduler_cannot_deliver():
    """A manually invoked callback is not a maintained, admitted mail execution."""
    execution, _ = invocation()
    execution.control.active = False
    with pytest.raises(StorageInvariantError):
        tasks._execute(execution)
    with pytest.raises(PermissionError):
        tasks.setup_mail_handler(scheduler=True).execute(execution)
