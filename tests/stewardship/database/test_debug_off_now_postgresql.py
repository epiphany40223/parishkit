"""Turn off debug logging on System health, and ``pk-admin system debug-off``
(ADM-13 PR 6b, #530).

The control appears while a service reports debug logging. Preview, then
confirm, in the shared review dialog (#1356): the preview is a one-sentence
confirmation, Apply's answer draws the region again, and a refusal is a
fragment the dialog shows as drawn; a stale sign-in gets the "Confirm with
Google" step-up; a repeated confirmation returns the first switch; one
switch at a time; Administrators only; the audit event names the switch,
and the panel says who set it. The command shows the page's preview at the
prompt and sets the switch through the page's own ``turn_off_debug`` on the
restricted web login, with the automation session as the fresh sign-in.
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.debug_switch_models import DebugOffSwitch

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .automation_builders import paired
from .campaign_builders import change
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_debug_production_banner_postgresql import force_mode
from .test_system_health_records_postgresql import raw

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/system/health/"


def debug_service(service="web"):
    """A process of ``service`` reporting now, with debug logging in effect.

    Written as the schema owner, which the status record's guard admits.
    """
    raw(
        "INSERT INTO stewardship_service_status (id,service,process,target,"
        "started_at,reported_at,application_version,debug_logging) "
        "VALUES (%s,%s,'main',NULL,now(),now(),'1.0.0',true)",
        [uuid4(), service],
    )


def post(browser, **values):
    """A form submission with the Admin CSRF token, under the web login."""
    token = browser.cookies["pk_admin_csrf"].value
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            PAGE, {"csrfmiddlewaretoken": token, **values}, HTTP_ACCEPT="text/html"
        )


def page(browser):
    """The System health page under the web login, as text."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.get(PAGE).content.decode()


def key_of(response):
    """The switch key the preview carries to its confirmation."""
    return re.search(
        r'name="key" value="([0-9a-f-]{36})"', response.content.decode()
    ).group(1)


def count(event):
    """How many audit events of this type were recorded."""
    return AuditEvent.objects.filter(event_type=event).count()


def test_the_control_is_offered_only_while_a_service_has_debug_logging(
    auth_service, google
):
    """Always drawn in its tile (#1214): greyed with why without debug
    logging, the button once a service has it."""
    browser, _ = signed_in()
    quiet = page(browser)
    assert 'id="debug-off"' in quiet
    assert 'value="debug_off_preview"' not in quiet
    assert "Debug logging is off in every service that reports." in quiet
    debug_service()
    body = page(browser)
    assert 'id="debug-off"' in body
    assert 'value="debug_off_preview"' in body


def test_preview_then_confirm_sets_one_switch_and_its_audit(auth_service, google):
    """Each step answers the dialog; a repeat returns the first switch."""
    debug_service()
    browser, _ = signed_in()
    views = count("system_health_viewed")
    preview = post(browser, action="debug_off_preview")
    assert preview.status_code == 200
    body = preview.content.decode()
    # A confirmation, not the page: one sentence naming the services the
    # server found, and none of the fixed bullets the About panel holds.
    assert "<h1" not in body and 'id="debug-off"' not in body
    assert (
        '<p id="debug-off-review-summary">This turns debug logging off now in the'
        " services that have it on (Web portal), without restarting them, until"
        " the debug-off switch is cleared.</p>"
    ) in body
    assert re.search(r'name="key" value="[0-9a-f-]{36}" data-review-preview', body)
    assert "Lines already written" not in body
    assert not DebugOffSwitch.objects.exists()
    key = key_of(preview)
    confirmed = post(browser, action="debug_off_confirm", key=key)
    assert confirmed.status_code == 200
    # Apply's answer is the region drawn again, its button greyed with who
    # set the switch; the page is not read again.
    done = confirmed.content.decode()
    assert "<h1" not in done and 'id="debug-off"' in done
    assert 'disabled aria-describedby="debug-off-reason"' in done
    assert "Debug logging was turned off by admin@example.org at" in done
    assert "data-live-status" not in done
    assert count("system_health_viewed") == views
    switch = DebugOffSwitch.objects.get()
    assert str(switch.pk) == key and switch.cleared_at is None
    event = AuditEvent.objects.get(event_type="debug_logging_turned_off")
    assert event.subject_id == switch.pk and event.actor_id == switch.actor_id
    # The same confirmation again returns the first switch, no second row.
    assert post(browser, action="debug_off_confirm", key=key).status_code == 200
    assert DebugOffSwitch.objects.count() == 1
    assert count("debug_logging_turned_off") == 1
    # While it is set, another preview is refused in the dialog, and the
    # button is greyed with who set it.
    refused = post(browser, action="debug_off_preview")
    assert refused.status_code == 409
    assert '<div data-review-refusal><p data-debug-off-refused="busy">' in (
        refused.content.decode()
    )
    body = page(browser)
    assert 'disabled aria-describedby="debug-off-reason"' in body
    assert "Debug logging was turned off by admin@example.org at" in body
    assert 'data-debug-switch="set"' in body


def test_the_page_follows_the_switch_before_the_services_report(auth_service, google):
    """In Production, a record written before the switch no longer names a
    service, so the tile turns Working as soon as the switch is set; one
    written after it, still with debug logging, names the service again."""
    debug_service()
    force_mode("production")
    browser, _ = signed_in()
    assert 'data-problem="debug_logging"' in page(browser)
    preview = post(browser, action="debug_off_preview")
    confirmed = post(browser, action="debug_off_confirm", key=key_of(preview))
    assert confirmed.status_code == 200
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        status = browser.get(PAGE + "status").content.decode()
    # Apply's answer is the region drawn again for the review dialog
    # (#1356): it names who set the switch; the tile follows on the page and
    # its status.
    answer = confirmed.content.decode()
    assert 'data-problem="debug_logging"' not in answer
    assert "Debug logging was turned off by admin@example.org at" in answer
    for body in (page(browser), status):
        assert 'data-problem="debug_logging"' not in body
        assert "Turned off from this page by admin@example.org at" in body
        assert "Debug logging was turned off by admin@example.org at" in body
    # A process that could not apply the switch says so in its next record.
    debug_service()
    body = page(browser)
    assert 'data-problem="debug_logging"' in body
    assert "Debug logging is on in Production in" in body


def test_a_stale_sign_in_gets_the_google_step_up(auth_service, google, monkeypatch):
    """Answered in the dialog: the step-up form, returning to this page."""
    debug_service()
    browser, _ = signed_in()
    monkeypatch.setattr(sessions, "FRESH_SECONDS", -1)
    for values in (
        {"action": "debug_off_preview"},
        {"action": "debug_off_confirm", "key": str(uuid4())},
    ):
        response = post(browser, **values)
        assert response.status_code == 403
        body = response.content.decode()
        assert "<div data-review-refusal>" in body
        assert 'data-debug-off-refused="reauthenticate"' in body
        assert 'id="debug-off"' not in body
        assert 'name="next" value="/admin/system/health/"' in body
    assert not DebugOffSwitch.objects.exists()


def test_only_an_administrator_may_turn_off_debug_logging(auth_service, google):
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
    assert post(browser, action="debug_off_preview").status_code == 403
    assert (
        post(browser, action="debug_off_confirm", key=str(uuid4())).status_code == 403
    )
    assert not DebugOffSwitch.objects.exists()


def test_stray_fields_are_refused_in_the_dialog(auth_service, google):
    """A malformed step is refused in the dialog. There is no Cancel step:
    the dialog's Cancel sends nothing (#1356)."""
    browser, _ = signed_in()
    views = count("system_health_viewed")
    cancelled = post(browser, action="debug_off_cancel")
    assert cancelled.status_code == 400
    refused = post(browser, action="debug_off_preview", key="x")
    assert refused.status_code == 400
    assert 'data-debug-off-refused="invalid"' in refused.content.decode()
    refused = post(browser, action="debug_off_confirm", key="not-a-uuid")
    assert refused.status_code == 400
    # None of these is a page view, and nothing was set.
    assert count("system_health_viewed") == views
    assert not DebugOffSwitch.objects.exists()


def test_a_missing_grant_is_said_plainly_not_as_a_step_up(
    auth_service, google, monkeypatch, caplog
):
    """A permission refusal other than the fresh sign-in's is logged and
    answered plainly in the dialog, since a new sign-in would not help."""
    from django.db import DatabaseError

    class Refusal(Exception):
        """A psycopg error's SQLSTATE, as Django wraps it."""

        sqlstate = "42501"

    def refuse(**values):
        """The insert the database refuses."""
        error = DatabaseError("permission denied for table stewardship_debug_off")
        error.__cause__ = Refusal(str(error))
        raise error

    browser, _ = signed_in()
    key = key_of(post(browser, action="debug_off_preview"))
    monkeypatch.setattr(DebugOffSwitch.objects, "create", refuse)
    answer = post(browser, action="debug_off_confirm", key=key)
    assert answer.status_code == 403
    assert 'data-debug-off-refused="denied"' in answer.content.decode()
    assert not DebugOffSwitch.objects.exists()
    assert "task_failed" in caplog.text


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def debug_off(admin, secret, *options, answer=b""):
    """``system debug-off`` with ``options``, ``answer`` typed at the prompt."""
    return admin("system", "debug-off", *options, secret=secret, answer=answer)


def test_debug_logging_is_turned_off_from_the_command_line(admin):
    """The prompt shows the preview; the switch, its events and a safe repeat."""
    debug_service("worker")
    _, secret, row = paired(admin.service)
    # The session's sign-in is ten minutes old: only the automation session
    # stands in for a fresh one.
    older = row.authenticated_at - timedelta(minutes=10)
    with unguarded():
        AutomationSession.objects.filter(pk=row.pk).update(authenticated_at=older)
        PortalSession.objects.filter(principal_id=row.principal_id).update(
            authenticated_at=older
        )
    for answer in (b"", b"no\n"):
        code, refused, errors = debug_off(admin, secret, answer=answer)
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "Debug logging is on in: Background worker." in errors
        assert "pk-stewardship debug-off-clear" in errors
    assert not DebugOffSwitch.objects.exists()

    key = str(uuid4())
    code, document, _ = debug_off(admin, secret, "--request-key", key, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["switch_id"] == key and result["created"] is True
    assert result["active"] is True and result["set_at"]
    switch = DebugOffSwitch.objects.get()
    assert switch.actor_id == row.principal_id and switch.cleared_at is None
    assert count("debug_logging_turned_off") == count("admin_cmd_system_debug_off") == 1
    [event] = AuditEvent.objects.filter(event_type="admin_cmd_system_debug_off")
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert count("automation_fresh_gate") == 1

    # The same key again returns the first switch and records nothing new.
    code, document, _ = debug_off(admin, secret, "--request-key", key, "--yes")
    assert code == 0 and document["result"]["created"] is False
    assert DebugOffSwitch.objects.count() == 1
    assert count("debug_logging_turned_off") == 1

    # Another while this one is set is refused before the prompt.
    code, refused, _ = debug_off(admin, secret, "--yes")
    assert code == 1 and refused["error"]["code"] == "stale_version"
    assert DebugOffSwitch.objects.count() == 1


def test_a_read_only_session_cannot_turn_off_debug_logging(admin):
    """Turn off debug logging needs a full-scope session, as every change does."""
    _, reader, _ = paired(admin.service, scope="read-only")
    code, refused, _ = debug_off(admin, reader, "--yes")
    assert code != 0 and refused["error"]["code"] in {"denied", "scope_required"}
    assert not DebugOffSwitch.objects.exists()
