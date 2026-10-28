"""An unconfirmed Ministry leader email is settled from evidence (#2300).

A Ministry leader email whose provider answer was lost stays Not sure it
arrived; it belongs to no campaign, and its page never sends it again
(applying the Email Ministry leaders review again sends a new one). Its
Mail message page and ``pk-admin delivery resolve`` settle it with "It was
sent" (accept) or "It was not sent" (confirm_unsent), each with a note,
through the resolution guard frozen file 0191 replaces; resending is
refused by the page, the command line, Python and the guard alone. Until it
is settled it holds a recorded mail halt from being cleared, and it counts
as review needed.

Each leader email here is made by the real scheduler and preparation worker
over #1592's deployments (``test_leader_emails_postgresql``); its lost
provider answer is seeded (``seed_uncertain``), as the notices' are.
"""

import re
from pathlib import Path
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
from parishkit.stewardship.jobs.leader_email_models import LeaderEmailRecipient

from .auth_builders import unguarded
from .automation_builders import paired
from .test_admin_delivery_cli_postgresql import NOTE
from .test_admin_delivery_cli_postgresql import resolve as cli_resolve
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_leader_emails_postgresql import (  # noqa: F401
    mail_ready,
    prepared,
    production,
    testing,
)
from .test_mail_halt_clear_postgresql import (
    dispatch_settle,
    halt_row,
    refused,
    sender_status,
    session,
    web_check,
    web_clear,
)
from .test_settle_notices_postgresql import (
    GUARD,
    MIGRATIONS,
    Rollback,
    guard_statement,
    outcome_panel,
    page,
    post,
    seed_uncertain,
)

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = MIGRATIONS / "0191_settle_leader_emails.sql"
# The leader each deployment fixture emails first.
LEADERS = {"production": "leader-9@example.org", "testing": "ann@example.org"}


@pytest.fixture
def admin(mail_ready, auth_service, monkeypatch):  # noqa: F811
    """Commands over the shared authentication service.

    ``mail_ready`` comes first so the deployment the service installs has
    the mail channels the leader emails need, whichever fixture builds it.
    """
    return runner(auth_service, monkeypatch)


def uncertain_leader_email(request, deployment="production", *, fail=True):
    """One Ministry leader email whose provider answer was lost.

    ``deployment`` names #1592's fixture (``production`` or ``testing``),
    which converts the deployment to users and adds the leaders; one
    request to its first leader is recorded, scheduled and prepared by the
    real scheduler and worker, and its message's attempt is then seeded as
    Not sure it arrived, its task failed unless ``fail`` is False.
    """
    built = request.getfixturevalue(deployment)
    store = built[0].service.store if deployment == "production" else built[0]
    rows = prepared(store, [LEADERS[deployment]])
    message = seed_uncertain(rows[LEADERS[deployment]].outbox, fail=fail)
    assert message.purpose == "leader_email" and message.campaign_id is None
    assert message.mode == SystemConfiguration.objects.get().mode
    return message


@pytest.mark.parametrize(
    ("deployment", "action", "state", "reason"),
    [
        ("production", "accept", "delivered", "admin_external_acceptance"),
        ("production", "confirm_unsent", "permanent_failure", "admin_confirmed_unsent"),
        ("testing", "confirm_unsent", "permanent_failure", "admin_confirmed_unsent"),
    ],
)
def test_the_page_settles_a_leader_email_without_offering_a_resend(
    request, admin, deployment, action, state, reason
):
    """The page explains the leader email, asks only what applies (no
    send-again question, nothing unavailable), counts it as review needed,
    and settles it with its note and audit event; afterwards nothing is
    left to do, and a Failed one says how a new one is sent."""
    message = uncertain_leader_email(request, deployment)
    browser, _, _ = paired(admin.service)
    mode = message.mode
    # Review needed counts it, and its link lists it (#2300 default).
    assert uncertain_by_mode() == {mode: 1}
    assert list(
        matching(state="needs_review", mode=mode).values_list("pk", flat=True)
    ) == [message.pk]
    assert matching(state="needs_attention").filter(pk=message.pk).exists()
    panel = outcome_panel(page(browser, message))
    assert "This is a Ministry leader email" in panel
    assert "Applying that review again sends a new one" in panel
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
    # Nothing it can never do is drawn, not even unavailable (#2300).
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
    if state == "permanent_failure":
        assert "to send a new one, apply the Email Ministry leaders review" in panel
        assert "not tried again on its own" not in panel


def test_resending_a_leader_email_is_refused_everywhere(request, admin):
    """A leader email is never sent again from here: the page's server and
    the command line refuse a resend, Python refuses one, and the guard
    alone refuses one inserted directly."""
    message = uncertain_leader_email(request)
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


def test_the_command_line_settles_a_leader_email_as_the_page_does(request, admin):
    """``delivery show`` asks the leader email's question; ``delivery
    resolve`` marks it delivered with its note, audited as the command, and
    afterwards it asks nothing and a retry is refused."""
    message = uncertain_leader_email(request)
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
    code, document, _ = cli_resolve(admin, secret, message, "accept", key)
    assert code == 0, document
    assert document["result"]["resolution"]["action"] == "accept"
    message.refresh_from_db()
    assert (message.state, message.reason) == ("delivered", "admin_external_acceptance")
    assert DeliveryResolution.objects.get(pk=key).evidence_note == NOTE
    assert AuditEvent.objects.filter(event_type="admin_cmd_delivery_resolve").exists()
    assert AuditEvent.objects.filter(event_type="delivery_resolution_accept").exists()
    assert uncertain_by_mode() == {}
    code, document, _ = cli_resolve(admin, secret, message, "retry_failed", uuid4())
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document, _ = admin("delivery", "show", str(message.pk), secret=secret)
    assert document["result"]["choices"] == [], document


def test_an_unsettled_leader_email_holds_the_halt_until_it_is_settled(request, admin):
    """Clear the halt counts the leader email as unsettled, in the preview
    and in the clear guard; once it is settled the clear is recorded."""
    message = uncertain_leader_email(request)
    _, _, row = paired(admin.service)
    actor, session_id = session()
    halt = halt_row()
    sender_status("halted")
    with task_login(ServiceRole.WEB, exact=True):
        check = web_check(actor, session_id)
    dispatch_settle(check, "passed")
    assert mail_halt_clear._unsettled() == (1, (message.pk,))
    with task_login(ServiceRole.WEB, exact=True), refused("not settled yet"):
        web_clear(halt, actor, session_id)
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
    assert mail_halt_clear._unsettled()[0] == 0
    with task_login(ServiceRole.WEB, exact=True):
        assert web_clear(halt, actor, session_id).halt_id == halt.pk


@pytest.mark.parametrize("hold", ["restore", "orphan"])
def test_the_guard_admits_a_leader_email_only_as_declared(request, admin, hold):
    """A restore under review refuses a leader email's answer in the guard,
    and so does a leader email no Email Ministry leaders recipient owns."""
    message = uncertain_leader_email(request)
    _, _, row = paired(admin.service)
    with unguarded(), connection.cursor() as cursor:
        if hold == "restore":
            SystemConfiguration.objects.update(restore_review_required=True)
        else:
            cursor.execute(
                "DELETE FROM stewardship_leader_email_recipient WHERE outbox_id=%s",
                [message.pk],
            )
            assert cursor.rowcount == 1
            assert not LeaderEmailRecipient.objects.filter(outbox_id=message.pk)
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


def test_a_leader_email_whose_task_is_running_shows_why_and_no_answer(request, admin):
    """While a Not sure it arrived leader email's task has not failed, its
    panel says so in one line in place of the question: no answer is drawn,
    disabled or not, and its note waits in the closed "Add a note" form."""
    message = uncertain_leader_email(request, fail=False)
    browser, _, _ = paired(admin.service)
    html = page(browser, message)
    panel = outcome_panel(html)
    assert 'id="notice-wait"' in panel
    assert "background task has not finished, so it can be settled" in panel
    assert 'type="radio"' not in panel and "disabled" not in panel
    assert "What did you find?" not in panel and "Other campaign work" not in panel
    assert 'id="delivery-note-disclosure"' in html


def test_the_check_refuses_the_old_guard_and_a_non_definer():
    """0191's DO block passes on the installed guard, fails on 0190's body
    (which settles no leader email) or a guard that is no SECURITY DEFINER,
    and passes again once 0191's statement and alter are run over the old
    body; the schema baseline carries the same body."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    alter = f"ALTER FUNCTION public.{GUARD}() SECURITY DEFINER;"
    old = guard_statement("0190_settle_notices.sql")
    new = guard_statement("0191_settle_leader_emails.sql")
    assert old != new and alter in text
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0191"), transaction.atomic():
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(DatabaseError, match="Migration 0191"), transaction.atomic():
            cursor.execute(f"ALTER FUNCTION public.{GUARD}() SECURITY INVOKER")
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(new)
            cursor.execute(alter)
            cursor.execute(check)
            raise Rollback
    baseline = (Path(MIGRATIONS).parent / "delivery_resolution.sql").read_text(
        encoding="utf-8"
    )
    assert new.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1) in baseline
