"""Report emails through the shared delivery machinery (#1786, delivery item 4).

Report emails replaced the Daily and Weekly digests, so the shared machinery
the digest suites proved now carries report messages: an Administrator's
resolution of an uncertain or failed message (including confirming an
unknown one unsent), a revoked Administrator's
cancelled message, the retry of a failed preparation and the Testing
cleanup. Each send is made by the real report producer and worker
(``report_builders``) and dispatched under the exact mail dispatch role.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.campaigns.cleanup_catalog import iter_inventory
from parishkit.stewardship.campaigns.production_models import ProductionCleanupTarget
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxRender
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.models import CampaignFactPin
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailPreparation,
    ReportEmailRecipient,
    ReportEmailSnapshot,
)
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import (
    DAILY_INSTANT,
    allocated,
    begin,
    execute,
    fail_preparation,
    messages,
    revoke_admin,
    run_finalizers,
    set_user,
    users_form,
)
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_delivery_resolution_postgresql import (
    confirmed_unsent,
    resolve,
    retry_admitted,
    unknown_inventory,
)
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)
ADMIN = "admin@example.org"
SECOND = "second@example.org"


def submit(message, status):
    """Record one provider outcome for ``message`` under the mail dispatch role.

    With ``status`` None the message is claimed and never submitted. The
    claimed task is then drained, as an exhausted delivery task is.
    """
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        if status is not None:
            assert begin(message, execution) is not None
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    return message


def occurrence_of(preparation):
    """The report send's occurrence, read fresh."""
    return ScheduleOccurrence.objects.get(pk=preparation.occurrence_id)


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize(
    "action,status",
    [
        ("accept", Status.UNKNOWN),
        ("resend", Status.UNKNOWN),
        ("retry_failed", Status.PERMANENT),
        ("retry_unsent", None),
    ],
)
def test_an_administrator_resolves_a_report_message_and_its_retry_resends_it(
    family_mail,  # noqa: F811
    production,
    action,
    status,
):
    """Acceptance records evidence; a retry sends the frozen report again.

    The resolution is keyless (no Family key or portal link), the failed
    delivery task keeps its history, and a retry is a new attempt of the
    same message with the same compiled report.
    """
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    principal = user(ADMIN)
    with campaign_clock(DAILY_INSTANT):
        preparation = allocated(family_mail)
        message = submit(messages().get(), status)
        version, attempt = message.version, message.attempt
        command = resolve(
            family_mail, principal, message, action, general=None, public=None
        )
        message.refresh_from_db()
        assert command.preparation is None
        assert message.sealed_substitutions is None
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        # A replay of the same command returns it and changes nothing.
        assert (
            resolve(
                family_mail,
                principal,
                message,
                action,
                command_id=command.pk,
                expected_version=version,
                general=None,
                public=None,
            ).pk
            == command.pk
        )
        if action == "accept":
            assert message.state == "delivered" and command.retry_task_id is None
        else:
            assert message.state == "pending" and message.attempt == attempt
            snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(SimpleNamespace(task_id=command.retry_task_id))
                mail, _, _, number = begin(message, execution)
                assert number == attempt + 1
                assert mail.html.endswith(snapshot.html)
                assert mail.recipients == (
                    (ADMIN,) if production else ("test@example.org",)
                )
                finish_submission(
                    message.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )
        run_finalizers()
        occurrence = occurrence_of(preparation)
        assert occurrence.state == "succeeded"
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "delivered"
        )


def test_an_administrator_confirms_an_unknown_report_message_unsent(
    family_mail,  # noqa: F811
):
    """Confirming an unknown report message unsent settles its uncertainty.

    The unknown-delivery resolve path ends the message as a definitive
    non-acceptance with the Administrator's evidence, so the unknown count
    falls to zero and a replay returns the same command. The recipient is
    still an Administrator, so the failure keeps the send open for an
    admitted retry instead of completing it. Production mode, because the
    unknown count the Admin resume guard reads is Production's.
    """
    family_mail = activate_response_service(family_mail)
    complete_empty_catchup(family_mail.campaign, uuid4())
    principal = user(ADMIN)
    with campaign_clock(DAILY_INSTANT):
        preparation = allocated(family_mail)
        message = submit(messages().get(), Status.UNKNOWN)
        version = message.version
        assert unknown_inventory(family_mail.campaign) == 1
        command = resolve(
            family_mail,
            principal,
            message,
            "confirm_unsent",
            general=None,
            public=None,
        )
        confirmed_unsent(message, command)
        assert message.version == version + 1
        assert unknown_inventory(family_mail.campaign) == 0
        replay = resolve(
            family_mail,
            principal,
            message,
            "confirm_unsent",
            command_id=command.pk,
            expected_version=version,
            general=None,
            public=None,
        )
        assert replay.pk == command.pk
        run_finalizers()
        occurrence = occurrence_of(preparation)
        assert occurrence.state == "pending"
        assert not ScheduleFulfillment.objects.filter(
            occurrence=occurrence, slot=occurrence.slot
        ).exists()
        assert retry_admitted(message) is True


@pytest.mark.parametrize("accepted_first", [False, True])
def test_a_revoked_administrators_report_is_cancelled_without_a_provider_attempt(
    family_mail,  # noqa: F811
    accepted_first,
):
    """A removed Administrator's pending message is cancelled at admission.

    Its message never opens a provider attempt; the other Administrator's
    accepted message alone completes the send, whose occurrence succeeds
    with a delivered fulfillment, whichever is decided first.
    """
    store = family_mail.service.store
    users_form(store)
    set_user(store, SECOND, "administrator", name="Second, Admin")
    with campaign_clock(DAILY_INSTANT):
        preparation = allocated(family_mail)
        by_address = {
            row.address: row.outbox
            for row in ReportEmailRecipient.objects.select_related("outbox")
        }
        message, remaining = by_address[SECOND], by_address[ADMIN]

        def deliver_remaining():
            """Accept the remaining Administrator's message."""
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(remaining)
                assert begin(remaining, execution) is not None
                finish_submission(
                    remaining.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )

        if accepted_first:
            deliver_remaining()
        revoke_admin(family_mail, SECOND)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is None
        message.refresh_from_db()
        assert message.state == "cancelled" and message.reason == "recipient_revoked"
        assert message.attempt == 0
        if not accepted_first:
            deliver_remaining()
        run_finalizers()
        occurrence = occurrence_of(preparation)
        assert occurrence.state == "succeeded"
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "delivered"
        )
        assert [row.state for row in messages()].count("delivered") == 1


def test_a_failed_report_preparation_is_retried_from_background_work(
    family_mail,  # noqa: F811
    google,
):
    """The Background work page retries a failed report preparation once.

    The retry needs the page's CSRF token, replays to the same retry for
    the same command, and a newer command or a retry of the retry is
    refused. The retry keeps the original preparation and, run by the real
    worker, makes the send's message.
    """
    browser, _ = signed_in()
    with campaign_clock(DAILY_INSTANT):
        status = fail_preparation(family_mail)
        path = f"/admin/system/background/{status.run_id}/report-email-retry/"
        page_path = f"/admin/system/background/{status.run_id}/"
        values = {"command_id": str(uuid4())}
        csrf = {"HTTP_X_CSRFTOKEN": browser.cookies["pk_admin_csrf"].value}
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(page_path)
            assert page.status_code == 200 and b"report-email-retry/" in page.content
            assert browser.post(path, values).status_code == 403
            for _ in range(2):
                assert browser.post(path, values, **csrf).status_code == 302
            assert b"report-email-retry/" not in browser.get(page_path).content
            retry = TaskRun.objects.get(parent_id=status.run_id)
            again = f"/admin/system/background/{retry.pk}/report-email-retry/"
            assert browser.post(again, values, **csrf).status_code == 409
            fresh = {"command_id": str(uuid4())}
            assert browser.post(path, fresh, **csrf).status_code == 409
        assert TaskRun.objects.get(pk=status.run_id).state == "failed"
        assert TaskRun.objects.filter(root_id=status.root_id).count() == 2
        assert retry.domain_request_id == status.domain_request_id
        execute(_status(retry))
        preparation = ReportEmailPreparation.objects.get(task_id=status.run_id)
        assert preparation.phase == "complete"
        assert [message.state for message in messages()] == ["pending"]


@pytest.mark.parametrize("outcome", [Status.ACCEPTED, Status.PERMANENT])
def test_testing_cleanup_removes_a_report_sends_inputs(response_service, outcome):
    """Testing cleanup deletes the snapshot, recipients, messages and pins.

    The Python and SQL inventories agree on the report's rows; deletion in
    two-row batches removes them and the snapshot's fact and source pins,
    and keeps the opaque preparation and the shared fact generations.
    """
    from parishkit.stewardship.reports.models import CampaignDailyFactSet

    harness = response_service
    with campaign_clock(DAILY_INSTANT):
        preparation = allocated(harness)
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        message = messages().get()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is not None
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(outcome, 1)
            )
        facts = set(CampaignDailyFactSet.objects.values_list("pk", flat=True))
        with work_transaction(), connection.cursor() as cursor:
            targets = {
                (item.category.value, item.identifier)
                for item in iter_inventory(harness.campaign.pk)
            }
            cursor.execute(
                "SELECT category,target_id FROM stewardship_cleanup_inventory_v1(%s)",
                [harness.campaign.pk],
            )
            assert targets == set(cursor.fetchall())
        assert ("report_email_snapshots", snapshot.pk) in targets
        recipient = ReportEmailRecipient.objects.get(snapshot=snapshot)
        assert ("report_email_recipients", recipient.pk) in targets
        status = running_request(harness)
        # Several dependency sweeps are allowed, but a broken selector must
        # fail quickly rather than turn into an indefinitely hanging test.
        for _ in range(status.inventory_total * 4):
            if not ProductionCleanupTarget.objects.filter(
                request_id=status.request_id
            ).exists():
                break
            assert delete_batch(status, maximum=2).deleted_count <= 2
        assert not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists()
    assert not ReportEmailSnapshot.objects.exists()
    assert not ReportEmailRecipient.objects.exists()
    assert not messages().exists()
    assert not OutboxRender.objects.exists()
    assert not SourceSnapshotPin.objects.filter(
        parent_kind="digest", parent_id=preparation.pk
    ).exists()
    assert not CampaignFactPin.objects.filter(
        parent_kind="digest", parent_id=preparation.pk
    ).exists()
    assert set(CampaignDailyFactSet.objects.values_list("pk", flat=True)) == facts
    assert ReportEmailPreparation.objects.get(pk=preparation.pk).phase == "complete"
