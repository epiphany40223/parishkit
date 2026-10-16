"""Allow debug logging again on System health, and ``pk-admin system
debug-allow`` (ADM-13 PR 6c, #1207).

In Testing, while the debug-off switch is set, an Administrator previews and
confirms clearing it in the shared review dialog (#1356): the preview is a
one-sentence confirmation, Apply's answer draws Turn off debug logging's
region again, and a refusal is a fragment the dialog shows as drawn; a
stale sign-in gets
the "Confirm with Google" step-up; a repeated confirmation returns the
switch; the audit event names the switch. In Production the page greys the
button, the server refuses the step, and the switch's guard (frozen
migration 0088) refuses the web login's clear on its own. The command shows
the page's preview at the prompt and clears the switch through the page's
own ``allow_debug`` on the restricted web login.
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

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
from .test_debug_off_switch_postgresql import web_with
from .test_debug_production_banner_postgresql import force_mode

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/system/health/"
MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
TEXT = (MIGRATIONS / "0088_debug_allow.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]


def set_switch():
    """A set debug-off switch, written as the schema owner."""
    return DebugOffSwitch.objects.create(
        id=uuid4(), actor_id=uuid4(), session_id=uuid4(), authenticated_at=now()
    )


def now():
    """The database's clock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT now()")
        return cursor.fetchone()[0]


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


def test_preview_then_confirm_clears_the_switch_in_testing(auth_service, google):
    """Answered in the dialog; the clear names the Administrator; a repeat
    is safe."""
    switch = set_switch()
    browser, _ = signed_in()
    body = page(browser)
    # The region is drawn while the switch is set, with the button offered.
    assert 'id="debug-off"' in body and 'value="debug_allow_preview"' in body
    views = count("system_health_viewed")
    preview = post(browser, action="debug_allow_preview")
    assert preview.status_code == 200
    text = preview.content.decode()
    # A confirmation, not the page: one sentence, the dialog's description.
    assert "<h1" not in text and 'id="debug-off"' not in text
    assert '<p id="debug-allow-review-summary">This clears the debug-off' in text
    assert "personal information" in text and "set at" not in text
    key = key_of(preview)
    assert key == str(switch.pk)
    assert f'name="key" value="{key}" data-review-preview' in text
    confirmed = post(browser, action="debug_allow_confirm", key=key)
    assert confirmed.status_code == 200
    # Apply's answer is the region drawn again; the page is not read again.
    done = confirmed.content.decode()
    assert "<h1" not in done and 'id="debug-off"' in done
    assert "data-live-status" not in done
    assert count("system_health_viewed") == views
    switch.refresh_from_db()
    session = PortalSession.objects.order_by("-authenticated_at").first()
    assert switch.cleared_at is not None and switch.cleared_by == "administrator"
    assert switch.cleared_actor_id == session.principal_id
    [event] = AuditEvent.objects.filter(event_type="debug_logging_allowed")
    assert event.subject_id == switch.pk and event.actor_id == session.principal_id
    # The same confirmation again returns the switch and records nothing new.
    assert post(browser, action="debug_allow_confirm", key=key).status_code == 200
    assert count("debug_logging_allowed") == 1
    # No switch is set now: the preview is refused and no button is drawn.
    refused = post(browser, action="debug_allow_preview")
    assert refused.status_code == 409
    assert '<div data-review-refusal><p data-debug-allow-refused="busy">' in (
        refused.content.decode()
    )
    assert ">Allow debug logging again<" not in page(browser)


def test_a_switch_cleared_by_someone_else_is_stale(auth_service, google):
    """The operator (or another Administrator) cleared it first."""
    switch = set_switch()
    browser, _ = signed_in()
    key = key_of(post(browser, action="debug_allow_preview"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_debug_off_clear_v2()")
    answer = post(browser, action="debug_allow_confirm", key=key)
    assert answer.status_code == 409
    assert 'data-debug-allow-refused="busy"' in answer.content.decode()
    switch.refresh_from_db()
    assert switch.cleared_by == "operator"
    assert count("debug_logging_allowed") == 0
    unknown = post(browser, action="debug_allow_confirm", key=str(uuid4()))
    assert unknown.status_code == 400
    # There is no Cancel step: the dialog's Cancel sends nothing (#1356).
    assert post(browser, action="debug_allow_cancel").status_code == 400


def test_a_stale_sign_in_gets_the_google_step_up(auth_service, google, monkeypatch):
    """Answered in the dialog: the step-up form, returning to this page."""
    switch = set_switch()
    browser, _ = signed_in()
    monkeypatch.setattr(sessions, "FRESH_SECONDS", -1)
    for values in (
        {"action": "debug_allow_preview"},
        {"action": "debug_allow_confirm", "key": str(switch.pk)},
    ):
        response = post(browser, **values)
        assert response.status_code == 403
        body = response.content.decode()
        assert "<div data-review-refusal>" in body
        assert 'data-debug-allow-refused="reauthenticate"' in body
        assert 'name="next" value="/admin/system/health/"' in body
    switch.refresh_from_db()
    assert switch.cleared_at is None


def test_only_an_administrator_may_allow_debug_logging(auth_service, google):
    """Staff cannot preview or confirm."""
    switch = set_switch()
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
    assert post(browser, action="debug_allow_preview").status_code == 403
    confirm = post(browser, action="debug_allow_confirm", key=str(switch.pk))
    assert confirm.status_code == 403
    switch.refresh_from_db()
    assert switch.cleared_at is None


def test_production_draws_no_button_and_refuses_the_step(auth_service, google):
    """In Production only the server operator clears the switch."""
    switch = set_switch()
    browser, _ = signed_in()
    force_mode("production")
    body = page(browser)
    assert 'value="debug_allow_preview"' not in body
    assert ">Allow debug logging again<" not in body
    assert 'data-debug-status="set"' in body
    for values in (
        {"action": "debug_allow_preview"},
        {"action": "debug_allow_confirm", "key": str(switch.pk)},
    ):
        answer = post(browser, **values)
        assert answer.status_code == 403
        assert 'data-debug-allow-refused="production"' in answer.content.decode()
    switch.refresh_from_db()
    assert switch.cleared_at is None
    assert count("debug_logging_allowed") == 0


@contextmanager
def web_clearing():
    """The web login, holding its declared column UPDATE grant."""
    with task_login(ServiceRole.WEB, exact=True):
        yield


def web_clear(switch, clearer, **values):
    """Clear ``switch`` as the web login would, for ``clearer``."""
    row = {
        "cleared_at": now(),
        "cleared_by": "administrator",
        "cleared_actor_id": clearer,
    } | values
    with transaction.atomic():
        return DebugOffSwitch.objects.filter(pk=switch.pk).update(**row)


@contextmanager
def refused(message):
    """A change refused with ``message`` and rolled back."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        yield


def test_the_guard_admits_only_a_fresh_administrators_clear_in_testing(
    auth_service, google
):
    """The SQL boundary holds without the page: Production, the wrong kind,
    a stale or unknown Administrator, and rewriting who set it are refused;
    the clear time is the database's; it is cleared once."""
    browser, _ = signed_in()
    session = PortalSession.objects.order_by("-authenticated_at").first()
    actor = session.principal_id
    switch = set_switch()
    force_mode("production")
    with web_clearing(), refused("clears the debug-off switch in Production"):
        web_clear(switch, actor)
    force_mode("testing")
    with web_clearing():
        with refused("only as an Administrator"):
            web_clear(switch, None, cleared_by="operator")
        with refused("only as an Administrator"):
            web_clear(switch, None)
        with refused("requires fresh Administrator authentication"):
            web_clear(switch, uuid4())
        # Its column grant already refuses the rest of the row.
        with refused("permission denied"):
            web_clear(switch, actor, actor_id=uuid4())
    # With every column granted, the guard still keeps who set it.
    with web_with("UPDATE"), refused("keeps who set it"):
        web_clear(switch, actor, actor_id=uuid4())
    # A stale sign-in is refused too.
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at - timedelta(minutes=10)
        )
    with web_clearing(), refused("requires fresh Administrator authentication"):
        web_clear(switch, actor)
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at
        )
    with web_clearing():
        assert web_clear(switch, actor, cleared_at=now() - timedelta(days=1)) == 1
        with refused("cleared once"):
            web_clear(switch, actor)
        with refused("permission denied"):
            DebugOffSwitch.objects.filter(pk=switch.pk).delete()
    switch.refresh_from_db()
    assert switch.cleared_at > now() - timedelta(minutes=5)
    # The worker holds no grant on the switch at all.
    other = set_switch()
    with task_login(ServiceRole.WORKER, exact=True), refused("permission denied"):
        web_clear(other, actor)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def debug_allow(admin, secret, *options, answer=b""):
    """``system debug-allow`` with ``options``, ``answer`` typed at the prompt."""
    return admin("system", "debug-allow", *options, secret=secret, answer=answer)


def test_debug_logging_is_allowed_again_from_the_command_line(admin):
    """The prompt shows the preview; the clear, its events and a safe repeat."""
    _, secret, row = paired(admin.service)
    # Nothing set: refused before the prompt.
    code, refused_document, _ = debug_allow(admin, secret, "--yes")
    assert code == 1 and refused_document["error"]["code"] == "stale_version"
    switch = set_switch()
    # The session's sign-in is ten minutes old: only the automation session
    # stands in for a fresh one.
    older = row.authenticated_at - timedelta(minutes=10)
    with unguarded():
        AutomationSession.objects.filter(pk=row.pk).update(authenticated_at=older)
        PortalSession.objects.filter(principal_id=row.principal_id).update(
            authenticated_at=older
        )
    for answer in (b"", b"no\n"):
        code, refusal, errors = debug_allow(admin, secret, answer=answer)
        assert code == 4 and refusal["error"]["code"] == "confirmation_required"
        assert "Clear the debug-off switch set at" in errors
    code, document, _ = debug_allow(admin, secret, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["switch_id"] == str(switch.pk) and result["created"] is True
    assert result["cleared_at"]
    switch.refresh_from_db()
    assert switch.cleared_by == "administrator"
    assert switch.cleared_actor_id == row.principal_id
    assert count("debug_logging_allowed") == count("admin_cmd_system_debug_allow") == 1
    [event] = AuditEvent.objects.filter(event_type="admin_cmd_system_debug_allow")
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    # Naming the same switch again returns it and records nothing new.
    code, document, _ = debug_allow(admin, secret, "--switch", str(switch.pk), "--yes")
    assert code == 0 and document["result"]["created"] is False
    assert count("debug_logging_allowed") == 1
    # In Production it is refused before the prompt.
    set_switch()
    force_mode("production")
    code, refusal, _ = debug_allow(admin, secret, "--yes")
    assert code == 1 and refusal["error"]["code"] == "denied"
    assert DebugOffSwitch.objects.filter(cleared_at__isnull=True).count() == 1


def test_the_migration_check_passes_and_refuses_the_old_guard():
    """0088's DO block passes as installed, and raises with 0078's guard
    (the definition an upgrade replaces) or a drifted one; all rolled back."""
    old = (MIGRATIONS / "0078_debug_off_switch.sql").read_text(encoding="utf-8")
    start = old.index("CREATE FUNCTION public.stewardship_debug_off_switch_guard_v1(")
    guard = old[start : old.index("END $$;", start) + len("END $$;")].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    # 0088's own guard with one of its boundaries dropped: the web-only
    # clear, or the single clear.
    start = TEXT.index("CREATE OR REPLACE FUNCTION")
    current = TEXT[start : TEXT.index("END $$;", start) + len("END $$;")]
    web_only = (
        "IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_web' THEN"
    )
    once = "IF OLD.cleared_at IS NOT NULL OR NEW.cleared_at IS NULL THEN"
    assert web_only in current and once in current
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)
    for change_sql in (
        guard,
        current.replace(web_only, "IF false THEN"),
        current.replace(once, "IF NEW.cleared_at IS NULL THEN"),
        "ALTER FUNCTION public.stewardship_debug_off_switch_guard_v1() "
        "SECURITY DEFINER",
        "GRANT EXECUTE ON FUNCTION public.stewardship_debug_off_switch_guard_v1() "
        "TO PUBLIC",
        "DROP TRIGGER stewardship_debug_off_switch_guard "
        "ON public.stewardship_debug_off_switch",
    ):
        with (
            connection.cursor() as cursor,
            pytest.raises(DatabaseError, match="0088"),
            transaction.atomic(),
        ):
            cursor.execute(change_sql)
            cursor.execute(CHECK)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)
