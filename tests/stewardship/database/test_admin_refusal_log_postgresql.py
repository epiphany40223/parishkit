"""Refused Admin page requests leave their durable entry (#1403).

Real requests through the real middleware stack: the hook runs just inside
the error pages, after the view's transaction, and writes on the request's
own connection. Django's ``TestCase`` wraps each test in an atomic block, in
which the writer correctly writes nothing, so these are transactional tests.
Refusals that already keep a record of their own (a refused sign-in, a
refused automation session) get no line and no entry.
"""

import json
import logging
from uuid import uuid4

import pytest
from django.test import Client, RequestFactory
from django.urls import resolve

from parishkit.stewardship.accounts.admin_editing import step_up_response
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.observability import Event
from parishkit.stewardship.web.refusal_log import AdminRefusalMiddleware

from .auth_builders import signed_in
from .automation_builders import post, two_admins  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
SESSIONS = "/admin/users/automation/sessions/"


def entries():
    """Every durable refusal entry, oldest first."""
    return list(
        OperationalLog.objects.filter(event="admin_input_refused").order_by(
            "created_at", "id"
        )
    )


def refusal_lines(caplog):
    """The admin_input_refused process-log lines."""
    return [r for r in caplog.records if r.msg is Event.ADMIN_INPUT_REFUSED]


def user_id(email="admin@example.org"):
    """The PortalUser id the entry names."""
    return PortalUser.objects.get(email=email).pk


def test_an_extra_submitted_key_is_counted_never_named(auth_service, google):
    """The entry says what was refused; a submitted key never reaches it."""
    browser, _ = signed_in()
    extra = "zz_submitted_extra_key"
    response = post(browser, SESSIONS, session_id=str(uuid4()), **{extra: "value"})
    assert response.status_code == 400
    (entry,) = entries()
    assert entry.level == "INFO" and entry.actor_id == user_id()
    assert entry.context == {
        "route": "admin:automation_sessions",
        "method": "post",
        "status": 400,
        "codes": ["invalid"],
        "reason": "unexpected_fields",
        # The extra key is counted, never named (#1403, PR 5).
        "unexpected_count": 1,
        "mode": "testing",
    }
    assert extra not in json.dumps(entry.context)


def test_a_signed_in_csrf_failure_names_the_user_at_warning(auth_service, google):
    """No view ran, so the request's live session names who was refused."""
    browser, _ = signed_in()
    response = browser.post(SESSIONS, {"session_id": str(uuid4())})
    assert response.status_code == 403
    (entry,) = entries()
    assert entry.level == "WARNING" and entry.actor_id == user_id()
    assert entry.context["reason"] == "csrf_failed"
    assert entry.context["route"] == "admin:automation_sessions"


def test_an_anonymous_csrf_failure_has_the_line_only(auth_service, caplog):
    """Public traffic never allocates a durable row."""
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    response = Client(enforce_csrf_checks=True).post(SESSIONS, {"session_id": "x"})
    assert response.status_code == 403
    assert entries() == []
    (line,) = refusal_lines(caplog)
    assert line.levelno == logging.WARNING


def test_a_permission_refusal_is_a_warning(two_admins, google):  # noqa: F811
    """Staff may not revoke automation sessions: a denial, at WARNING."""
    google[0].update(email="staff@example.org", sub="staff-subject")
    browser, _ = signed_in()
    response = post(browser, SESSIONS, session_id=str(uuid4()))
    assert response.status_code == 403
    (entry,) = entries()
    assert entry.level == "WARNING"
    assert entry.actor_id == user_id("staff@example.org")
    assert entry.context["codes"] == ["denied"]
    assert entry.context["reason"] == "denied"


def test_a_step_up_answer_is_written_at_info():
    """A fresh-gated action's first answer, through the hook (spec: INFO)."""
    actor = uuid4()
    request = RequestFactory().post(SESSIONS)
    request.resolver_match = resolve(SESSIONS)
    request.principal = Principal(actor, roles=frozenset({"administrator"}))
    response = AdminRefusalMiddleware(lambda _: step_up_response("/admin/", "Home"))(
        request
    )
    assert response.status_code == 403
    (entry,) = entries()
    assert entry.level == "INFO" and entry.actor_id == actor
    assert entry.context["reason"] == "fresh_authentication_required"


def test_a_greyed_out_menu_page_opened_directly_is_not_recorded(
    auth_service, google, caplog
):
    """No current campaign greys out Campaign settings: a visit, not input."""
    from django.urls import reverse

    browser, _ = signed_in()
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    response = browser.get(
        reverse("admin:campaign_settings"),
        HTTP_ACCEPT="text/html",
        HTTP_SEC_FETCH_MODE="navigate",
    )
    assert response.status_code == 409
    assert entries() == [] and refusal_lines(caplog) == []


def test_a_refused_sign_in_keeps_only_its_own_record(auth_service, google, caplog):
    """admin_login_refused stands for the refusal: no line and no entry."""
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    google[0].update(email="outsider@example.net", sub="outsider-subject")
    assert signed_in()[1].status_code == 403
    assert entries() == [] and refusal_lines(caplog) == []


def test_a_refused_automation_session_keeps_only_its_own_record(
    auth_service, google, caplog
):
    """automation_session_refused stands for the refusal: no line, no entry."""
    from parishkit.stewardship.audit.models import AuditEvent

    from .automation_builders import command, paired

    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    intruder = Client(enforce_csrf_checks=True)
    # A real CSRF token first, so the view's admission is what refuses.
    assert intruder.get("/admin/login").status_code == 200
    intruder.cookies["pk_admin"] = caller.session.session_key
    response = post(intruder, SESSIONS, session_id=str(uuid4()))
    assert response.status_code == 403
    assert AuditEvent.objects.filter(
        event_type="automation_session_refused", subject_id=row.pk
    ).exists()
    assert entries() == [] and refusal_lines(caplog) == []


def test_a_strict_violation_leaves_the_connection_usable(
    auth_service, google, monkeypatch
):
    """A refusal that says nothing is noted, never raised (#1444).

    The Logs page's error is replaced by a bare 400 so the strict check has
    something to note. The request still answers, its entry is written, and
    the same connection serves the next query and the next request; the
    note is taken here so the test hook does not fail this test.
    """
    from django.db import connection
    from django.http import HttpResponse

    from parishkit.stewardship.audit import log_views
    from parishkit.stewardship.web import refusal_log

    browser, _ = signed_in()
    monkeypatch.setattr(log_views, "_error", lambda *_, **__: HttpResponse(status=400))
    response = browser.get("/admin/system/logs/?correlation=x")
    assert response.status_code == 400
    (violation,) = refusal_log.take_strict_violations()
    assert violation.startswith("admin:logs answered 400")
    (entry,) = entries()
    assert entry.context["reason"] == "unclassified"
    # The same connection is still open and outside any transaction.
    assert connection.connection is not None and not connection.in_atomic_block
    assert PortalUser.objects.filter(email="admin@example.org").exists()
    assert browser.get("/admin/system/logs/").status_code == 200
