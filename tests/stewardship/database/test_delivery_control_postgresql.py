"""Current-Admin pause commits exact control and unsent holds without rerouting."""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.runtime import campaign_transaction
from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.storage import StaleRecordError

from ..campaign_factory import schedule
from ..content_factory import content
from . import test_setup_preview_postgresql as preview_inputs
from .campaign_builders import advance, campaign_clock
from .test_campaign_mail_postgresql import deliver
from .test_digest_schedule_planning_postgresql import add_digest
from .test_outbox_postgresql import claim
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)
from .test_withdrawal_work_postgresql import fail_retained, future_message

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def delivery_scheduled(request, monkeypatch):
    """Add three later reminders through the real setup owner before activation."""
    original = preview_inputs.with_schedules

    def reminders(service, patch):
        """Keep one setup for HTTP, SQL and complete-group policy acceptance."""
        login, status, initial, row = original(service, patch)
        reminder = content(str(status.attempt_id), kind="email", slot="reminder")
        with web_login():
            status = save_section(
                login,
                service,
                status.attempt_id,
                step="email_reminder",
                values=reminder,
                expected_version=status.version,
            )
            records = [row] + [
                schedule(
                    str(status.attempt_id),
                    kind="reminder",
                    date=row["values"]["date"],
                    time=f"{hour:02d}:00:00",
                    template_version=reminder["id"],
                    subject=reminder["values"]["subject"],
                )
                for hour in (10, 11, 12)
            ]
            status = save_section(
                login,
                service,
                status.attempt_id,
                step="schedules",
                values={"records": records},
                expected_version=status.version,
            )
        return login, status, initial, row

    monkeypatch.setattr(preview_inputs, "with_schedules", reminders)
    return request.getfixturevalue("scheduled")


def raw_values(item, binding):
    """Mirror the exact bound command for independent restricted-SQL probes."""
    login = item.arguments[0].portal_session
    return dict(
        campaign_id=item.campaign.pk,
        control_id=uuid4(),
        action="pause",
        session_id=login.pk,
        authenticated_at=login.authenticated_at,
        actor_id=login.principal_id,
        correlation_id=uuid4(),
        preview_at=datetime.fromisoformat(binding["observed"]),
        expires_at=datetime.fromisoformat(binding["expires"]),
        expected_campaign_version=binding["campaign_version"],
        expected_runtime_version=binding["runtime_version"],
        inventory=binding["inventory"],
        selection={},
        reason=binding["reason"],
    )


def assert_sql_pause_boundary(item, binding):
    """Exercise direct restricted INSERT without trusting signed-form validation.

    The same genuine setup supports all negative probes; failed commands each
    roll back independently before the ordinary HTTP/service acceptance path.
    """
    values = raw_values(item, binding)
    with pytest.raises(DatabaseError) as failure:
        DeliveryControlCommand.objects.create(**values)
    assert failure.value.__cause__.sqlstate == "42501"
    for changed in (
        {"actor_id": uuid4()},
        {"session_id": uuid4()},
        {"authenticated_at": values["authenticated_at"] - timedelta(minutes=6)},
        {"expected_campaign_version": binding["campaign_version"] + 1},
        {"expected_runtime_version": binding["runtime_version"] + 1},
        {"expires_at": values["preview_at"]},
        {"expires_at": values["expires_at"] + timedelta(seconds=1)},
        {"inventory": binding["inventory"] | {"queued": 10}},
        {"selection": {"force": True}},
        {"reason": " "},
        {"control_id": None},
        {"action": "resume"},
        {"action": "resolve"},
    ):
        with (
            pytest.raises(DatabaseError) as failure,
            campaign_transaction(item.campaign.pk, correlation_id=uuid4()),
        ):
            DeliveryControlCommand.objects.create(**(values | changed))
        assert failure.value.__cause__.sqlstate in {"42501", "23514"}, changed
    assert not DeliveryControlCommand.objects.exists()


def assert_unmaterialized_backlog(item):
    """A rolled-back real pause proves complete counts before any outbox exists."""
    due = (
        ScheduleDefinition.objects.filter(kind="reminder")
        .order_by("-current_revision__due_at")
        .values_list("current_revision__due_at", flat=True)
        .first()
    )
    families = FamilyCampaign.objects.filter(
        campaign=item.campaign,
        active=True,
        email_eligible=True,
        effective_submission_id__isnull=True,
    )
    eligible, deliverable = (
        families.count(),
        families.filter(email_deliverable=True).count(),
    )
    with campaign_clock(due), web_login():
        binding, _ = commands.preview_pause(
            *item.arguments, reason="Check complete backlog"
        )
        with campaign_transaction(item.campaign.pk, correlation_id=uuid4()):
            DeliveryControlCommand.objects.create(**raw_values(item, binding))
            impact = commands.family_recovery_impact(item.campaign.pk)
            assert impact["selected"] == deliverable
            assert impact["coalesced"] == 3 * deliverable
            assert impact["skipped"] == 4 * (eligible - deliverable)
            assert impact["unmaterialized"] == 4 * eligible
            assert impact["blocked"] == 0
            with (
                pytest.raises(DatabaseError) as failure,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT * FROM stewardship_delivery_family_recovery")
            assert failure.value.__cause__.sqlstate == "42501"
            transaction.set_rollback(True)
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    assert not DeliveryControlCommand.objects.exists()


def test_pause_is_atomic_exact_and_keeps_delivery_payload(
    delivery_scheduled, monkeypatch, tmp_path
):
    """Pause holds current work despite live inventory churn, preserving its bytes."""
    item = delivery_scheduled
    path = f"/admin/campaign/{item.campaign.pk}/delivery"
    with web_login():
        page = item.browser.get(path)
        assert page.status_code == 200 and "no-store" in page["Cache-Control"]
        assert (
            item.browser.post(
                path, {"action": "preview_pause", "reason": "Check"}
            ).status_code
            == 403
        )
        assert (
            post(
                item.browser,
                path,
                {"action": "preview_pause", "reason": "Check", "override": "yes"},
            ).status_code
            == 400
        )
        before = commands.page(*item.arguments)
        assert before["next_due"]["count"] == 1
        assert before["next_due"]["types"] == {"initial": 1}
        binding, stale = commands.preview_pause(*item.arguments, reason="Check sender")
        assert before["available"] and binding["inventory"]["queued"] == 0
        assert_sql_pause_boundary(item, binding)
    assert_unmaterialized_backlog(item)
    _, message = future_message(item)
    original = OutboxMessage.objects.get(pk=message.message_id)
    with web_login():
        token = stale
        receipt = commands.confirm(*item.arguments, token=token)
        assert receipt.inventory["queued"] == 1
        assert commands.confirm(*item.arguments, token=token).pk == receipt.pk
        status = commands.page(*item.arguments)
        # The successful go-live test predates this pause and cannot release it.
        assert not status["health"]["ready"]
        assert status["inventory"]["held"] == 1
        assert status["campaign"].delivery_paused
        page = item.browser.get(path)
        assert page.status_code == 200
        assert page.context["admin_chrome"]["delivery_pause"]["inventory"]["held"] == 1
        assert (
            page.context["admin_chrome"]["delivery_pause"]["reason"] == "Check sender"
        )
        assert item.browser.get(reverse("admin:background")).status_code == 200
    item.campaign.refresh_from_db()
    held = OutboxMessage.objects.get(pk=original.pk)
    assert held.pause_hold_id is not None and held.action == "hold"
    assert held.state == original.state == "pending" and held.attempt == 0
    assert held.render_id == original.render_id
    assert held.sealed_substitutions == original.sealed_substitutions
    assert held.routing == original.routing == "production"
    assert item.campaign.state == "scheduled"
    assert SystemConfiguration.objects.get().mode == "production"
    assert DeliveryControlCommand.objects.count() == 1
    test_path = reverse(
        "admin:campaign_mail", args=[item.campaign.pk, status["test_template"]]
    )
    resume_token = None
    for outcome, ready in (
        (DeliveryOutcome.NOT_SENT, False),
        (DeliveryOutcome.ACCEPTED, True),
        (DeliveryOutcome.UNKNOWN, False),
        (DeliveryOutcome.ACCEPTED, True),
    ):
        with web_login():
            page = item.browser.get(test_path)
            assert page.status_code == 200, page.content
            token = page.context["form"]["preview_token"].value()
            assert (
                post(
                    item.browser,
                    test_path,
                    {"preview_token": token, "acknowledge_unknown": True},
                ).status_code
                == 302
            )
            assert not commands.health(item.campaign.pk)["ready"]
            if resume_token:
                with pytest.raises(StaleRecordError, match="inputs changed"):
                    commands.confirm(*item.arguments, token=resume_token)
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
            lambda *args, result=outcome, **kwargs: result,
        )
        result = deliver(
            (
                item.arguments[1],
                None,
                None,
                tmp_path / "google_workspace" / "credential",
            )
        )
        assert result.state == outcome.value
        with web_login():
            proof = commands.health(item.campaign.pk)
            assert proof["ready"] is ready
            if ready:
                assert proof["proof"] == str(result.pk)
                page = post(
                    item.browser,
                    path,
                    {"action": "preview_resume", "reason": "Sender verified"},
                )
                assert page.status_code == 200, page.content
                resume_token = page.context["control_token"]
        held.refresh_from_db()
        item.campaign.refresh_from_db()
        assert held.pause_hold_id and item.campaign.delivery_paused
        assert held.attempt == 0 and held.routing == "production"
    assert CampaignMailTest.objects.filter(state="delivery_unknown").count() == 1
    with web_login():
        # A newer accepted test can supersede a prior uncertain test, but no
        # uncertain live outbox is silently resolved by this sender check.
        response = post(
            item.browser, path, {"action": "confirm", "preview": resume_token}
        )
        assert response.status_code == 302, response.content
        resumed = commands.confirm(*item.arguments, token=resume_token)
        assert resumed.action == "resume"
    item.campaign.refresh_from_db()
    held.refresh_from_db()
    assert not item.campaign.delivery_paused
    assert held.pause_hold_id is None and held.action == "release_hold"
    assert held.render_id == original.render_id and held.attempt == 0
    assert held.sealed_substitutions == original.sealed_substitutions
    assert item.campaign.state == "scheduled"
    assert_family_resume(item, monkeypatch, tmp_path)


def assert_family_resume(item, monkeypatch, tmp_path):
    """Recover held and unmaterialized reminders in the same real transaction."""
    # Future activation has no missed-work demand; advancing only the domain
    # clock exercises ordinary due planning without fabricating a completion.
    assert not ActivationCatchUpDemand.objects.filter(campaign=item.campaign).exists()
    reminder = (
        ScheduleDefinition.objects.select_related("current_revision")
        .filter(kind="reminder")
        .order_by("current_revision__due_at")
        .first()
    )
    _, old = future_message(item, definition=reminder)
    second = (
        ScheduleDefinition.objects.select_related("current_revision")
        .filter(kind="reminder")
        .exclude(pk=reminder.pk)
        .order_by("current_revision__due_at")
        .first()
    )
    running_row, running_mail = future_message(item, definition=second)
    due = (
        ScheduleDefinition.objects.filter(kind="reminder")
        .order_by("-current_revision__due_at")
        .values_list("current_revision__due_at", flat=True)
        .first()
    )
    with campaign_clock(due):
        running_task = claim(running_mail)
        advance(
            running_row,
            running_task.worker_id,
            "running",
            task_id=running_task.run_id,
            fence=running_task.fence,
        )
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review backlog")
            commands.confirm(*item.arguments, token=token)
            status = commands.page(*item.arguments)
            assert status["family_recovery"]["selected"] == 1
            assert status["family_recovery"]["coalesced"] == 3
            assert status["family_recovery"]["unmaterialized"] == 1
            test_path = reverse(
                "admin:campaign_mail", args=[item.campaign.pk, status["test_template"]]
            )
            page = item.browser.get(test_path)
            assert page.status_code == 200
            token = page.context["form"]["preview_token"].value()
            assert (
                post(
                    item.browser,
                    test_path,
                    {
                        "preview_token": token,
                        "acknowledge_unknown": True,
                    },
                ).status_code
                == 302
            )
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
            lambda *args, **kwargs: DeliveryOutcome.ACCEPTED,
        )
        assert (
            deliver(
                (
                    item.arguments[1],
                    None,
                    None,
                    tmp_path / "google_workspace" / "credential",
                )
            ).state
            == "accepted"
        )
        with web_login():
            preview, token = commands.preview_resume(
                *item.arguments, reason="Release selected backlog"
            )
            assert preview["selection"]["plan"] == "family"
            commands.confirm(*item.arguments, token=token)
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    assert ScheduleOccurrence.objects.filter(state="coalesced").count() == 3
    assert ScheduleFulfillment.objects.filter(disposition="coalesced").count() == 3
    cancelled = OutboxMessage.objects.get(pk=old.message_id)
    assert cancelled.state == "cancelled" and cancelled.pause_hold_id is None
    assert cancelled.sealed_substitutions is None
    assert TaskRun.objects.get(pk=cancelled.task_id).state == "cancelled"
    assert OutboxMessage.objects.get(pk=running_mail.message_id).state == "cancelled"
    assert TaskRun.objects.get(pk=running_task.run_id).state == "running"


def test_failed_initial_defers_only_its_family_not_campaign_resume(
    delivery_scheduled, monkeypatch, tmp_path
):
    """Resume preserves ordinary per-Family recovery without a campaign deadlock."""
    item = delivery_scheduled
    occurrence, message = future_message(item)
    fail_retained(occurrence, message)
    reminder = ScheduleDefinition.objects.filter(kind="reminder").order_by("id").first()
    _, waiting = future_message(item, definition=reminder)
    due = item.campaign.active_configuration.starts_at + timedelta(days=1)
    with campaign_clock(due):
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Review failed initial"
            )
            commands.confirm(*item.arguments, token=token)
            impact = commands.family_recovery_impact(item.campaign.pk)
            assert impact["blocked"] == 0 and impact["deferred"] == 1
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            _, token = commands.preview_resume(
                *item.arguments, reason="Resume other work"
            )
            commands.confirm(*item.arguments, token=token)
        item.campaign.refresh_from_db()
        occurrence.refresh_from_db()
        assert not item.campaign.delivery_paused and occurrence.state == "failed"
        assert not ScheduleFulfillment.objects.filter(occurrence=occurrence).exists()
        from parishkit.stewardship.deployment import ServiceRole
        from parishkit.stewardship.jobs.family_mail_dispatch import FamilyDeliveryHeld

        from .test_background_grants_postgresql import task_login
        from .test_daily_digest_dispatch_postgresql import begin
        from .test_family_mail_dispatch_postgresql import claim as claim_delivery

        held_reminder = OutboxMessage.objects.get(pk=waiting.message_id)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim_delivery(held_reminder)
            with pytest.raises(FamilyDeliveryHeld, match="recovery is held"):
                begin(held_reminder, execution)


def accepted_sender_check(item, monkeypatch, tmp_path):
    """Exercise the real explicit-test owner; only the provider result is fake."""
    with web_login():
        status = commands.page(*item.arguments)
        path = reverse(
            "admin:campaign_mail", args=[item.campaign.pk, status["test_template"]]
        )
        page = item.browser.get(path)
        assert page.status_code == 200
        token = page.context["form"]["preview_token"].value()
        assert (
            post(
                item.browser,
                path,
                {"preview_token": token, "acknowledge_unknown": True},
            ).status_code
            == 302
        )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
        lambda *args, **kwargs: DeliveryOutcome.ACCEPTED,
    )
    assert (
        deliver(
            (
                item.arguments[1],
                None,
                None,
                tmp_path / "google_workspace" / "credential",
            )
        ).state
        == "accepted"
    )


def test_resume_materializes_every_due_digest_day(scheduled, monkeypatch, tmp_path):
    """Resume is independent of scheduler page size and retains original coverage."""
    item = scheduled
    identifiers = {
        add_digest(item.arguments[1].store, item.campaign),
        add_digest(item.arguments[1].store, item.campaign, weekly=True),
    }
    due = item.campaign.active_configuration.starts_at + timedelta(days=15, minutes=14)
    definitions = list(
        ScheduleDefinition.objects.select_related("current_revision").filter(
            pk__in=identifiers
        )
    )
    expected = {
        d.pk: tuple(
            slot.key
            for slot in SchedulePlan.from_values(
                d.current_revision.values, item.campaign.active_configuration.values
            )
            .page(through=due, limit=100)
            .slots
        )
        for d in definitions
    }
    with campaign_clock(due):
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Review overdue reports"
            )
            commands.confirm(*item.arguments, token=token)
            impact = commands.digest_recovery_impact(item.campaign.pk)
            assert impact["selected"] == 2 and impact["blocked"] == 0
            assert (
                impact["slots"]
                == impact["unmaterialized"]
                == sum(map(len, expected.values()))
            )
            for private in (
                "stewardship_delivery_digest_recovery",
                "stewardship_delivery_digest_effect",
            ):
                with (
                    pytest.raises(DatabaseError) as failure,
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(f"SELECT * FROM {private}")
                assert failure.value.__cause__.sqlstate == "42501"
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            preview, token = commands.preview_resume(
                *item.arguments, reason="Release combined reports"
            )
            assert preview["selection"]["digests"] == impact
            forged = raw_values(item, preview) | {
                "action": "resume",
                "selection": preview["selection"]
                | {"digests": impact | {"slots": impact["slots"] + 1}},
            }
            with (
                pytest.raises(DatabaseError) as failure,
                campaign_transaction(item.campaign.pk, correlation_id=uuid4()),
            ):
                DeliveryControlCommand.objects.create(**forged)
            assert failure.value.__cause__.sqlstate == "23514"
        with (
            campaign_clock(due + timedelta(minutes=2)),
            web_login(),
            pytest.raises(StaleRecordError, match="inputs changed"),
        ):
            commands.confirm(*item.arguments, token=token)
        with web_login():
            commands.confirm(*item.arguments, token=token)
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    for definition in definitions:
        rows = ScheduleOccurrence.objects.filter(definition=definition).order_by(
            "due_at"
        )
        assert tuple(rows.values_list("slot", flat=True)) == expected[definition.pk]
        selected = rows.get(state="pending")
        assert (
            tuple(day.isoformat() for day in covered_dates(selected.pk))
            == expected[definition.pk]
        )
        assert (
            rows.filter(state="coalesced", replacement=selected).count()
            == len(expected[definition.pk]) - 1
        )
        assert selected.task_id is None and selected.outbox_id is None
