"""The AdminCaller seam: construction rules, request mirroring and narrowing."""

import dataclasses
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.utils import timezone

from parishkit.stewardship.accounts import admin_caller, privileged_actions, sessions
from parishkit.stewardship.accounts.admin_caller import (
    AUTOMATION,
    FULL,
    READ_ONLY,
    WEB,
    AdminCaller,
)
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.observability import correlation

SOURCE = Path(admin_caller.__file__).parents[2]


def automation(scope=FULL, **fields):
    """An automation caller; production gains its constructor with sessions."""
    return AdminCaller(
        session=SimpleNamespace(session_key="command"),
        channel=AUTOMATION,
        automation_session_id=uuid4(),
        scope=scope,
        **fields,
    )


def csrf_post(path="/admin/configuration"):
    """A POST carrying the CSRF middleware's server-owned processed marker."""
    request = RequestFactory().post(path)
    request.csrf_processing_done = True
    return request


def exempt(request):
    """Mark a request as exempt from CSRF enforcement, as test clients do."""
    request._dont_enforce_csrf_checks = True
    return request


def test_web_caller_carries_the_request_state():
    """A view's earlier admission travels with the caller it builds."""
    request = RequestFactory().get("/admin/")
    request.session = SimpleNamespace(session_key="key")
    request.portal_session, request.principal = object(), object()
    caller = AdminCaller.from_request(request)
    assert caller.channel == WEB and caller.scope == FULL
    assert caller.automation_session_id is None and not caller.read_only
    assert caller.session is request.session
    assert caller.portal_session is request.portal_session
    assert caller.principal is request.principal
    assert caller.correlation_id is None and not caller.state_changing


def test_web_caller_uses_only_the_bound_correlation():
    """A caller names the request's correlation and never invents one."""
    identifier = uuid4()
    with correlation(identifier):
        caller = AdminCaller.from_request(RequestFactory().get("/admin/"))
    assert caller.correlation_id == identifier


def test_web_caller_without_admission_or_session_has_neither():
    """A request that never reached admission yields an unadmitted caller."""
    caller = AdminCaller.from_request(RequestFactory().get("/admin/"))
    assert (caller.session, caller.portal_session, caller.principal) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: RequestFactory().get("/admin/configuration"),
        lambda: csrf_post("/family/configuration"),
        lambda: RequestFactory().post("/admin/configuration"),
        lambda: exempt(csrf_post()),
    ],
    ids=["get", "outside-admin", "csrf-unprocessed", "csrf-exempt"],
)
def test_state_changing_web_caller_requires_the_csrf_post(build):
    """Only a CSRF-processed Admin POST may build a state-changing caller."""
    request = build()
    with pytest.raises(PermissionError):
        AdminCaller.from_request(request, state_changing=True)
    with pytest.raises(PermissionError):
        admin_caller.as_caller(request, state_changing=True)


def test_state_changing_web_caller_accepts_the_csrf_post():
    """The assertion is recorded so later privileged intake can rely on it."""
    caller = AdminCaller.from_request(csrf_post(), state_changing=True)
    assert caller.state_changing
    assert admin_caller.as_caller(caller, state_changing=True) is caller


def test_conversion_keeps_callers_and_refuses_an_unasserted_web_caller():
    """A web caller built without the CSRF assertion cannot reach intake."""
    caller = AdminCaller.from_request(csrf_post())
    assert admin_caller.as_caller(caller) is caller
    with pytest.raises(PermissionError):
        admin_caller.as_caller(caller, state_changing=True)
    full = automation()
    assert admin_caller.as_caller(full, state_changing=True) is full


@pytest.mark.parametrize(
    "name",
    [
        "channel",
        "automation_session_id",
        "scope",
        "_request",
        "_state_changing",
        "_constructed",
    ],
)
def test_channel_and_scope_are_fixed_at_construction(name):
    """Nothing after construction can widen a caller's channel or scope."""
    caller = automation(scope=READ_ONLY)
    with pytest.raises(AttributeError):
        setattr(caller, name, None)
    assert caller.read_only and caller.channel == AUTOMATION


@pytest.mark.parametrize(
    "fields",
    [
        {"channel": "browser", "_request": object()},
        {"channel": WEB},
        {"channel": WEB, "_request": object(), "scope": READ_ONLY},
        {"channel": WEB, "_request": object(), "automation_session_id": uuid4()},
        {"channel": AUTOMATION},
        {"channel": AUTOMATION, "automation_session_id": uuid4()},
        {"channel": AUTOMATION, "automation_session_id": uuid4(), "scope": "all"},
        {
            "channel": AUTOMATION,
            "automation_session_id": uuid4(),
            "_request": object(),
        },
        {
            "channel": AUTOMATION,
            "automation_session_id": uuid4(),
            "_state_changing": True,
        },
    ],
)
def test_incoherent_callers_are_refused(fields):
    """A caller is web with a request, or automation with a session and scope."""
    with pytest.raises(ValueError):
        AdminCaller(session=None, **fields)


def test_web_caller_defaults_to_full_scope():
    """Only automation must name its scope; the web is always full."""
    caller = AdminCaller(session=None, channel=WEB, _request=object())
    assert caller.scope == FULL and not caller.read_only


@pytest.mark.parametrize(
    "build",
    [
        lambda: RequestFactory().get("/admin/configuration"),
        lambda: csrf_post("/family/configuration"),
        lambda: RequestFactory().post("/admin/configuration"),
        lambda: exempt(csrf_post()),
    ],
    ids=["get", "outside-admin", "csrf-unprocessed", "csrf-exempt"],
)
def test_construction_rechecks_the_csrf_post(build):
    """Direct construction or replace cannot skip the CSRF assertion."""
    request = build()
    with pytest.raises(PermissionError):
        AdminCaller(session=None, channel=WEB, _request=request, _state_changing=True)
    plain = AdminCaller.from_request(request)
    with pytest.raises(PermissionError):
        dataclasses.replace(plain, _state_changing=True)


def test_replace_keeps_a_valid_csrf_assertion():
    """Replacing an asserted caller re-runs the check and still passes it."""
    caller = AdminCaller.from_request(csrf_post(), state_changing=True)
    assert dataclasses.replace(caller, campaign_id=1).state_changing


def test_only_admin_caller_constructs_callers_in_production():
    """Production builds callers through from_request (later from_automation)."""
    pattern = re.compile(r"\bAdminCaller\s*\(")
    offenders = [
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if path.name != "admin_caller.py" and pattern.search(path.read_text())
    ]
    assert offenders == []


def test_from_automation_is_called_only_from_admin_cli():
    """The command-line constructor has one caller, the host command line."""
    offenders = [
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if path.name not in {"admin_cli.py", "admin_caller.py"}
        and "from_automation(" in path.read_text()
    ]
    assert offenders == []


def test_admission_is_mirrored_onto_the_web_request_only():
    """Views keep reading request.portal_session and request.principal."""
    request = RequestFactory().get("/admin/")
    caller = AdminCaller.from_request(request)
    row, principal = object(), object()
    caller.admitted(row, principal)
    assert (caller.portal_session, caller.principal) == (row, principal)
    assert (request.portal_session, request.principal) == (row, principal)
    command = automation()
    command.admitted(row, principal)
    assert (command.portal_session, command.principal) == (row, principal)


def test_session_replacement_rotates_the_web_csrf_token(monkeypatch):
    """Authority rotation keeps today's cookie and CSRF token replacement."""
    rotated = []
    monkeypatch.setattr(admin_caller, "rotate_token", rotated.append)
    request = RequestFactory().get("/admin/")
    caller = AdminCaller.from_request(request)
    replacement = SimpleNamespace(session_key="new")
    caller.replace_session(replacement)
    assert caller.session is replacement and request.session is replacement
    assert rotated == [request]
    with pytest.raises(PermissionError):
        automation().replace_session(replacement)
    assert rotated == [request]


def test_require_fresh_takes_a_web_caller(monkeypatch):
    """A caller and the request it came from give the same freshness answer."""
    instant = timezone.now()
    request = RequestFactory().get("/admin/")
    request.portal_session = SimpleNamespace(authenticated_at=instant)
    monkeypatch.setattr(sessions, "database_now", lambda: instant + timedelta(0, 299))
    caller = AdminCaller.from_request(request)
    assert sessions.require_fresh(caller) == sessions.require_fresh(request) == instant
    monkeypatch.setattr(sessions, "database_now", lambda: instant + timedelta(0, 301))
    with pytest.raises(sessions.FreshAuthenticationRequired):
        sessions.require_fresh(caller)


@pytest.mark.parametrize("scope", [FULL, READ_ONLY])
def test_require_fresh_refuses_an_unadmitted_or_read_only_automation_caller(
    monkeypatch, scope
):
    """Without an admitted principal, or with read-only scope, no lookup admits.

    A fresh-looking command session alone never stands in for a sign-in; the
    full-scope branch is covered against the database
    (test_automation_fresh_guards_postgresql.py).
    """
    instant = timezone.now()
    monkeypatch.setattr(sessions, "database_now", lambda: instant)
    caller = automation(scope, portal_session=SimpleNamespace(authenticated_at=instant))
    with pytest.raises(sessions.FreshAuthenticationRequired):
        sessions.require_fresh(caller)


def test_read_only_automation_never_records_activity():
    """The scope is enforced in admission itself, before any session lookup."""
    with pytest.raises(PermissionError):
        sessions.authenticated_admin(automation(READ_ONLY), store=None, activity=True)


def test_read_only_automation_is_refused_privileged_intake(monkeypatch):
    """Privileged intake refuses a read-only scope before reading anything."""
    monkeypatch.setattr(
        privileged_actions,
        "connection",
        SimpleNamespace(in_atomic_block=True),
    )

    def unexpected():
        """Admission must refuse before it reaches the runtime."""
        raise AssertionError("read-only scope reached the runtime")

    monkeypatch.setattr(privileged_actions, "runtime", unexpected)
    with pytest.raises(PermissionError):
        privileged_actions.admit_admin_action(
            automation(READ_ONLY),
            actor_id=uuid4(),
            action=Action.CONFIGURATION_REQUEST,
        )
