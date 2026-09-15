"""End-date preflight cannot overlook a close worker claimed before binding."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.admission import (
    CampaignAdmissionUnavailable,
    close_work_running,
)
from parishkit.stewardship.campaigns.boundary_production import produce_boundaries
from parishkit.stewardship.campaigns.boundary_revisions import current_boundary
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import CampaignBoundaryOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import enqueue

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    command,
    draft_campaign,
    end_request,
)
from .test_background_grants_postgresql import task_login
from .test_boundary_tasks_postgresql import run
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


def future_close(campaign):
    """Retained future work is real SQL data, not a fabricated admission callback."""
    with work_transaction():
        row = current_boundary(
            campaign_id=campaign.pk,
            kind="close",
            due_at=campaign.active_configuration.ends_at,
            actor_id=None,
            correlation_id=uuid4(),
        )
        task = enqueue(
            task_type="campaign_boundary",
            domain_request_id=campaign.pk,
            actor_id=None,
            correlation_id=uuid4(),
            idempotency_key=row.pk,
            admit=lambda *args: True,
        )
    return row, task


@pytest.mark.parametrize("state", ["queued", "running", "abandoned"])
def test_preflight_sees_root_claim_even_before_occurrence_binding(tmp_path, state):
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        row, task = future_close(campaign)
        if state != "queued":
            task = act(task, "claim", lease_seconds=1 if state == "abandoned" else 300)
        if state == "abandoned":
            task = expire(task)
        assert row.task_id is None
        assert close_work_running(campaign.pk) is (state != "queued")
        request, _ = end_request(store, campaign, actor, "edit_end")
        previous = store.active()
        if state == "queued":
            with task_login(ServiceRole.CONFIG_INSTALLER, exact=True):
                result = install_request(
                    store,
                    request_id=request.request_id,
                    correlation_id=uuid4(),
                    admit_campaign=admit_test_work,
                )
            assert result.state == "applied"
            row.refresh_from_db()
            assert row.state == "skipped" and row.reason == "boundary_replaced"
        else:
            with pytest.raises(CampaignAdmissionUnavailable):
                install_request(
                    store,
                    request_id=request.request_id,
                    correlation_id=uuid4(),
                    admit_campaign=admit_test_work,
                )
            assert store.active() == previous


def test_end_date_a_b_a_keeps_history_and_allocates_a_fresh_executable_root(tmp_path):
    """A stale hint cancels only old work, even when its date is current again."""
    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        a, a_task = future_close(campaign)
        for date in ("2026-11-10", original.end_date.isoformat()):
            request, _ = end_request(store, campaign, actor, "edit_end", date)
            with task_login(ServiceRole.CONFIG_INSTALLER, exact=True):
                assert (
                    install_request(
                        store,
                        request_id=request.request_id,
                        correlation_id=uuid4(),
                        admit_campaign=admit_test_work,
                    ).state
                    == "applied"
                )
            campaign.refresh_from_db()
            if date == "2026-11-10":
                b, b_task = future_close(campaign)
        rows = list(
            CampaignBoundaryOccurrence.objects.filter(kind="close").order_by(
                "execution_revision"
            )
        )
        assert [row.execution_revision for row in rows] == [1, 2, 3]
        assert [row.state for row in rows] == ["skipped", "skipped", "pending"]
        assert rows[0].due_at == rows[2].due_at == original.ends_at
        assert rows[1].due_at != original.ends_at
        saved = list(
            CampaignBoundaryOccurrence.objects.filter(pk__in=[a.pk, b.pk]).values()
        )
        for task in (a_task, b_task):
            assert run(SimpleNamespace(task_root_id=task.root_id))
            assert TaskRun.objects.get(pk=task.root_id).state == "cancelled"
        campaign.refresh_from_db()
        assert campaign.state == "active"
        assert (
            list(
                CampaignBoundaryOccurrence.objects.filter(pk__in=[a.pk, b.pk]).values()
            )
            == saved
        )
    with campaign_clock(original.ends_at):
        with scheduler_session() as guard:
            work = produce_boundaries(guard)
            closing = next(item for item in work if item.kind == "close")
            assert produce_boundaries(guard) == work
        assert closing.occurrence_id == rows[2].pk
        assert closing.task_root_id not in {a_task.root_id, b_task.root_id}
        assert run(closing)
    campaign.refresh_from_db()
    assert campaign.state == "closed"
    assert (
        AuditContext.objects.filter(
            schema="boundary", context__occurrence_id=str(a.pk)
        ).count()
        == 1
    )
    assert (
        AuditContext.objects.filter(
            schema="boundary", context__occurrence_id=str(b.pk)
        ).count()
        == 1
    )


def test_boundary_revision_is_immutable_and_duplicate_allocation_is_rejected(tmp_path):
    """Raw ORM writes cannot rewrite the revision or allocate another current effect."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
        row, _ = future_close(campaign)
        with pytest.raises(IntegrityError), transaction.atomic():
            CampaignBoundaryOccurrence.objects.filter(pk=row.pk).update(
                execution_revision=2, version=F("version") + 1
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            CampaignBoundaryOccurrence.objects.create(
                campaign=campaign, kind="close", due_at=row.due_at, execution_revision=2
            )


def test_close_winning_before_reviewed_end_edit_requires_reopen(tmp_path):
    """An earlier review cannot publish an ordinary date edit after closing commits."""
    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        request, _ = end_request(store, campaign, actor, "edit_end")
    selected = store.active()
    with campaign_clock(original.ends_at):
        with scheduler_session() as guard:
            close = next(
                item for item in produce_boundaries(guard) if item.kind == "close"
            )
        assert run(close)
        result = install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        )
    assert result.state == "failed"
    assert store.active() == selected
    campaign.refresh_from_db()
    assert campaign.state == "closed"
    assert campaign.active_configuration_id == original.pk
    assert CampaignBoundaryOccurrence.objects.filter(kind="close").count() == 1


@pytest.mark.parametrize("state", ["running", "abandoned"])
def test_sql_end_edit_excludes_unbound_close_even_without_python_preflight(
    tmp_path, monkeypatch, state
):
    """SQL independently rejects a current root claim before occurrence binding."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        row, task = future_close(campaign)
        task = act(task, "claim", lease_seconds=1 if state == "abandoned" else 300)
        if state == "abandoned":
            expire(task)
        request, _ = end_request(store, campaign, actor, "edit_end")
        monkeypatch.setattr(
            "parishkit.stewardship.campaigns.admission.close_work_running",
            lambda _: False,
        )
        with pytest.raises(IntegrityError, match="quiescent exceptional intent"):
            install_request(
                store,
                request_id=request.request_id,
                correlation_id=uuid4(),
                admit_campaign=admit_test_work,
            )
    row.refresh_from_db()
    assert row.state == "pending" and row.task_id is None
