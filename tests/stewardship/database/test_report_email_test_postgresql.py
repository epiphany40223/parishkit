# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.
"""Send a test to me of a report email, on the restricted logins (#2036).

The report-emails spec's "Send a test to me": the Report emails row action,
the editor's button and ``report-email send-test`` draw the report as
Preview does and queue one ``ReportEmailTest``; the worker makes its one
``admin_report_test`` message and mail dispatch sends the kept email to the
Administrator (the Testing recipient in Testing). It never counts as a
send: no occurrence, fulfillment, preparation, snapshot or manual request.
One test per Administrator and report may be pending; once its message is
settled the database settles the test and scrubs its email. Frozen file
0173's DO block is checked against the bodies it replaces.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
    ReportEmailPreparation,
    ReportEmailSnapshot,
    ReportEmailTest,
)
from parishkit.stewardship.reports.report_email_test import (
    TASK_TYPE,
    label_line,
    report_email_test_handler,
)
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .report_builders import WEEKLY_INSTANT, add_digest, begin, set_user
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import resolve
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user
from .test_report_email_editor_postgresql import started
from .test_report_email_preview_postgresql import FIRST_SEND, daily_sent
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "report_email_test_sent"
ADMIN = "admin@example.org"
# A Daily-shaped report added on the campaign's first day (October 1, 2054,
# New York time) has figures for that day after midnight.
ADDED = datetime(2054, 10, 1, 14, tzinfo=UTC)
MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0173_report_email_test.sql"


def sends():
    """The rows a test must never write: every send record of a report."""
    return {
        "occurrences": ScheduleOccurrence.objects.count(),
        "fulfillments": ScheduleFulfillment.objects.count(),
        "manual_requests": ReportEmailManualRequest.objects.count(),
        "preparations": ReportEmailPreparation.objects.count(),
        "snapshots": ReportEmailSnapshot.objects.count(),
        "report_messages": OutboxMessage.objects.filter(purpose="admin_report").count(),
    }


def post_test(browser, report):
    """Post a row's Send a test to me as the page script does."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            reverse("admin:report_email_test"),
            {"report": str(report)},
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            HTTP_ACCEPT="application/json",
        )


def run_test_task(row):
    """Run the test's worker task with the worker's real maintained lifetime."""
    owner = report_email_test_handler()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            row.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)


def deliver(message, status=Status.ACCEPTED):
    """Submit the test's message through the real mail dispatch: its mail."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        prepared = begin(message, execution)
        assert prepared is not None
        finish_submission(message.pk, execution.claim, FamilyDeliveryResult(status, 1))
    return prepared[0]


def daily(family_mail):
    """A Daily-shaped report of the campaign, with no figures yet: its id."""
    store, campaign = family_mail.service.store, family_mail.campaign
    with campaign_clock(ADDED):
        return add_digest(store, campaign)


def test_a_test_is_one_message_to_its_administrator_and_never_a_send(
    family_mail, google
):
    """The row's test queues one audited request and task; a second waits
    until the first is settled; the worker makes one admin_report_test
    message with the labeled email; dispatch sends exactly it; delivery
    settles and scrubs the test; and no send record is ever written."""
    # Its first send built the day's figures, so the test draws them.
    identifier, _snapshot = daily_sent(family_mail)
    with campaign_clock(FIRST_SEND):
        browser, _ = signed_in()
        before = sends()
        answer = post_test(browser, identifier)
        assert answer.status_code == 200, answer.content[:400]
        assert answer["Cache-Control"] == "no-store"
        assert json.loads(answer.content) == {"queued": True, "updating": False}
        row = ReportEmailTest.objects.get()
        system = SystemConfiguration.objects.get()
        assert (row.state, row.definition_id, row.address) == (
            "queued",
            identifier,
            ADMIN,
        )
        assert row.mode == system.mode
        assert row.images and row.images[0][0] == "participation@parishkit"
        task = TaskRun.objects.get(pk=row.task_id)
        assert (task.task_type, task.state, task.domain_request_id) == (
            TASK_TYPE,
            "queued",
            row.pk,
        )
        event = AuditEvent.objects.get(event_type=EVENT)
        assert event.subject_id == identifier
        context = AuditContext.objects.get(event_id=event.pk).context
        assert context == {"outcome": "succeeded", "report_mode": system.mode}
        # One pending test per Administrator and report: refused, unwritten.
        again = post_test(browser, identifier)
        assert again.status_code == 409, again.content[:400]
        assert "already on its way to you" in again.content.decode()
        assert ReportEmailTest.objects.count() == 1
        assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
        run_test_task(row)
        assert TaskRun.objects.get(pk=row.task_id).state == "succeeded"
        message = OutboxMessage.objects.get(purpose="admin_report_test")
        assert (message.semantic_key, message.family_id, message.state) == (
            row.pk,
            None,
            "pending",
        )
        assert message.credential_namespace == "none"
        assert message.campaign_id == family_mail.campaign.pk
        render = OutboxRender.objects.get(pk=message.render_id)
        testing = system.mode == "testing"
        line = label_line(row.mode, ADMIN, row.testing_recipient)
        assert render.intended_recipients == [ADMIN]
        assert render.routed_recipients == [
            system.testing_recipient if testing else ADMIN
        ]
        assert render.subject == "[TEST] " + row.subject
        assert render.text == line + "\n\n" + row.text
        assert render.html.endswith(row.html)
        mail = deliver(message)
        assert mail.subject == render.subject
        assert mail.recipients == tuple(render.routed_recipients)
        # The chart travels with it, as a send's does.
        assert mail.chart.startswith(b"\x89PNG")
        assert OutboxMessage.objects.get(pk=message.pk).state == "delivered"
        settled = ReportEmailTest.objects.get(pk=row.pk)
        assert settled.state == "settled"
        assert (settled.subject, settled.html, settled.text, settled.images) == (
            "",
            "",
            "",
            [],
        )
        assert sends() == before
        # Settled, it no longer counts: another test may be sent.
        assert post_test(browser, identifier).status_code == 200
        assert ReportEmailTest.objects.filter(state="queued").count() == 1


def weekly_started(harness):
    """A live campaign's Weekly-shaped report and its Administrator: (id, browser)."""
    browser = started(harness)
    identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
    return identifier, browser


def test_a_production_test_goes_to_the_administrator_alone(
    live_response_service, google
):
    """In Production the test goes to the Administrator's own address under
    "[TEST] ", with no image for a report with no chart; it is no send of
    the report, and a delivery whose outcome is unknown keeps it pending
    until an Administrator settles it from evidence (never by a resend)."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        assert SystemConfiguration.objects.get().mode == "production"
        before = sends()
        answer = post_test(browser, identifier)
        assert answer.status_code == 200, answer.content[:400]
        row = ReportEmailTest.objects.get()
        assert (row.mode, row.testing_recipient, row.images) == ("production", None, [])
        run_test_task(row)
        message = OutboxMessage.objects.get(purpose="admin_report_test")
        assert message.routing == "production"
        mail = deliver(message, Status.UNKNOWN)
        assert mail.recipients == (ADMIN,)
        assert mail.subject.startswith("[TEST] ")
        message.refresh_from_db()
        assert message.state == "delivery_unknown"
        # Sent mail lists it as a report email test, and its page offers
        # only the settlements from evidence, never Send again.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            listed = browser.get(reverse("admin:deliveries"))
            shown = browser.get(reverse("admin:delivery", args=[message.pk]))
        assert listed.status_code == 200 and b"Report email test" in listed.content
        assert shown.status_code == 200, shown.content[:400]
        # Unknown is still pending: the next test waits for it.
        assert post_test(browser, identifier).status_code == 409
        assert ReportEmailTest.objects.get().state == "queued"
        # The resolution settles a test from evidence only, never resends it.
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        message.refresh_from_db()
        principal = user(ADMIN)
        for refused in ("resend", "retry_unsent", "retry_failed"):
            with pytest.raises((PermissionError, StaleRecordError, DatabaseError)):
                resolve(harness, principal, message, refused)
        resolve(harness, principal, message, "accept")
        assert OutboxMessage.objects.get(pk=message.pk).state == "delivered"
        assert ReportEmailTest.objects.get().state == "settled"
        assert sends() == before


def test_the_editor_sends_the_unsaved_form_and_refuses_by_field(
    live_response_service, google
):
    """The editor's Send a test to me posts the unsaved form (test=yes): one
    test of a new report (no report id), nothing saved; a refused field is
    a 400 naming it; Staff are refused."""
    from .test_report_email_editor_postgresql import fields, post

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        version = SystemConfiguration.objects.get().active_configuration_id
        answer = post(admin, path, fields(test="yes", name="Unsaved report"))
        assert answer.status_code == 200, answer.content[:400]
        assert json.loads(answer.content)["queued"] is True
        row = ReportEmailTest.objects.get()
        assert row.definition_id is None and "Unsaved report" in row.subject
        assert SystemConfiguration.objects.get().active_configuration_id == version
        event = AuditEvent.objects.get(event_type=EVENT)
        assert event.subject_id == harness.campaign.pk
        # One pending test of an unsaved form, too (NULLS NOT DISTINCT).
        assert post(admin, path, fields(test="yes")).status_code == 409
        assert post_test(admin, uuid4()).status_code == 400
        refused = post(admin, path, fields(test="yes", name="", block=[]))
        assert refused.status_code == 400
        problems = json.loads(refused.content)["errors"]
        assert {problem["field"] for problem in problems} >= {"Report name"}
        assert ReportEmailTest.objects.count() == 1
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        assert post(staff, path, fields(test="yes")).status_code == 403
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        assert post_test(staff, identifier).status_code == 403
        assert ReportEmailTest.objects.count() == 1


def test_the_guard_admits_only_the_webs_exact_request(live_response_service, google):
    """Web may insert only its Administrator's own address and task; no login
    may change or delete a test, and one not over cannot be settled."""
    from parishkit.stewardship.jobs.storage import enqueue

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        assert post_test(browser, identifier).status_code == 200
        row = ReportEmailTest.objects.get()
        request_id = uuid4()

        def insert(**changes):
            """Insert a copy of the test as web, with ``changes``."""
            with (
                task_login(ServiceRole.WEB, exact=True),
                work_transaction(),
            ):
                task = enqueue(
                    task_type=TASK_TYPE,
                    domain_request_id=request_id,
                    actor_id=row.requested_by_id,
                    correlation_id=request_id,
                    idempotency_key=request_id,
                    admit=lambda *args: True,
                )
                values = dict(
                    id=request_id,
                    campaign_id=row.campaign_id,
                    configuration_id=row.configuration_id,
                    definition_id=None,
                    requested_by_id=row.requested_by_id,
                    mode=row.mode,
                    testing_recipient=None,
                    address=ADMIN,
                    subject="Report",
                    html="<p>Report</p>",
                    text="Report",
                    images=[],
                    task_id=task.run_id,
                    actor_id=row.requested_by_id,
                    correlation_id=request_id,
                )
                ReportEmailTest.objects.create(**(values | changes))

        for changes in (
            {"address": "someone@example.org"},
            {"mode": "testing", "testing_recipient": "testing@example.org"},
            {"definition_id": uuid4()},
            {"subject": ""},
            {"images": [["chart", "bm90IGEgcG5n"]]},
        ):
            with pytest.raises(DatabaseError, match="current Administrator"):
                insert(**changes)
        settle = (
            "UPDATE stewardship_report_email_test SET state='settled',"
            "subject='',html='',text='',images='[]',version=version+1"
        )
        with connection.cursor() as cursor:
            with (
                pytest.raises(DatabaseError, match="that is over"),
                transaction.atomic(),
            ):
                cursor.execute(settle)
            with pytest.raises(DatabaseError, match="retained"), transaction.atomic():
                cursor.execute("DELETE FROM stewardship_report_email_test")
        for role in (ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH):
            with (
                task_login(role, exact=True),
                pytest.raises(DatabaseError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "UPDATE stewardship_report_email_test SET state='settled'"
                )
        assert ReportEmailTest.objects.get().state == "queued"


def test_a_lost_scope_cancels_the_test_and_settles_it(live_response_service, google):
    """A test whose Administrator lost the role before its message was made
    is cancelled by its task, and the database settles and scrubs it."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        assert post_test(browser, identifier).status_code == 200
        row = ReportEmailTest.objects.get()
        store = harness.service.store
        set_user(store, "second@example.org", "administrator", name="Second, Admin")
        set_user(store, ADMIN, "staff")
        run_test_task(row)
        assert TaskRun.objects.get(pk=row.task_id).state == "cancelled"
        assert not OutboxMessage.objects.filter(purpose="admin_report_test").exists()
        settled = ReportEmailTest.objects.get()
        assert (settled.state, settled.html) == ("settled", "")


def test_paused_production_mail_refuses_a_test(live_response_service, google):
    """While Production mail is paused, no test is queued (it would wait)."""
    from parishkit.stewardship.campaigns.models import Campaign

    from .auth_builders import unguarded

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        with unguarded():
            # A pause's evidence, as Pause and resume mail records it.
            Campaign.objects.filter(pk=harness.campaign.pk).update(
                delivery_paused=True,
                pause_actor_id=uuid4(),
                pause_version=1,
                paused_at=WEEKLY_INSTANT,
            )
        answer = post_test(browser, identifier)
        assert answer.status_code == 409, answer.content[:400]
        refusal = json.loads(answer.content)
        assert refusal["errors"][0]["code"] == "not_available"
        assert refusal["refusal"]["message"].startswith("Mail is paused")
        assert not ReportEmailTest.objects.exists()


def test_send_test_is_the_rows_test(live_response_service, google, cli, monkeypatch):
    """``report-email send-test`` queues the row's test, keyed: a repeated key
    returns it unchanged, even one racing the first; another while it is
    pending is stale_version; an unknown report is not_available; a
    read-only session is denied."""
    from parishkit.stewardship.reports import report_email_test

    from .test_admin_report_email_cli_postgresql import read_only_session

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, _browser = weekly_started(harness)
        run = cli()
        key = uuid4()
        argv = ("report-email", "send-test", str(identifier), "--request-key", str(key))
        code, document = run(*argv)
        assert code == 0, document
        result = document["result"]
        assert result["created"] and result["id"] == str(key)
        assert result["recipient"] == "self"
        assert result["subject"].startswith("[TEST] ")
        row = ReportEmailTest.objects.get(pk=key)
        assert result["task"]["id"] == str(row.task_id)
        assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
        code, document = run(*argv)
        assert code == 0 and document["result"]["created"] is False
        assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
        # A replay racing the first call misses it before the lock and
        # finds it pending under the lock: still that test, not stale.
        lookups = []
        real = report_email_test._replayed

        def racing(*args):
            """Miss the first lookup, as a call racing the first would."""
            lookups.append(args)
            return None if len(lookups) == 1 else real(*args)

        monkeypatch.setattr(report_email_test, "_replayed", racing)
        code, document = run(*argv)
        assert code == 0 and document["result"]["created"] is False, document
        assert len(lookups) == 2
        monkeypatch.setattr(report_email_test, "_replayed", real)
        assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
        code, document = run("report-email", "send-test", str(identifier))
        assert code == 1 and document["error"]["code"] == "stale_version", document
        code, document = run("report-email", "send-test", str(uuid4()))
        assert code == 1 and document["error"]["code"] == "not_available", document
        reader = read_only_session(run.service)
        code, document = run(*argv[:3], secret=reader)
        assert code == 1 and document["error"]["code"] == "denied"
        assert ReportEmailTest.objects.count() == 1


def unresolved(family_mail):
    """The Testing messages go-live's Production cleanup is blocked by now."""
    from parishkit.stewardship.campaigns.cleanup_preview import cleanup_preview

    with work_transaction():
        return cleanup_preview(family_mail.campaign.pk).unresolved


def queued_message(family_mail, browser, identifier):
    """Queue a Testing test of ``identifier`` and make its message.

    Returns ``(row, message, before)``, ``before`` the cleanup's unresolved
    count without the test (the report's own first send is still pending);
    the unsent test's message adds one.
    """
    assert SystemConfiguration.objects.get().mode == "testing"
    before = unresolved(family_mail)
    assert post_test(browser, identifier).status_code == 200
    row = ReportEmailTest.objects.get(state="queued")
    run_test_task(row)
    assert unresolved(family_mail) == before + 1
    message = OutboxMessage.objects.get(
        purpose="admin_report_test", semantic_key=row.pk
    )
    return row, message, before


def assert_settled_and_unblocked(family_mail, browser, identifier, queued):
    """The cancelled test is settled and scrubbed, and blocks nothing after it.

    Its message is cancelled as not sent, the next test is admitted, and
    Testing's go-live Production cleanup is no longer blocked by it.
    """
    row, message, before = queued
    message.refresh_from_db()
    assert (message.state, message.reason) == ("cancelled", "preparation_failed")
    assert message.attempt == 0
    assert TaskRun.objects.get(pk=message.task_id).state == "cancelled"
    settled = ReportEmailTest.objects.get(pk=row.pk)
    assert (settled.state, settled.subject, settled.html, settled.images) == (
        "settled",
        "",
        "",
        [],
    )
    assert unresolved(family_mail) == before
    assert post_test(browser, identifier).status_code == 200
    assert ReportEmailTest.objects.filter(state="queued").count() == 1


def test_a_test_that_cannot_be_prepared_is_cancelled_and_settled(
    family_mail, google, monkeypatch
):
    """A test whose delivery fails preparation on its last attempt is
    cancelled as not sent, as a chosen-Family test is: never left pending,
    which would refuse every later test and block Testing cleanup."""
    from parishkit.stewardship.jobs import family_mail_delivery_tasks
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DISPATCH

    identifier, _snapshot = daily_sent(family_mail)
    with campaign_clock(FIRST_SEND):
        browser, _ = signed_in()
        queued = queued_message(family_mail, browser, identifier)
        message = queued[1]

        def broken(*args, **kwargs):
            """A synthetic failure before anything is handed to the provider."""
            raise RuntimeError("synthetic preparation failure")

        monkeypatch.setattr(family_mail_delivery_tasks, "read_private", broken)
        monkeypatch.setattr(family_mail_delivery_tasks, "MAX_ATTEMPTS", 1)
        owner = delivery_handler(
            family_mail.service.store,
            private=family_mail.rings.private,
            public_origin="https://parish.example.org",
            credential_path=Path("/unused"),
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            execution = claim_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={DISPATCH: owner},
            )
            with maintain_execution(execution):
                owner.execute(execution)
        assert_settled_and_unblocked(family_mail, browser, identifier, queued)


def test_an_abandoned_test_delivery_is_cancelled_once_its_attempts_are_spent(
    family_mail, google, monkeypatch
):
    """Recovery retries an abandoned test delivery until its five attempts
    are spent, then cancels the unsent message (frozen file 0173's
    abandoned branch admits exactly that), which settles the test."""
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        MAX_ATTEMPTS,
        delivery_handler,
    )

    from .test_family_mail_test_postgresql import abandon_until_settled

    identifier, _snapshot = daily_sent(family_mail)
    with campaign_clock(FIRST_SEND):
        browser, _ = signed_in()
        queued = queued_message(family_mail, browser, identifier)
        message = queued[1]
        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_delivery_tasks."
            "RECOVERY_RETRY_SECONDS",
            1,
        )
        owner = delivery_handler(None, credential_path=Path("/unused"))
        assert abandon_until_settled(message, owner) == MAX_ATTEMPTS == 5
        assert TaskRun.objects.get(pk=message.task_id).attempt >= 5
        assert_settled_and_unblocked(family_mail, browser, identifier, queued)


def test_a_campaign_not_sending_in_its_mode_refuses_and_cancels_a_test(
    live_response_service, google
):
    """A Production campaign that is not scheduled, active or closed sends
    no report email, so no test either, as the database's live check
    decides: the worker cancels a queued test at once and the page refuses
    a new one up front."""
    from parishkit.stewardship.campaigns.models import Campaign

    from .auth_builders import unguarded

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        assert post_test(browser, identifier).status_code == 200
        row = ReportEmailTest.objects.get()
        with unguarded():
            Campaign.objects.filter(pk=harness.campaign.pk).update(state="draft")
        run_test_task(row)
        assert TaskRun.objects.get(pk=row.task_id).state == "cancelled"
        assert not OutboxMessage.objects.filter(purpose="admin_report_test").exists()
        assert ReportEmailTest.objects.get().state == "settled"
        answer = post_test(browser, identifier)
        assert answer.status_code == 409, answer.content[:400]
        refusal = json.loads(answer.content)
        assert refusal["errors"][0]["code"] == "not_available"
        assert refusal["refusal"]["message"] == (
            "This campaign is not sending report emails now, so no test was sent."
        )
        assert ReportEmailTest.objects.count() == 1


def earlier(file, name):
    """``name``'s CREATE OR REPLACE text in a frozen file."""
    text = (MIGRATIONS / file).read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    tag = re.search(r" AS (\$[A-Za-z_]*\$)", text[start:])[1]
    body = text.index(tag, start + text[start:].index(" AS ") + 4) + len(tag)
    return text[start : text.index(tag + ";", body) + len(tag) + 1]


# The shared functions 0173 replaces, each last replaced by 0161.
REPLACED = (
    "stewardship_task_type_creators_v1",
    "stewardship_task_type_login_v1",
    "stewardship_family_mail_outbox_write_v1",
    "stewardship_family_dispatch_write_v1",
    "stewardship_family_smtp_result_v1",
    "stewardship_delivery_resolution_guard_v1",
)


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


@pytest.mark.parametrize("name", REPLACED)
def test_the_check_refuses_each_old_body_and_the_new_one_passes(name):
    """0173's DO block passes on the installed functions and fails once any
    replaced one is 0161's body again; 0173's own statement installs the new
    one over it, and the check passes again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    old = earlier("0161_report_conversion.sql", name)
    new = earlier("0173_report_email_test.sql", name)
    assert old != new
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="Migration 0173"),
            transaction.atomic(),
        ):
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(new)
            cursor.execute(
                f"ALTER FUNCTION public.{name}"
                f"({identity(cursor, name)}) OWNER TO CURRENT_USER"
            )
            cursor.execute(check)
            raise Rollback


def identity(cursor, name):
    """One function's identity arguments, as an ALTER names them."""
    cursor.execute(
        "SELECT pg_get_function_identity_arguments(oid) FROM pg_proc WHERE proname=%s",
        [name],
    )
    return cursor.fetchone()[0]


def test_the_check_refuses_a_resolution_guard_that_is_no_definer():
    """CREATE OR REPLACE resets nothing the statement names, but the check
    still proves the resolution guard is SECURITY DEFINER."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="Migration 0173"),
        transaction.atomic(),
    ):
        cursor.execute(
            "ALTER FUNCTION public.stewardship_delivery_resolution_guard_v1() "
            "SECURITY INVOKER"
        )
        cursor.execute(check)


def test_the_scheduler_admits_hints_from_bindings_alone(live_response_service, google):
    """The scheduler's login reads a test's bindings, never its address or
    email: it admits the test task's hint and recovery, and the message's
    delivery hint, with its own rights."""
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        delivery_handler,
    )

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        identifier, browser = weekly_started(harness)
        assert post_test(browser, identifier).status_code == 200
        row = ReportEmailTest.objects.get()
        status = _status(TaskRun.objects.get(pk=row.task_id))
        handler = report_email_test_handler(scheduler=True)
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert handler.admit("hint", status) is True
            assert handler.admit("recovery_hint", status) is False
        run_test_task(row)
        message = OutboxMessage.objects.get(purpose="admin_report_test")
        delivery = _status(TaskRun.objects.get(pk=message.task_id))
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            owner = delivery_handler(harness.service.store, scheduler=True)
            assert owner.admit("hint", delivery) is True
