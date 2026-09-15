"""Compiled boundary execution and task recovery use real journals and leases."""

from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns import boundaries, boundary_tasks
from parishkit.stewardship.campaigns.boundary_production import (
    TASK_TYPE,
    produce_boundaries,
)
from parishkit.stewardship.campaigns.boundary_tasks import (
    admit_boundary,
    boundary_handler,
)
from parishkit.stewardship.campaigns.lifecycle import Action, portal_admitted
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignBoundaryOccurrence,
    CampaignTransition,
)
from parishkit.stewardship.campaigns.runtime import campaign_facts
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import (
    Execution,
    claim_hint,
    execute_hint,
    recover_hint,
)
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.runtime_background import bind_authority

from .campaign_builders import campaign_clock, command, draft_campaign
from .test_boundary_production_postgresql import scheduled  # noqa: F401
from .test_taskrun_postgresql import act as task_act
from .test_taskrun_postgresql import expire

pytestmark = pytest.mark.django_db(transaction=True)


def run(item, *, recover=False, store=None):
    """Use the real dispatcher, closed handler and maintained worker lifetime."""
    operation = recover_hint if recover else execute_hint
    handlers = {TASK_TYPE: boundary_handler()}
    if store is not None:
        handlers = bind_authority(handlers, store)
    return operation(
        item.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers=handlers,
    )


def test_close_first_hint_applies_both_boundaries_and_later_start_only_acknowledges(
    scheduled,  # noqa: F811
):
    """No externally visible overdue active gap or duplicate lifecycle action."""
    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            start, close = produce_boundaries(guard)
        assert run(close)
        scheduled.refresh_from_db()
        assert scheduled.state == "closed"
        assert not portal_admitted(
            campaign_facts(scheduled, SystemConfiguration.objects.get()),
            scheduled.active_configuration.ends_at,
        )
        assert run(start)
        assert not run(close)
        assert not run(start)
    assert list(
        CampaignTransition.objects.order_by("created_at").values_list(
            "action", flat=True
        )
    ) == [
        "activate",
        "start",
        "close",
    ]
    assert set(
        TaskRun.objects.filter(task_type=TASK_TYPE).values_list("state", flat=True)
    ) == {"succeeded"}
    assert set(
        CampaignBoundaryOccurrence.objects.values_list("task_id", flat=True)
    ) == {close.task_root_id}


def test_start_worker_does_not_apply_future_close(scheduled):  # noqa: F811
    """Only the due start is executable at the inclusive opening instant."""
    with campaign_clock(scheduled.active_configuration.starts_at):
        with scheduler_session() as guard:
            (start,) = produce_boundaries(guard)
        assert run(start)
    scheduled.refresh_from_db()
    assert scheduled.state == "active"
    assert list(CampaignBoundaryOccurrence.objects.values_list("kind", flat=True)) == [
        "start"
    ]


def test_second_boundary_denial_rolls_back_start_and_both_occurrences(
    scheduled,  # noqa: F811
    monkeypatch,
):
    """A late owning denial cannot commit a partial ordered transition batch."""
    original = boundary_tasks._eligible

    def eligible(row):
        """Inject a failure only after the first transition's in-transaction effect."""
        return Campaign.objects.get(pk=row.campaign_id).state != "active" and original(
            row
        )

    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            _, close = produce_boundaries(guard)
        monkeypatch.setattr(boundary_tasks, "_eligible", eligible)
        with pytest.raises(PermissionError):
            run(close)
    scheduled.refresh_from_db()
    assert scheduled.state == "scheduled"
    assert CampaignTransition.objects.count() == 1
    assert set(CampaignBoundaryOccurrence.objects.values_list("state", flat=True)) == {
        "pending"
    }
    assert TaskRun.objects.get(pk=close.task_root_id).state == "running"


@pytest.mark.parametrize("after_commit", [False, True])
def test_crash_recovery_distinguishes_committed_effects_from_unfinished_work(
    scheduled,  # noqa: F811
    monkeypatch,
    after_commit,
):
    """Recovery succeeds only from domain proof; otherwise it retains retry delay."""
    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            _, close = produce_boundaries(guard)

        def crash(*args, **kwargs):
            """Simulate process failure without changing persisted task ownership."""
            raise RuntimeError("synthetic boundary crash")

        original_transition = Execution.transition

        def crash_completion(execution, action, **options):
            """Fail only acknowledgment, allowing the domain's bounded renewals."""
            if action == "complete":
                crash()
            return original_transition(execution, action, **options)

        with monkeypatch.context() as patch:
            if after_commit:
                patch.setattr(Execution, "transition", crash_completion)
            else:
                patch.setattr(boundary_tasks, "apply_due_boundaries", crash)
            with pytest.raises(RuntimeError, match="synthetic boundary crash"):
                run(close)
        live = _status(TaskRun.objects.get(pk=close.task_root_id))
        short = task_act(live, "heartbeat", lease_seconds=1)
        expire(short)
        assert run(close, recover=True)
    task = TaskRun.objects.get(pk=close.task_root_id)
    assert task.state == ("succeeded" if after_commit else "retry_wait")
    assert CampaignTransition.objects.count() == (3 if after_commit else 1)


def test_forged_or_unbound_task_views_are_not_admitted(scheduled):  # noqa: F811
    """A campaign UUID or stale current-view token cannot stand in for an occurrence."""
    with campaign_clock(scheduled.active_configuration.starts_at):
        with scheduler_session() as guard:
            (start,) = produce_boundaries(guard)
        status = _status(TaskRun.objects.get(pk=start.task_root_id))
        with work_transaction():
            with pytest.raises(PermissionError):
                admit_boundary("hint", replace(status, version=status.version + 1))
            unrelated = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=scheduled.pk,
                actor_id=None,
                correlation_id=uuid4(),
                idempotency_key=uuid4(),
                admit=lambda *args: True,
            )
            with pytest.raises(PermissionError, match="binding is unavailable"):
                admit_boundary("hint", unrelated)


def test_scheduler_handler_has_no_execution_port():
    """Even direct registry misuse cannot let a scheduler execute lifecycle effects."""
    with pytest.raises(PermissionError, match="scheduler cannot execute"):
        boundary_handler(scheduler=True).execute(None)


def test_post_claim_manifest_mismatch_blocks_boundary_effect(tmp_path):
    """A selected but unactivated manifest revokes an already claimed task."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    with campaign_clock(campaign.active_configuration.ends_at):
        with scheduler_session() as guard:
            _, close = produce_boundaries(guard)
        execution = claim_hint(
            close.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers=bind_authority({TASK_TYPE: boundary_handler()}, store),
        )
        assert execution is not None
        selected = store.active()
        document = selected.document()
        document["version_id"] = str(uuid4())
        document["predecessor_digest"] = selected.digest
        candidate = parse_version(document, validate_sections=store.validate_sections)
        store.write_version(candidate)
        store.select(candidate)
        with maintain_execution(execution), pytest.raises(ConfigError):
            execution.handler.execute(execution)
    campaign.refresh_from_db()
    assert campaign.state == "scheduled"
    assert CampaignTransition.objects.count() == 1
    assert TaskRun.objects.get(pk=close.task_root_id).state == "running"
    assert set(CampaignBoundaryOccurrence.objects.values_list("state", flat=True)) == {
        "pending"
    }


def test_overdue_start_hint_can_complete_close_in_same_authority_scope(scheduled):  # noqa: F811
    """Completing the hinted start does not revoke its atomic successor work."""
    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            start, close = produce_boundaries(guard)
        assert run(start) and run(close)
    scheduled.refresh_from_db()
    assert scheduled.state == "closed"


def test_locked_boundary_renews_short_claim_before_population_effect(
    scheduled,  # noqa: F811
    monkeypatch,
):
    """A slow effect gets the bounded domain budget, without weakening SQL expiry."""
    original_emit = boundaries._emit
    observed = []

    def delayed_emit(*args, **kwargs):
        """Exceed the initial short lease only after locked admission renews it."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT lease_expires_at-clock_timestamp() "
                "FROM stewardship_task_run WHERE id=%s",
                [close.task_root_id],
            )
            observed.append(cursor.fetchone()[0])
        Event().wait(3.1)
        return original_emit(*args, **kwargs)

    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            _, close = produce_boundaries(guard)
        execution = claim_hint(
            close.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: boundary_handler()},
        )
        with maintain_execution(execution):
            execution.heartbeat(seconds=3)
            monkeypatch.setattr(
                "parishkit.stewardship.campaigns.boundaries._emit", delayed_emit
            )
            execution.handler.execute(execution)
    assert len(observed) == 2
    assert all(
        timedelta(seconds=299) <= budget <= timedelta(seconds=301)
        for budget in observed
    )
    assert TaskRun.objects.get(pk=close.task_root_id).state == "succeeded"
