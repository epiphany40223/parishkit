"""Send now of a report email, from Report emails' row action (#1786).

An explicit, separately audited, idempotent Administrator request: the
page's Send now (``report_email_views.report_email_send_now``) and
``report-email send-now`` call ``report_email_manual.request_send_now``,
whose database guard checks the report's current revision and that no
other send of it is still being prepared or delivered.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.reports.report_email_manual import request_send_now
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
    ReportEmailPreparation,
    ReportEmailSnapshot,
)

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import WEEKLY_INSTANT as INSTANT
from .report_builders import add_digest, execute, produce, settle_messages
from .report_builders import weekly_allocated as allocated
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def _weekly_definition(harness):
    """The campaign's one report (shaped as the converted Weekly)."""
    return ScheduleDefinition.objects.get(campaign=harness.campaign, kind="report")


def request(harness, principal, command):
    """Use the actual restricted Web identity, not schema-owner write access."""
    definition = _weekly_definition(harness)
    with task_login(ServiceRole.WEB, exact=True):
        return request_send_now(
            harness.service.store,
            principal.pk,
            harness.campaign.pk,
            definition.pk,
            definition.current_revision_id,
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
    (spec, "Send now"), and the row says so on GET; once it is delivered,
    Send now is allowed."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        allocated(harness)
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(reverse("admin:report_emails"))
        assert page.status_code == 200
        # The row's Send now is unavailable, with why, from the page's read.
        assert b"A send of this report is in progress" in page.content
        assert b'data-confirm-open="report-send-now-dialog"' not in page.content
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


def _send_now_values(definition, configuration):
    """The page's Send now post for ``definition``."""
    return {
        "report": str(definition.pk),
        "revision": str(definition.current_revision_id),
        "command_id": str(uuid4()),
        "configuration_id": str(configuration),
    }


@pytest.mark.parametrize("stale", [False, True])
def test_the_rows_send_now_requires_csrf_and_the_reviewed_revision(
    response_service, google, stale
):
    """A drawn Send now cannot silently send a report changed since."""
    harness = response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        configuration = SystemConfiguration.objects.get().active_configuration_id
        definition = _weekly_definition(harness)
        values = _send_now_values(definition, configuration)
        browser, _ = signed_in()
        path = reverse("admin:report_email_send_now")
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(reverse("admin:report_emails"))
            assert page.status_code == 200
            assert b'data-confirm-open="report-send-now-dialog"' in page.content
            assert (
                f'data-confirm-with="revision={definition.current_revision_id}"'.encode()
                in (page.content)
            )
            assert browser.post(path, values).status_code == 403
            assert (
                browser.post(
                    path,
                    values | {"extra": "x"},
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                ).status_code
                == 400
            )
        if stale:
            from .report_builders import edit_report

            edit_report(harness.service.store, definition.pk, subject="Changed")
        before = TaskRun.objects.count()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                path, values, HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value
            )
        assert response.status_code == (409 if stale else 302)
        if stale:
            refusal = response.json()["refusal"]
            assert "changed since this page was drawn" in refusal["message"]
        else:
            assert response["Location"] == (
                f"{reverse('admin:report_emails')}?requested={values['command_id']}"
            )
            with task_login(ServiceRole.WEB, exact=True, reconnect=True):
                page = browser.get(response["Location"])
            assert b"was requested." in page.content
        assert ReportEmailManualRequest.objects.count() == (0 if stale else 1)
        assert TaskRun.objects.count() == before + (0 if stale else 1)


def test_send_now_of_an_unknown_report_is_refused(response_service, google):
    """A post naming no report of the campaign records nothing (400)."""
    harness = response_service
    with campaign_clock(INSTANT):
        weekly(harness)
        configuration = SystemConfiguration.objects.get().active_configuration_id
        values = _send_now_values(_weekly_definition(harness), configuration)
        browser, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                reverse("admin:report_email_send_now"),
                values | {"report": str(uuid4())},
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 400
        assert not ReportEmailManualRequest.objects.exists()
