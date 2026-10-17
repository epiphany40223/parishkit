"""Clear the halt on System health (ADM-13 PR 4b, #530).

The control appears in the Mail tile only while a mail sender is halted.
Its preview asks mail dispatch for a mailbox check and says what is
halted and why; the check's outcome and Confirm follow the polled status
in place; Confirm records the clear signal with ``mail_halt_cleared`` and
acknowledges the halt's critical-problems banner entries the preview saw.
No step needs a recent sign-in (#1547). Every step is answered with the
page, whose #clear-halt region ui-v1.js swaps in place; refusals say why
in that region. The guards and the consumers' side are 4a's
(test_mail_halt_clear_postgresql.py).
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent, CriticalEventAcknowledgement
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.mail_halt_models import MailboxCheck, MailHaltClear
from parishkit.stewardship.jobs.service_status_models import ServiceStatus

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock, change
from .test_accept_once_postgresql import unrelated
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_mail_halt_clear_postgresql import (
    dispatch_settle,
    halt_row,
    now,
    sender_status,
    session,
    web_clear,
)

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/system/health/"
STATUS = "/admin/system/health/status"


def post(browser, **values):
    """A form submission with the Admin CSRF token, under the web login."""
    token = browser.cookies["pk_admin_csrf"].value
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            PAGE, {"csrfmiddlewaretoken": token, **values}, HTTP_ACCEPT="text/html"
        )


def get(browser, path=PAGE):
    """A System health read under the web login, as text."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.get(path).content.decode()


def region(text):
    """The #clear-halt region of a page."""
    start = text.index('<div id="clear-halt"')
    return text[
        start : text.index('data-live-mirror-target="health-mail-panel"', start)
    ]


def token_of(text):
    """The signed binding the preview's forms carry."""
    return re.search(r'name="token" value="([^"]+)"', region(text)).group(1)


def count(event):
    """How many audit events of this type were recorded."""
    return AuditEvent.objects.filter(event_type=event).count()


def halted():
    """A recorded halt that a reporting mail sender holds."""
    halt = halt_row()
    sender_status("halted")
    return halt


def test_the_control_shows_only_while_a_sender_is_halted(auth_service, google):
    """No halt: nothing drawn and no query for it; a halt not recorded yet:
    the button greyed with its reason; a recorded one: offered."""
    browser, _ = signed_in()
    sender_status("running")
    text = get(browser)
    assert 'id="clear-halt"' in text and "clear_preview" not in text
    assert 'data-live-mirror="clear-halt-offer"' in text
    assert ">Clear the halt<" not in region(text)
    sender_status("halted")
    text = get(browser)
    assert 'data-clear-state="unrecorded"' in region(text)
    assert 'value="clear_preview"' not in region(text)
    halt = halt_row()
    text = get(browser)
    assert 'value="clear_preview"' in region(text)
    # The polled status names the halt and carries the offer.
    status = get(browser, STATUS)
    assert f'data-live-key-value="{halt.pk}"' in status
    assert 'value="clear_preview"' in status


def test_preview_check_and_confirm_clear_the_halt(auth_service, google):
    """The preview asks for one mailbox check and holds Confirm; the poll
    brings the passed check with Confirm offered; Confirm records the
    signal and its event once and acknowledges the halt's banner entry."""
    browser, _ = signed_in()
    entry = unrelated()
    halt = halted()
    preview = post(browser, action="clear_preview")
    assert preview.status_code == 200
    text = region(preview.content.decode())
    assert "Sending halted at" in text and "was not sent" in text
    assert "Checking the mailbox…" in text
    assert 'data-clear-reason="checking"' in text
    assert f'data-live-mirror-target="clear-halt-check-{halt.pk}"' in text
    assert re.search(r'value="clear_confirm" disabled', text)
    [check] = MailboxCheck.objects.all()
    assert check.state == "waiting"
    # Opening the preview again while the check is live asks for no other.
    post(browser, action="clear_preview")
    assert MailboxCheck.objects.count() == 1
    token = token_of(preview.content.decode())
    # Confirm before the check passes is refused in words.
    early = post(browser, action="clear_confirm", token=token)
    assert early.status_code == 409
    assert "The mailbox check is still running." in region(early.content.decode())
    assert not MailHaltClear.objects.exists()
    dispatch_settle(check, "passed")
    status = get(browser, STATUS)
    assert 'data-clear-check="passed"' in status
    assert 'data-clear-reason="ready"' in status
    assert not re.search(r'value="clear_confirm" disabled', status)
    # No recent sign-in is needed (#1547).
    session = PortalSession.objects.order_by("-authenticated_at").first()
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=now() - timedelta(minutes=40)
        )
    done = post(browser, action="clear_confirm", token=token)
    assert done.status_code == 200
    assert "Halt cleared." in region(done.content.decode())
    [signal] = MailHaltClear.objects.all()
    assert signal.halt_id == halt.pk and signal.actor_id == session.principal_id
    [event] = AuditEvent.objects.filter(event_type="mail_halt_cleared")
    assert event.subject_id == signal.pk
    assert set(
        CriticalEventAcknowledgement.objects.values_list("log_id", flat=True)
    ) == {entry.pk}
    # A repeat returns the first signal and records nothing new.
    assert post(browser, action="clear_confirm", token=token).status_code == 200
    assert count("mail_halt_cleared") == 1
    # The halt is cleared, so the section goes.
    assert "clear_preview" not in region(get(browser))


def test_check_again_asks_for_a_new_check(auth_service, google):
    """Once a check refused, Check again asks for another; while one runs,
    it asks for none."""
    browser, _ = signed_in()
    halted()
    token = token_of(post(browser, action="clear_preview").content.decode())
    [first] = MailboxCheck.objects.all()
    again = post(browser, action="clear_check", token=token)
    assert again.status_code == 200 and MailboxCheck.objects.count() == 1
    dispatch_settle(first, "refused")
    text = region(post(browser, action="clear_check", token=token).content.decode())
    assert MailboxCheck.objects.count() == 2
    assert "Checking the mailbox…" in text


def test_an_unsettled_email_holds_confirm(auth_service, google, family_mail):  # noqa: F811
    """The preview lists it with a link to settle it; Confirm stays greyed
    with the reason, and the server refuses it with the same words."""
    browser, _ = signed_in()
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        failed_delivery(family_mail)
    halted()
    preview = post(browser, action="clear_preview")
    [check] = MailboxCheck.objects.all()
    dispatch_settle(check, "passed")
    text = region(post(browser, action="clear_preview").content.decode())
    assert 'data-clear-unsettled="1"' in text
    assert "/admin/mail/outgoing/?state=delivery_unknown" in text
    assert 'data-clear-reason="unsettled"' in text
    refused = post(
        browser, action="clear_confirm", token=token_of(preview.content.decode())
    )
    assert refused.status_code == 409
    assert "settle them on Outgoing mail first" in region(refused.content.decode())
    assert not MailHaltClear.objects.exists()


def test_a_halt_that_changed_is_said_in_the_region(auth_service, google):
    """A newer halt since the preview: Confirm is refused as changed."""
    browser, _ = signed_in()
    halted()
    token = token_of(post(browser, action="clear_preview").content.decode())
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_mail_halt (id,began_at,kind) "
            "VALUES (%s,now(),'after_data')",
            [uuid4()],
        )
    answer = post(browser, action="clear_confirm", token=token)
    assert answer.status_code == 409
    assert "The mail halt changed since it was reviewed." in region(
        answer.content.decode()
    )
    bad = post(browser, action="clear_confirm", token="altered")
    assert bad.status_code == 400
    assert not MailHaltClear.objects.exists()


def test_only_an_administrator_may_clear(auth_service, google):
    """Staff cannot preview or confirm."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    halted()
    assert post(browser, action="clear_preview").status_code == 403
    assert post(browser, action="clear_confirm", token="x").status_code == 403
    assert not MailboxCheck.objects.exists() and not MailHaltClear.objects.exists()


def backdate(table, pk, column="created_at", age=timedelta(minutes=5)):
    """Move a row's timestamp into the past, as the schema owner (the
    models refuse bulk rewrites of history)."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET {column} = now() - %s WHERE id = %s", [age, pk]
        )


def reason_of(text, path=PAGE):
    """The clear section's greyed-button reason: ``(state, text)``. The
    polled status carries it in its mirrored copy, outside any region."""
    found = re.search(
        r'data-clear-state="(\w+)">(.*?)</p>',
        region(text) if path == PAGE else text,
        flags=re.DOTALL,
    )
    return found.group(1), found.group(2)


def test_the_section_says_why_a_halted_sender_has_no_halt(auth_service, google):
    """While a sender reports Halted with no current halt, the line tells a
    clear just recorded ("clearing") from a halt not recorded yet, and both
    from a lasting mismatch, which says to restart the mail sender with the
    runbook link rather than "within about 30 seconds" forever."""
    browser, _ = signed_in()
    # A halt that began a moment ago and is not recorded yet.
    sender_status("halted")
    state, words = reason_of(get(browser))
    assert state == "unrecorded" and "within about 30 seconds" in words
    # A clear just recorded; the sender has not lifted yet.
    halt = halt_row()
    actor, session_id = session()
    signal = web_clear(halt, actor, session_id)
    for path in (PAGE, STATUS):
        state, words = reason_of(get(browser, path), path)
        assert state == "clearing"
        assert "The halt was cleared; the mail senders start again" in words
        assert "restart" not in words
    # The sender still reports Halted long after: say to restart it.
    backdate("stewardship_mail_halt_clear", signal.pk, age=timedelta(minutes=5))
    [status] = ServiceStatus.objects.all()
    backdate(
        "stewardship_service_status",
        status.pk,
        column="sender_since",
        age=timedelta(minutes=2, seconds=30),
    )
    text = get(browser)
    state, words = reason_of(text)
    assert state == "stuck" and "within about 30 seconds" not in words
    assert "restart the mail sender" in words
    assert "stewardship-launch-runbooks.md#mail-provider-outage" in words


def test_a_halted_report_that_lasts_with_no_halt_says_to_restart(auth_service, google):
    """No halt was ever recorded and the sender has reported Halted for
    minutes: the same restart line."""
    browser, _ = signed_in()
    sender_status("halted", age=timedelta(minutes=2, seconds=30))
    state, words = reason_of(get(browser))
    assert state == "stuck" and "restart the mail sender" in words


def test_the_acknowledgement_covers_only_the_halts_entries(auth_service, google):
    """An entry from more than ten minutes before the halt began, and one of
    another banner event, stay unacknowledged; the halt's own go."""
    from parishkit.stewardship.audit.services import operational
    from parishkit.stewardship.observability import Event

    from ..log_samples import sample as log_sample

    browser, _ = signed_in()
    inside = unrelated()
    old = unrelated()
    backdate("stewardship_operational_log", old.pk, age=timedelta(minutes=30))
    with transaction.atomic():
        other = operational(
            Event.SOURCE_PROVIDER_FAILED,
            level="CRITICAL",
            **log_sample(Event.SOURCE_PROVIDER_FAILED),
        )
    halted()
    preview = post(browser, action="clear_preview")
    [check] = MailboxCheck.objects.all()
    dispatch_settle(check, "passed")
    token = token_of(preview.content.decode())
    assert post(browser, action="clear_confirm", token=token).status_code == 200
    assert set(
        CriticalEventAcknowledgement.objects.values_list("log_id", flat=True)
    ) == {inside.pk}
    assert {old.pk, other.pk}.isdisjoint(
        CriticalEventAcknowledgement.objects.values_list("log_id", flat=True)
    )


def test_a_page_with_nothing_halted_reads_no_halt_records(auth_service, google):
    """While no sender reports Halted the page and its poll make no query
    about halts, clears or mailbox checks."""
    browser, _ = signed_in()
    sender_status("running")
    for path in (PAGE, STATUS):
        with CaptureQueriesContext(connection) as queries:
            get(browser, path)
        assert not [
            row["sql"]
            for row in queries
            if row["sql"].startswith("SELECT")
            and re.search(
                r"stewardship_mail_halt|stewardship_mailbox_check", row["sql"]
            )
        ]
