"""Manual reports are explicit, separately audited, idempotent Admin obligations."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.production_models import ProductionCleanupTarget
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.reports.weekly_manual import request_manual_report
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
    WeeklyDigestSnapshot,
    WeeklyManualRequest,
)

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_digest_schedule_planning_postgresql import add_digest
from .test_policy_postgresql import user
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit as respond_form
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_completion_postgresql import history
from .test_weekly_coverage_postgresql import accepted, publish, replacement
from .test_weekly_dispatch_postgresql import allocated
from .test_weekly_fanout_postgresql import configure_content
from .test_weekly_tasks_postgresql import execute

pytestmark = pytest.mark.django_db(transaction=True)


def request(harness, principal, command):
    """Use the actual restricted Web identity, not schema-owner write access."""
    with task_login(ServiceRole.WEB, exact=True):
        return request_manual_report(
            harness.service.store,
            principal.pk,
            harness.campaign.pk,
            command_id=command,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
        )


def test_manual_after_success_repeats_only_by_explicit_intent(live_response_service):
    """A fresh manual report can repeat prior content without reopening its slot."""
    harness = live_response_service
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        original = allocated(harness)
        accepted(WeeklyDigestRecipient.objects.get(snapshot=original))
        before = history(original)
        command = uuid4()
        task = request(harness, principal, command)
        row = WeeklyDigestPreparation.objects.get(pk=command)
        assert row.phase == "capture" and row.occurrence_id == command
        assert request(harness, principal, command).run_id == task.run_id
        with task_login(ServiceRole.WORKER, exact=True):
            execute(task)
        snapshot = WeeklyDigestSnapshot.objects.get(preparation=row)
        recipient = WeeklyDigestRecipient.objects.get(snapshot=snapshot)
        assert snapshot.information == original.information
        assert recipient.covered_messages == []
        assert "Manual weekly" in recipient.subject
        assert "Current actionable requests" in recipient.text
        accepted(recipient)
        assert history(original).watermark == before.watermark
        assert request(harness, principal, command).run_id == task.run_id
        assert WeeklyManualRequest.objects.count() == 1
        assert (
            AuditEvent.objects.filter(
                event_type="weekly_manual_requested", subject_id=command
            ).count()
            == 1
        )


def test_manual_waits_for_unresolved_prior_work(live_response_service):
    """Explicit intent cannot bypass unresolved recipients or provider uncertainty."""
    with campaign_clock(INSTANT):
        allocated(live_response_service)
        before = TaskRun.objects.count()
        with pytest.raises(DatabaseError, match="resolved prior work"):
            request(live_response_service, user("admin@example.org"), uuid4())
        assert TaskRun.objects.count() == before
        assert not WeeklyManualRequest.objects.exists()


def test_manual_delivery_does_not_consume_regular_information_or_corrections(
    live_response_service,
):
    """A new manual snapshot is history, not the next scheduled success boundary."""
    harness = live_response_service
    with campaign_clock(datetime(2026, 10, 8, tzinfo=UTC)):
        original = allocated(harness)
        accepted(WeeklyDigestRecipient.objects.get(snapshot=original))
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "New request after scheduled success"
        respond_form(harness, form, answers)
        command = uuid4()
        task = request(harness, user("admin@example.org"), command)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(task)
        manual = WeeklyDigestSnapshot.objects.get(preparation_id=command)
        assert manual.submission_watermark == 2
        assert manual.corrections == [[original.information[0], "superseded"]]
        accepted(WeeklyDigestRecipient.objects.get(snapshot=manual))
        regular_history = history(original)
        assert regular_history.watermark == 1
        assert not regular_history.corrected
    with campaign_clock(datetime(2026, 10, 15, tzinfo=UTC)):
        claim, regular = replacement()
        publish(claim)
        recipient = WeeklyDigestRecipient.objects.get(snapshot=regular)
        assert regular.information == manual.information
        assert regular.corrections == manual.corrections
        assert recipient.information == manual.information
        assert recipient.corrections == manual.corrections
        assert recipient.covered_messages == []
        accepted(recipient)
        assert history(regular).watermark == 2


def test_testing_manual_records_empty_without_mail(response_service):
    """Rehearsal submission text cannot become live AdditionalInformationItems."""
    with campaign_clock(INSTANT):
        add_digest(
            response_service.service.store, response_service.campaign, weekly=True
        )
        configure_content(response_service)
        task = request(response_service, user("admin@example.org"), uuid4())
        with task_login(ServiceRole.WORKER, exact=True):
            execute(task)
        assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
        assert WeeklyDigestSnapshot.objects.get().information == []
        assert not WeeklyDigestRecipient.objects.exists()


@pytest.mark.parametrize("raw", [False, True])
def test_manual_command_rejects_nonadmin_independently(response_service, raw):
    """The SQL intent guard still checks Admin authority if Python is bypassed."""
    with campaign_clock(INSTANT):
        harness = response_service
        add_digest(harness.service.store, harness.campaign, weekly=True)
        configure_content(harness)
        principal, command = user("external@example.org"), uuid4()
        before = TaskRun.objects.count()
        with pytest.raises(DatabaseError if raw else PermissionError):
            if not raw:
                request(harness, principal, command)
            else:
                with task_login(ServiceRole.WEB, exact=True), work_transaction():
                    task = enqueue(
                        task_type="weekly_digest_prepare",
                        domain_request_id=command,
                        actor_id=principal.pk,
                        correlation_id=command,
                        idempotency_key=command,
                        admit=lambda *args: True,
                    )
                    WeeklyManualRequest.objects.create(
                        id=command,
                        campaign_id=harness.campaign.pk,
                        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
                        task_id=task.run_id,
                        actor_id=principal.pk,
                        correlation_id=command,
                    )
        assert TaskRun.objects.count() == before
        assert not WeeklyManualRequest.objects.exists()


def test_manual_history_survives_testing_cleanup_and_cannot_reopen(response_service):
    """Cleanup removes private captures, retaining the original command receipt."""
    harness = response_service
    with campaign_clock(INSTANT):
        add_digest(harness.service.store, harness.campaign, weekly=True)
        configure_content(harness)
        principal, command = user("admin@example.org"), uuid4()
        task = request(harness, principal, command)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(task)
        assert WeeklyDigestSnapshot.objects.exists()
        status = running_request(harness)
        for _ in range(status.inventory_total * 4):
            if not ProductionCleanupTarget.objects.filter(
                request_id=status.request_id
            ).exists():
                break
            delete_batch(status, maximum=2)
        assert not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists()
        assert not WeeklyDigestSnapshot.objects.exists()
        assert WeeklyManualRequest.objects.filter(pk=command).exists()
        assert request(harness, principal, command).run_id == task.run_id
        assert not WeeklyDigestSnapshot.objects.exists()
        with (
            pytest.raises(DatabaseError, match="immutable"),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM stewardship_weekly_manual_request WHERE id=%s", [command]
            )


@pytest.mark.parametrize("stale", [False, True])
def test_manual_form_requires_csrf_confirmation_and_reviewed_configuration(
    response_service, google, stale
):
    """A reviewed Testing form cannot silently authorize a changed mail policy."""
    from ..policy_factory import address
    from .campaign_builders import change

    harness = response_service
    with campaign_clock(INSTANT):
        add_digest(harness.service.store, harness.campaign, weekly=True)
        configure_content(harness)
        configuration = SystemConfiguration.objects.get().active_configuration_id
        browser, _ = signed_in()
        path = f"/admin/reports/weekly-digests/request/{harness.campaign.pk}/"
        values = {
            "command_id": str(uuid4()),
            "configuration_id": str(configuration),
            "acknowledge": "on",
        }
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
            assert (
                page.status_code == 200 and b"Queue new manual report" in page.content
            )
            assert browser.post(path, values).status_code == 403
            assert (
                browser.post(
                    path,
                    values | {"acknowledge": ""},
                    HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
                ).status_code
                == 400
            )
        if stale:
            store = harness.service.store
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "add",
                        "section": "login_rules",
                        **address("staff@example.org", roles=("staff",)),
                    }
                ],
            )
        before = TaskRun.objects.count()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for _ in range(2):
                response = browser.post(
                    path, values, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value
                )
                assert response.status_code == (409 if stale else 302)
                assert response["Cache-Control"] == "no-store"
        assert WeeklyManualRequest.objects.count() == (0 if stale else 1)
        assert TaskRun.objects.count() == before + (0 if stale else 1)
