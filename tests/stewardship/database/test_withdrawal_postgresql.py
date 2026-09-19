"""Actual pre-start withdrawal through the restricted web/SQL lifecycle owner."""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from datetime import datetime, timedelta
from time import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, connection
from django.db.models import F

from parishkit.stewardship.accounts import withdrawal_commands as commands
from parishkit.stewardship.accounts.confirmation_commands import confirm
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.models import CampaignTransition
from parishkit.stewardship.campaigns.runtime import campaign_transaction
from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.storage import StaleRecordError

from . import test_setup_preparation_postgresql as setup_inputs
from .campaign_builders import campaign_clock
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_confirmation_sql_postgresql import fresh
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scheduled(request, monkeypatch):
    """Install, clean, prepare and confirm a genuinely future campaign once."""
    start = database_now().date() + timedelta(days=2)
    original_campaign, original_schedule = (
        setup_inputs.first_campaign,
        setup_inputs.schedule,
    )

    def dated_campaign(*args, **kwargs):
        """Preserve the genuine setup owner with dates relative to the SQL clock."""
        return original_campaign(
            *args,
            **(
                kwargs
                | {
                    "start_date": start.isoformat(),
                    "end_date": (start + timedelta(days=30)).isoformat(),
                }
            ),
        )

    def dated_schedule(*args, **kwargs):
        """Keep the real invitation within the campaign interval."""
        return original_schedule(*args, **(kwargs | {"date": start.isoformat()}))

    monkeypatch.setattr(setup_inputs, "first_campaign", dated_campaign)
    monkeypatch.setattr(setup_inputs, "schedule", dated_schedule)
    links = request.getfixturevalue("ready_links")
    preparation, arguments = prepare(links)
    with web_login():
        _, _, token = fresh(arguments)
        receipt = confirm(*arguments, token=token, typed="Production")
    campaign = preparation.transition.campaign
    campaign.refresh_from_db()
    links[0].cookies["pk_admin"] = arguments[0].session.session_key
    return SimpleNamespace(
        browser=links[0],
        campaign=campaign,
        confirmation=receipt,
        arguments=arguments[:3],
        path=f"/admin/campaign/{campaign.pk}/production/withdraw",
        activation_arguments=arguments,
        activation_token=token,
        ring=links[2],
    )


def test_withdrawal_http_is_exact_atomic_and_preserves_cleanup(scheduled, monkeypatch):
    """No hidden toggle: CSRF, consent, fresh intent, history and replay all matter."""
    item = scheduled
    campaign = item.campaign
    revision = campaign.readiness_revision
    with web_login():
        progress = item.browser.get(f"/admin/campaign/{campaign.pk}/production")
        assert progress.status_code == 200
        assert progress.context["withdrawal_available"]
        page = item.browser.get(item.path)
        assert page.status_code == 200, page.content
        assert page.context["available"] and page.context["fresh"]
        assert page["Cache-Control"] == "no-store"
        assert item.browser.head(item.path).status_code == 200
        values = {
            "action": "preview",
            "reason": "Correct campaign configuration",
            "acknowledged": "yes",
        }
        assert item.browser.post(item.path, values).status_code == 403
        for changed in (
            values | {"reason": " "},
            values | {"acknowledged": "no"},
            values | {"actor": str(uuid4())},
        ):
            assert post(item.browser, item.path, changed).status_code == 400
        page = post(item.browser, item.path, values)
        assert page.status_code == 200, page.content
        inventory = page.context["preview"]["inventory"]
        assert (
            inventory["blocking"]
            == inventory["cancellable"]
            == inventory["messages"]
            == 0
        )
        token = page.context["withdrawal_token"]
        confirm_values = {"action": "confirm", "preview": token}
        assert (
            post(
                item.browser, item.path, confirm_values | {"reason": "Changed reason"}
            ).status_code
            == 400
        )
        response = post(item.browser, item.path, confirm_values)
        assert response.status_code == 302, response.content
        receipt = ProductionWithdrawal.objects.get()
        assert receipt.reason == values["reason"]
        assert receipt.cleanup_acknowledged
        assert receipt.confirmation_id == item.confirmation.pk
        assert post(item.browser, item.path, confirm_values).status_code == 302
        assert ProductionWithdrawal.objects.count() == 1
        with monkeypatch.context() as patch:
            patch.setattr(signing, "time", SimpleNamespace(time=lambda: time() + 301))
            with pytest.raises(signing.SignatureExpired):
                signing.loads(token, salt=commands.SALT, max_age=300)
            assert post(item.browser, item.path, confirm_values).status_code == 302
        result = item.browser.get(item.path)
        assert b"Withdrawal completed" in result.content
        assert not result.context["available"]
        assert (
            item.browser.get(f"/admin/campaign/{campaign.pk}/production").status_code
            == 403
        )
        # Old signed activation intent cannot reactivate the withdrawn campaign.
        replay = confirm(
            *item.activation_arguments, token=item.activation_token, typed="Production"
        )
        assert replay.pk == item.confirmation.pk
    campaign.refresh_from_db()
    assert campaign.state == "draft" and not campaign.ever_active
    assert not campaign.structural_locked
    assert campaign.active_token_generation_id is None
    assert campaign.readiness_revision == revision + 1
    assert SystemConfiguration.objects.get().mode == "testing"
    assert item.confirmation.request.events.filter(action="complete").exists()
    assert (
        CampaignTransition.objects.get(pk=receipt.transition_id).reason
        == receipt.reason
    )
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    with web_login():
        assert item.browser.get(item.path).status_code == 403
        assert post(item.browser, item.path, confirm_values).status_code == 403


def test_withdrawal_expiry_start_and_private_effect_rollback(scheduled):
    """A delayed boundary cannot extend eligibility; failed effects roll back intent."""
    item = scheduled
    with web_login():
        _, token = commands.preview(
            *item.arguments, reason="Fix dates", acknowledged=True
        )
    with (
        campaign_clock(item.campaign.active_configuration.starts_at),
        web_login(),
        pytest.raises(StaleRecordError),
    ):
        commands.withdraw(*item.arguments, token=token)
    assert not ProductionWithdrawal.objects.exists()
    with web_login():

        def abort_transition(execute, sql, parameters, many, context):
            """Inject after the private effect ran, before its enclosing commit."""
            result = execute(sql, parameters, many, context)
            if sql.startswith('INSERT INTO "stewardship_production_withdrawal"'):
                raise RuntimeError("synthetic response loss before commit")
            return result

        with connection.execute_wrapper(abort_transition), pytest.raises(RuntimeError):
            commands.withdraw(*item.arguments, token=token)
        assert not ProductionWithdrawal.objects.exists()
        item.campaign.refresh_from_db()
        assert item.campaign.state == "scheduled"
        assert SystemConfiguration.objects.get().mode == "production"
        receipt = commands.withdraw(*item.arguments, token=token)
        assert receipt.pk


def test_withdrawal_direct_sql_rechecks_session_inventory_and_lock_order(scheduled):
    """The service's INSERT grant is not an unguarded runtime-mode write."""
    item = scheduled
    with web_login():
        bound, token = commands.preview(
            *item.arguments, reason="Correct setup", acknowledged=True
        )
        values = dict(
            confirmation_id=item.confirmation.pk,
            transition_id=uuid4(),
            request_key=uuid4(),
            session_id=item.arguments[0].portal_session.pk,
            authenticated_at=item.arguments[0].portal_session.authenticated_at,
            preview_at=datetime.fromisoformat(bound["observed"]),
            expires_at=datetime.fromisoformat(bound["expires"]),
            expected_campaign_version=bound["campaign_version"],
            expected_runtime_version=bound["runtime_version"],
            inventory=bound["inventory"],
            reason=bound["reason"],
            cleanup_acknowledged=True,
            actor_id=item.arguments[0].portal_session.principal_id,
            correlation_id=uuid4(),
        )
        with pytest.raises(DatabaseError):
            ProductionWithdrawal.objects.create(**values)
        for patch in (
            {"session_id": uuid4()},
            {"actor_id": uuid4()},
            {"authenticated_at": values["authenticated_at"] - timedelta(minutes=6)},
            {"inventory": bound["inventory"] | {"messages": 9}},
            {"expected_campaign_version": bound["campaign_version"] + 1},
            {"expected_runtime_version": bound["runtime_version"] + 1},
            {"reason": " "},
            {"cleanup_acknowledged": False},
            {"confirmation_id": uuid4()},
            {"expires_at": values["preview_at"]},
        ):
            with (
                pytest.raises(DatabaseError),
                campaign_transaction(item.campaign.pk, correlation_id=uuid4()),
            ):
                ProductionWithdrawal.objects.create(**(values | patch))
        assert not ProductionWithdrawal.objects.exists()
        # Private cancellation mechanics have no runtime EXECUTE grant.
        with pytest.raises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_schedule_cancel_v1(%s,%s,%s,%s,%s)",
                [uuid4(), uuid4(), values["actor_id"], uuid4(), "production_withdrawn"],
            )
        receipt = commands.withdraw(*item.arguments, token=token)
        with pytest.raises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stewardship_production_withdrawal SET reason=%s WHERE id=%s",
                ["rewrite", receipt.pk],
            )


@pytest.mark.parametrize("failed", [False, True])
def test_next_go_live_requires_new_test_cleanup_links_and_confirmation(
    scheduled, tmp_path, failed
):
    """Complete a second real cycle, never revive the old test or prepared links."""
    from parishkit.stewardship.accounts import campaign_mail, go_live_commands
    from parishkit.stewardship.accounts.content_models import ContentVersion
    from parishkit.stewardship.accounts.go_live_inputs import collect_inputs
    from parishkit.stewardship.campaigns.activation_models import (
        ProductionTokenPreparation,
    )
    from parishkit.stewardship.campaigns.activation_tasks import token_handler
    from parishkit.stewardship.campaigns.activation_tokens import TASK_TYPE
    from parishkit.stewardship.campaigns.confirmation_models import (
        ProductionConfirmation,
    )
    from parishkit.stewardship.campaigns.digest_schedule_planning import (
        DigestScheduleProducer,
    )
    from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
    from parishkit.stewardship.campaigns.production_models import (
        ProductionTransitionRequest,
    )
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.campaigns.schedules import occurrence_key
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.reports.weekly_manual import request_manual_report

    from .production_cycle_checks import reject_retired_cycle
    from .test_background_grants_postgresql import task_login
    from .test_campaign_mail_postgresql import deliver
    from .test_cleanup_tasks_postgresql import run
    from .test_daily_digest_fanout_postgresql import configure_content as daily_content
    from .test_digest_schedule_planning_postgresql import add_digest
    from .test_weekly_fanout_postgresql import configure_content as weekly_content
    from .test_withdrawal_work_postgresql import fail_retained, future_message

    item = scheduled
    login, service, campaign_id = item.arguments
    for weekly in (False, True):
        add_digest(service.store, item.campaign, weekly=weekly)
    harness = SimpleNamespace(service=service, campaign=item.campaign)
    daily_content(harness)
    weekly_content(harness)
    previous, old_message = future_message(item)
    if failed:
        old_message = fail_retained(previous, old_message)
    cutoff = item.campaign.active_configuration.starts_at + timedelta(days=9)
    producer = DigestScheduleProducer(uuid4())
    with (
        campaign_clock(cutoff),
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        scheduler_session() as guard,
    ):
        first_reports = producer(guard) + producer(guard)
        assert all(report.created for report in first_reports)
    with web_login():
        _, token = commands.preview(
            *item.arguments, reason="Revise before opening", acknowledged=True
        )
        commands.withdraw(*item.arguments, token=token)
        state = collect_inputs(*item.arguments)
        assert "family_test_mail_required" in state.problems
        _, _, old_evidence = go_live_commands.verify_preview(*item.arguments)
        assert old_evidence is None
        template = ContentVersion.objects.get(
            slot="initial", configuration_id=service.store.active().version_id
        )
        preview = campaign_mail.prepare(login, service, campaign_id, template.record_id)
        campaign_mail.request_sample(
            login,
            service,
            campaign_id,
            template.record_id,
            preview_token=signing.dumps(preview.binding(), salt=campaign_mail.SALT),
        )
    assert (
        deliver(
            (service, None, None, tmp_path / "google_workspace" / "credential")
        ).state
        == "accepted"
    )
    with web_login():
        inputs, _, token = go_live_commands.verify_preview(*item.arguments)
        assert not inputs.problems, inputs.problems
        status = go_live_commands.start_cleanup(
            *item.arguments, preview_token=token, acknowledge=True
        )
        assert status.request_id != item.confirmation.request_id
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert run(status)
    path = f"/admin/campaign/{campaign_id}/go-live/cleanup/{status.request_id}/links"
    with web_login():
        page = item.browser.get(path)
        assert page.status_code == 200, page.content
        response = post(item.browser, path, {"control": page.context["prepare"]})
        assert response.status_code == 302, response.content
    preparation = ProductionTokenPreparation.objects.get(
        transition_id=status.request_id
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            preparation.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=item.ring.public)},
        )
    with web_login():
        arguments = login, service, campaign_id, status.request_id, preparation.pk
        _, _, token = fresh(arguments)
        second = confirm(*arguments, token=token, typed="Production")
        assert second.generation_id != item.confirmation.generation_id
    assert (
        ProductionConfirmation.objects.count()
        == ProductionTransitionRequest.objects.count()
        == 2
    )
    assert ProductionWithdrawal.objects.count() == 1
    item.campaign.refresh_from_db()
    assert item.campaign.state == "scheduled"
    assert item.campaign.active_token_generation_id == second.generation_id
    assert SystemConfiguration.objects.get().mode == "production"
    previous.refresh_from_db()
    if failed:
        assert previous.state == "failed"
    else:
        assert previous.state == "skipped" and previous.reason == "production_withdrawn"
    assert previous.production_cycle == 0
    assert item.campaign.production_cycle == 1
    with campaign_clock(previous.due_at):
        reject_retired_cycle(item, previous, old_message, failed=failed)
        with web_login():
            command = uuid4()
            request_manual_report(
                service.store,
                login.portal_session.principal_id,
                campaign_id,
                command_id=command,
                configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            )
        manual = ScheduleOccurrence.objects.get(pk=command)
        assert manual.production_cycle == 1
        assert manual.occurrence_key == occurrence_key(
            manual.revision_id, "production", "admins", manual.slot, production_cycle=1
        )
    with (
        campaign_clock(previous.due_at),
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        scheduler_session() as guard,
    ):
        result = plan_family(
            guard,
            family_id=UUID(previous.target.removeprefix("family:")),
            worker_id=uuid4(),
        )
        assert result.created == 1
    replacement = ScheduleOccurrence.objects.get(
        revision_id=previous.revision_id,
        target=previous.target,
        production_cycle=1,
    )
    assert replacement.state == "pending"
    assert replacement.occurrence_key != previous.occurrence_key
    assert replacement.slot == previous.slot
    with (
        campaign_clock(cutoff),
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        scheduler_session() as guard,
    ):
        # Reuse the same long-lived producer, not just a clean process cursor.
        next_reports = producer(guard) + producer(guard)
        assert [report.created for report in next_reports] == [
            report.created for report in first_reports
        ]
    assert (
        ScheduleOccurrence.objects.filter(
            definition__kind__in=("daily_digest", "weekly_digest"),
            production_cycle=0,
        )
        .exclude(state="skipped", reason="production_withdrawn")
        .count()
        == 0
    )
    assert ScheduleOccurrence.objects.filter(
        definition__kind__in=("daily_digest", "weekly_digest"),
        production_cycle=1,
    ).exclude(slot__startswith="manual:").count() == sum(
        report.created for report in first_reports
    )
