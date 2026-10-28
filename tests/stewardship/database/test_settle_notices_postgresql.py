"""An unconfirmed operational or security notice is settled from evidence (#2295).

A notice to the Administrators whose provider answer was lost stays Not
sure it arrived; it belongs to no campaign and is never sent again. Its
Mail message page and ``pk-admin delivery resolve`` settle it with "It was
sent" (accept) or "It was not sent" (confirm_unsent), each with a note,
through the resolution guard frozen file 0190 replaces; resending is
refused by Python and by the guard alone. Until it is settled it holds a
recorded mail halt from being cleared, and it counts as review needed.

Each notice here is made by the real scheduler and preparation worker; its
lost provider answer is seeded (``uncertain_notice``).
"""

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import mail_halt_clear
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_metadata import matching, uncertain_by_mode
from parishkit.stewardship.jobs.delivery_resolution import resolve_delivery
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status

from .auth_builders import unguarded
from .automation_builders import paired
from .test_admin_delivery_cli_postgresql import NOTE
from .test_admin_delivery_cli_postgresql import resolve as cli_resolve
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_mail_halt_clear_postgresql import (
    dispatch_settle,
    halt_row,
    refused,
    sender_status,
    session,
    web_check,
    web_clear,
)
from .test_sent_mail_postgresql import notices
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0190_settle_notices.sql"
GUARD = "stewardship_delivery_resolution_guard_v1"


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Commands over the shared authentication service."""
    return runner(auth_service, monkeypatch)


def uncertain_notice(store, *, fail=True):
    """One operational notice whose provider answer was lost, task failed.

    The scheduler and preparation worker allocate one notice per current
    Administrator (``notices``). The first one's delivery task is claimed
    and its attempt is then seeded as Not sure it arrived with the SQL
    guards paused (``unguarded``), as the Sent mail count tests seed theirs:
    what is under test is how a notice is settled, not how one becomes
    uncertain (test_operational_dispatch_postgresql covers that). Its task
    then fails, as the mail consumer's ``permanent_failure`` leaves it,
    unless ``fail`` is False (the task is still claimed).
    """
    recipients = notices(SimpleNamespace(service=SimpleNamespace(store=store)))
    message = recipients[min(recipients)].outbox
    act(_status(TaskRun.objects.get(pk=message.task_id)), "claim")
    run = TaskRun.objects.get(pk=message.task_id)
    with unguarded(), connection.cursor() as cursor:
        # The attempt and its history event, as mail dispatch's mark_unknown
        # writes them (the paused history trigger's own INSERT).
        cursor.execute(
            "UPDATE stewardship_outbox_message SET state='delivery_unknown',"
            "action='mark_unknown',reason='smtp_delivery_unknown',attempt=1,"
            "run_id=%s,task_fence=%s,worker_id=%s,command_id=%s,"
            "submitted_at=now(),provider_deadline=now()+interval '1 minute',"
            "updated_at=now(),version=version+1 WHERE id=%s",
            [run.pk, run.fence, run.worker_id, uuid4(), message.pk],
        )
        cursor.execute(
            "INSERT INTO stewardship_outbox_event (id,created_at,actor_id,"
            "correlation_id,message_id,command_id,command_digest,version,"
            "previous_state,state,action,attempt,render_id,run_id,task_fence,"
            "worker_id,provider_key_digest,provider_message_digest,"
            "evidence_digest,evidence_note,reason,not_before,submitted_at,"
            "provider_deadline,finished_at) SELECT gen_random_uuid(),updated_at,"
            "actor_id,correlation_id,id,command_id,command_digest,version,"
            "'pending',state,action,attempt,render_id,run_id,task_fence,"
            "worker_id,provider_key_digest,provider_message_digest,"
            "evidence_digest,evidence_note,reason,not_before,submitted_at,"
            "provider_deadline,finished_at FROM stewardship_outbox_message "
            "WHERE id=%s",
            [message.pk],
        )
    if fail:
        act(_status(run), "permanent_failure")
    message.refresh_from_db()
    assert message.state == "delivery_unknown"
    assert (TaskRun.objects.get(pk=message.task_id).state == "failed") is fail
    return message


def page(browser, message):
    """The notice's Mail message page as the web login serves it."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(f"/admin/mail/sent/{message.pk}/")
    assert response.status_code == 200
    return response.content.decode()


def post(browser, message, action, **extra):
    """Post one answer as the page's form does; the response."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            f"/admin/mail/sent/{message.pk}/resolution/",
            {
                "command_id": str(uuid4()),
                "expected_version": str(message.version),
                "action": action,
                "note": NOTE,
                **extra,
            },
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )


def outcome_panel(html):
    """The outcome panel's markup."""
    start = html.index('id="delivery-resolve"')
    return html[start : html.index('id="delivery-state"', start)]


@pytest.mark.parametrize(
    ("action", "state", "reason"),
    [
        ("accept", "delivered", "admin_external_acceptance"),
        ("confirm_unsent", "permanent_failure", "admin_confirmed_unsent"),
    ],
)
def test_the_page_settles_a_notice_without_offering_a_resend(
    response_service, google, admin, action, state, reason
):
    """The page explains the notice, asks only what applies (no send-again
    question, nothing unavailable), counts it as review needed, and settles
    it with its note and audit event; afterwards nothing is left to do."""
    message = uncertain_notice(response_service.service.store)
    browser, _, _ = paired(admin.service)
    mode = message.mode
    # Review needed counts it, and its link lists it (#2295 decision).
    assert uncertain_by_mode() == {mode: 1}
    assert list(
        matching(state="needs_review", mode=mode).values_list("pk", flat=True)
    ) == [message.pk]
    panel = outcome_panel(page(browser, message))
    assert "This is an operational notice" in panel
    assert "a halted mail sender can't be cleared" in panel
    assert re.findall(r'name="found" value="(\w+)"', panel) == [
        "sent",
        "not_sent",
        "unsure",
    ]
    assert re.findall(r'name="action" value="(\w+)"', panel) == [
        "accept",
        "confirm_unsent",
        "note",
    ]
    # Nothing it can never do is drawn, not even unavailable (#2295).
    assert "disabled" not in panel
    assert "Send it again" not in panel and 'name="again"' not in panel
    assert "duplicate_acknowledged" not in panel
    assert "belong to no campaign" not in panel
    response = post(browser, message, action)
    assert response.status_code == 302, response.content[:400]
    message.refresh_from_db()
    assert (message.state, message.reason) == (state, reason)
    (receipt,) = DeliveryResolution.objects.filter(message_id=message.pk)
    assert (receipt.action, receipt.evidence_note) == (action, NOTE)
    event = AuditEvent.objects.get(event_type=f"delivery_resolution_{action}")
    assert event.subject_id == receipt.pk and event.campaign_reference is None
    assert uncertain_by_mode() == {}
    assert not matching(state="needs_attention").filter(pk=message.pk).exists()
    # Settled: the panel asks nothing and shows nothing unavailable; the
    # note waits in its closed disclosure, as for any settled email.
    html = page(browser, message)
    panel = outcome_panel(html)
    assert 'name="action"' not in panel and "disabled" not in panel
    assert 'id="delivery-note-disclosure"' in html


def test_resending_a_notice_is_refused_by_python_and_by_the_guard(
    response_service, google, admin
):
    """A notice is never sent again: the page's server and the command line
    refuse a resend, and the guard alone refuses one inserted directly."""
    message = uncertain_notice(response_service.service.store)
    browser, secret, row = paired(admin.service)
    response = post(browser, message, "resend", duplicate_acknowledged="yes")
    assert response.status_code == 403
    code, document, _ = admin(
        "delivery",
        "resend",
        str(message.pk),
        "--expected-version",
        str(message.version),
        "--note",
        NOTE,
        "--request-key",
        str(uuid4()),
        "--yes",
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    with pytest.raises(PermissionError), task_login(ServiceRole.WEB, exact=True):
        resolve_delivery(
            admin.service.store,
            row.principal_id,
            message_id=message.pk,
            command_id=uuid4(),
            expected_version=message.version,
            action="resend",
            note=NOTE,
            duplicate_acknowledged=True,
        )
    command = uuid4()
    with (
        task_login(ServiceRole.WEB, exact=True),
        refused("lacks current exact Admin authority"),
    ):
        DeliveryResolution.objects.create(
            id=command,
            correlation_id=command,
            actor_id=row.principal_id,
            message_id=message.pk,
            expected_version=message.version,
            action="resend",
            evidence_note=NOTE,
            duplicate_acknowledged=True,
            previous_task_id=message.task_id,
        )
    message.refresh_from_db()
    assert message.state == "delivery_unknown"
    assert not DeliveryResolution.objects.exists()


def test_the_command_line_settles_a_notice_as_the_page_does(
    response_service, google, admin
):
    """``delivery show`` asks the notice's question; ``delivery resolve``
    records it as not sent, and a later retry is refused."""
    message = uncertain_notice(response_service.service.store)
    _, secret, _ = paired(admin.service)
    code, document, _ = admin("delivery", "show", str(message.pk), secret=secret)
    assert code == 0, document
    shown = document["result"]
    assert shown["outcome"] == "needs_resolution"
    assert [
        (each["choice"], each["action"], each["unavailable"])
        for each in shown["choices"]
    ] == [
        ("sent", "accept", None),
        ("not_sent", "confirm_unsent", None),
        ("unsure", "note", None),
    ]
    assert "resend" not in shown["actions"]
    key = uuid4()
    code, document, _ = cli_resolve(admin, secret, message, "confirm_unsent", key)
    assert code == 0, document
    assert document["result"]["resolution"]["action"] == "confirm_unsent"
    message.refresh_from_db()
    assert message.state == "permanent_failure"
    assert DeliveryResolution.objects.get(pk=key).evidence_note == NOTE
    code, document, _ = cli_resolve(admin, secret, message, "retry_failed", uuid4())
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document, _ = admin("delivery", "show", str(message.pk), secret=secret)
    assert document["result"]["choices"] == [], document


def test_an_unsettled_notice_holds_the_halt_until_it_is_settled(
    response_service, google, admin
):
    """Clear the halt counts the notice as unsettled, in the preview and in
    the clear guard; once it is settled the clear is recorded."""
    message = uncertain_notice(response_service.service.store)
    _, _, row = paired(admin.service)
    actor, session_id = session()
    halt = halt_row()
    sender_status("halted")
    with task_login(ServiceRole.WEB, exact=True):
        check = web_check(actor, session_id)
    dispatch_settle(check, "passed")
    assert mail_halt_clear._unsettled()[0] == 1
    with task_login(ServiceRole.WEB, exact=True), refused("not settled yet"):
        web_clear(halt, actor, session_id)
    with task_login(ServiceRole.WEB, exact=True):
        resolve_delivery(
            admin.service.store,
            row.principal_id,
            message_id=message.pk,
            command_id=uuid4(),
            expected_version=message.version,
            action="accept",
            note=NOTE,
        )
    assert mail_halt_clear._unsettled()[0] == 0
    with task_login(ServiceRole.WEB, exact=True):
        assert web_clear(halt, actor, session_id).halt_id == halt.pk


@pytest.mark.parametrize("hold", ["restore", "campaign"])
def test_the_guard_admits_a_notice_only_while_the_deployment_is_admitted(
    response_service, google, admin, hold
):
    """A restore under review refuses a notice's answer in the guard, and
    so does a notice bound to a campaign, which no notice ever is."""
    message = uncertain_notice(response_service.service.store)
    _, _, row = paired(admin.service)
    with unguarded(), connection.cursor() as cursor:
        if hold == "restore":
            SystemConfiguration.objects.update(restore_review_required=True)
        else:
            cursor.execute(
                "UPDATE stewardship_outbox_message SET campaign_id="
                "(SELECT id FROM stewardship_campaign ORDER BY id LIMIT 1)"
                " WHERE id=%s RETURNING campaign_id",
                [message.pk],
            )
            assert cursor.fetchone()[0] is not None
    command = uuid4()
    with (
        task_login(ServiceRole.WEB, exact=True),
        refused("lacks current exact Admin authority"),
    ):
        DeliveryResolution.objects.create(
            id=command,
            correlation_id=command,
            actor_id=row.principal_id,
            message_id=message.pk,
            expected_version=message.version,
            action="note",
            evidence_note=NOTE,
            duplicate_acknowledged=False,
        )
    assert not DeliveryResolution.objects.exists()


def guard_statement(file):
    """The guard's CREATE OR REPLACE statement in a frozen file."""
    text = (MIGRATIONS / file).read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{GUARD}(")
    return text[start : text.index("END $$;", start) + len("END $$;")]


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_the_check_refuses_the_old_guard_and_a_non_definer():
    """0190's DO block passes on the installed guard, fails on 0173's body
    or a guard that is no SECURITY DEFINER, and passes again once 0190's
    statement and alter are run over the old body."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    alter = f"ALTER FUNCTION public.{GUARD}() SECURITY DEFINER;"
    old = guard_statement("0173_report_email_test.sql")
    new = guard_statement("0190_settle_notices.sql")
    assert old != new and alter in text
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0190"), transaction.atomic():
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0190"), transaction.atomic():
            cursor.execute(f"ALTER FUNCTION public.{GUARD}() SECURITY INVOKER")
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(new)
            cursor.execute(alter)
            cursor.execute(check)
            raise Rollback
    # The schema baseline carries the same body.
    baseline = (MIGRATIONS.parent / "delivery_resolution.sql").read_text(
        encoding="utf-8"
    )
    assert new.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1) in baseline


def test_a_notice_that_failed_asks_nothing(response_service, google, admin):
    """A Failed notice is never sent again, so its page asks no question
    and shows no unavailable answer; it is not in Needs attention."""
    message = uncertain_notice(response_service.service.store)
    browser, _, row = paired(admin.service)
    with task_login(ServiceRole.WEB, exact=True):
        resolve_delivery(
            admin.service.store,
            row.principal_id,
            message_id=message.pk,
            command_id=uuid4(),
            expected_version=message.version,
            action="confirm_unsent",
            note=NOTE,
        )
    panel = outcome_panel(page(browser, message))
    assert "Failed" in panel
    assert "<fieldset" not in panel and "disabled" not in panel
    # Nothing implies a retry by hand (#2295).
    assert "A notice is never sent again." in panel
    assert "not tried again on its own" not in panel
    assert not matching(state="needs_attention").exists()
    assert matching(state="permanent_failure").filter(pk=message.pk).exists()


def test_a_notice_whose_task_is_running_shows_why_and_no_answer(
    response_service, google, admin
):
    """While a Not sure it arrived notice's task has not failed, its panel
    says so in one line in place of the question: no answer is drawn,
    disabled or not, and its note waits in the closed "Add a note" form."""
    message = uncertain_notice(response_service.service.store, fail=False)
    browser, _, _ = paired(admin.service)
    html = page(browser, message)
    panel = outcome_panel(html)
    assert 'id="notice-wait"' in panel
    assert "background task has not finished, so it can be settled" in panel
    assert 'type="radio"' not in panel and "disabled" not in panel
    assert "What did you find?" not in panel and "Other campaign work" not in panel
    assert 'id="delivery-note-disclosure"' in html
