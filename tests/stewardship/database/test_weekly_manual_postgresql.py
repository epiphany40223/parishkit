"""Send a weekly report now: Send now of the converted Weekly report (#1786).

An explicit, separately audited, idempotent Administrator request. Report
emails replaced the Weekly digest: the page and ``digest weekly-request``
send the Weekly report through its Send now (``report_email_manual``),
whose database guard checks the report's current revision and that no other
send of it is still being prepared.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
    ReportEmailPreparation,
    ReportEmailSnapshot,
)
from parishkit.stewardship.reports.weekly_manual import request_manual_report

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import WEEKLY_INSTANT as INSTANT
from .report_builders import add_digest, execute, produce, settle_messages
from .report_builders import weekly_allocated as allocated
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user

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


def weekly(harness):
    """The converted Weekly report, ready to send now: its definition ID."""
    identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
    harness.campaign.refresh_from_db()
    if harness.campaign.state == "active":
        complete_empty_catchup(harness.campaign, uuid4())
    return identifier


def test_send_now_is_one_audited_send_and_moves_no_scheduled_slot(
    live_response_service,
):
    """A replay returns the same send; the scheduled sends stay as they were."""
    harness = live_response_service
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        scheduled = allocated(harness)
        # Send now waits for the scheduled send's email to be delivered.
        settle_messages()
        fulfilled = set(ScheduleFulfillment.objects.values_list("slot", "disposition"))
        command = uuid4()
        task = request(harness, principal, command)
        row = ReportEmailPreparation.objects.get(pk=command)
        assert row.phase == "inputs" and row.occurrence_id == command
        assert row.definition_id == scheduled.definition_id
        occurrence = ScheduleOccurrence.objects.get(pk=command)
        assert occurrence.slot == f"manual:{command}"
        assert request(harness, principal, command).run_id == task.run_id
        execute(task)
        assert ReportEmailPreparation.objects.get(pk=command).phase == "complete"
        snapshot = ReportEmailSnapshot.objects.get(preparation_id=command)
        assert "manual" in snapshot.subject
        assert ReportEmailManualRequest.objects.count() == 1
        assert (
            AuditEvent.objects.filter(
                event_type="report_email_manual_requested",
                subject_id=scheduled.definition_id,
            ).count()
            == 1
        )
        # Send now fulfills no scheduled slot and moves none.
        assert set(
            ScheduleFulfillment.objects.exclude(slot__startswith="manual:").values_list(
                "slot", "disposition"
            )
        ) == {item for item in fulfilled if not item[0].startswith("manual:")}


def test_send_now_waits_for_an_earlier_send_still_being_prepared(
    live_response_service,
):
    """Explicit intent cannot start a second send while one is open."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        assert produce()  # The scheduled send is allocated, not yet run.
        before = TaskRun.objects.count()
        with pytest.raises(DatabaseError, match="no open send"):
            request(harness, user("admin@example.org"), uuid4())
        assert TaskRun.objects.count() == before
        assert not ReportEmailManualRequest.objects.exists()


def test_send_now_waits_for_an_earlier_sends_email(live_response_service, google):
    """While a scheduled send's email is undelivered, Send now is refused
    (spec, "Send now"), and the page says so on GET; once it is delivered,
    Send now is allowed."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        allocated(harness)
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_manual")
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
        assert page.status_code == 200
        assert b"still being prepared or delivered" in page.content
        before = TaskRun.objects.count()
        with pytest.raises(DatabaseError, match="no open send"):
            request(harness, user("admin@example.org"), uuid4())
        assert TaskRun.objects.count() == before
        settle_messages()
        request(harness, user("admin@example.org"), uuid4())
        assert ReportEmailManualRequest.objects.count() == 1


@pytest.mark.parametrize("raw", [False, True])
def test_send_now_rejects_a_non_administrator_independently(response_service, raw):
    """The SQL intent guard still checks Admin authority if Python is bypassed."""
    with campaign_clock(INSTANT):
        harness = response_service
        definition = weekly(harness)
        principal, command = user("external@example.org"), uuid4()
        before = TaskRun.objects.count()
        with pytest.raises(DatabaseError if raw else PermissionError):
            if not raw:
                request(harness, principal, command)
            else:
                from parishkit.stewardship.campaigns.schedule_models import (
                    ScheduleDefinition,
                )

                revision = ScheduleDefinition.objects.get(
                    pk=definition
                ).current_revision_id
                with task_login(ServiceRole.WEB, exact=True), work_transaction():
                    task = enqueue(
                        task_type="report_email_prepare",
                        domain_request_id=command,
                        actor_id=principal.pk,
                        correlation_id=command,
                        idempotency_key=command,
                        admit=lambda *args: True,
                    )
                    ReportEmailManualRequest.objects.create(
                        id=command,
                        campaign_id=harness.campaign.pk,
                        definition_id=definition,
                        revision_id=revision,
                        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
                        task_id=task.run_id,
                        actor_id=principal.pk,
                        correlation_id=command,
                    )
        assert TaskRun.objects.count() == before
        assert not ReportEmailManualRequest.objects.exists()


def test_a_send_now_request_is_immutable(response_service):
    """The command's receipt stays as it was recorded."""
    harness = response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        command = uuid4()
        request(harness, user("admin@example.org"), command)
        with (
            pytest.raises(DatabaseError, match="immutable"),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM stewardship_report_email_manual_request WHERE id=%s",
                [command],
            )


@pytest.mark.parametrize("stale", [False, True])
def test_the_form_requires_csrf_confirmation_and_reviewed_configuration(
    response_service, google, stale
):
    """A reviewed form cannot silently authorize a changed configuration."""
    harness = response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        configuration = SystemConfiguration.objects.get().active_configuration_id
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_manual")
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
            # The trail is the way back (navigation rule 3), not a link of
            # its own (#521).
            emailed = reverse("admin:emailed_reports")
            assert f'<li><a href="{emailed}">Emailed reports</a></li>'.encode() in (
                page.content
            )
            assert browser.post(path, values).status_code == 403
            assert (
                browser.post(
                    path,
                    values | {"acknowledge": ""},
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                ).status_code
                == 400
            )
        if stale:
            # Any applied change makes the reviewed configuration stale.
            from .report_builders import edit_report

            store = harness.service.store
            weekly_id = next(
                row["id"]
                for row in store.active().document()["sections"]["schedules"]
                if row["values"]["kind"] == "report"
            )
            edit_report(store, weekly_id, subject="Changed")
        before = TaskRun.objects.count()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            for _ in range(2):
                response = browser.post(
                    path,
                    values,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                )
                assert response.status_code == (409 if stale else 302)
                assert response["Cache-Control"] == "no-store"
        if stale:
            # The refusal links the pages it names (navigation rule 6, #521).
            for name, label in (
                ("background", "Background work"),
                ("deliveries", "Sent mail"),
            ):
                link = f'<a href="{reverse(f"admin:{name}")}">{label}</a>'
                assert link.encode() in response.content
        assert ReportEmailManualRequest.objects.count() == (0 if stale else 1)
        assert TaskRun.objects.count() == before + (0 if stale else 1)


def test_the_form_explains_a_missing_weekly_report(response_service, google):
    """Without a Weekly report the page says so instead of refusing blindly."""
    with campaign_clock(INSTANT):
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_manual")
        before = TaskRun.objects.count()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
        assert page.status_code == 409
        assert b"This campaign has no Weekly report" in page.content
        # Queue is in view but unavailable, named by why; no form (#1331).
        assert (
            b'disabled aria-describedby="manual-no-schedule">'
            b"Queue new manual report</button>"
        ) in page.content
        assert b'name="acknowledge"' not in page.content
        assert ReportEmailManualRequest.objects.count() == 0
        assert TaskRun.objects.count() == before


def test_the_form_shows_queue_unavailable_while_a_send_is_being_prepared(
    live_response_service, google
):
    """An earlier send still being prepared is found on GET, as the guard
    would refuse it: Queue is shown unavailable with why, and nothing runs."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        assert produce()
        browser, _ = signed_in()
        path = reverse("admin:weekly_digest_manual")
        before = TaskRun.objects.count()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(path)
        assert page.status_code == 200
        assert b"an earlier send of it is still being prepared" in page.content
        assert b'disabled aria-describedby="manual-unavailable"' in page.content
        assert b'name="acknowledge"' not in page.content
        assert TaskRun.objects.count() == before
        assert not ReportEmailManualRequest.objects.exists()
