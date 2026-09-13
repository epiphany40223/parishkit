"""Closed campaign-test execution and recovery cannot create an automatic resend."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts import campaign_mail_tasks as tasks


@pytest.mark.parametrize(
    "state,expected",
    [
        ("accepted", "recovery_complete"),
        ("cancelled", "recovery_cancel"),
        ("not_sent", "recovery_fail"),
        ("delivery_unknown", "recovery_fail"),
        ("queued", "recovery_fail"),
        ("submitting", None),
    ],
)
def test_recovery_uses_outcome_but_never_retries(monkeypatch, state, expected):
    """Only the scheduler's drained journal recovery can resolve submitting work."""
    monkeypatch.setattr(tasks, "bound_delivery", lambda _: SimpleNamespace(state=state))
    result = tasks.recovery_plan(SimpleNamespace(state="abandoned"))
    assert (result.action if result else None) == expected
    with pytest.raises(PermissionError):
        tasks.recovery_plan(SimpleNamespace(state="running"))


@pytest.mark.parametrize("state", ["queued", "submitting", "accepted", "cancelled"])
@pytest.mark.parametrize("action", ["hint", "claim"])
def test_claim_cannot_reenter_a_submission(monkeypatch, state, action):
    """Only an unsent journal, or safe cancellation settlement, can be claimed."""
    monkeypatch.setattr(tasks, "bound_delivery", lambda _: SimpleNamespace(state=state))
    monkeypatch.setattr(tasks, "mail_authority", lambda _: None)
    monkeypatch.setattr(tasks, "live", lambda _: True)
    assert tasks.admit_task(action, object(), store=object()) is (
        state in {"queued", "cancelled"}
    )


def test_scheduler_cannot_execute_and_worker_needs_installed_path():
    """Private execution requires an installed consumer, not scheduler metadata."""
    with pytest.raises(TypeError):
        tasks.campaign_mail_handler(object())
    handler = tasks.campaign_mail_handler(object(), scheduler=True)
    with pytest.raises(PermissionError):
        handler.execute(object())
