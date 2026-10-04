"""Admission through an explicit AdminCaller matches the request path exactly."""

from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import transaction
from django.test import RequestFactory

from parishkit.stewardship.accounts import admin_editing, sessions
from parishkit.stewardship.accounts.admin_caller import (
    AUTOMATION,
    FULL,
    READ_ONLY,
    AdminCaller,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.privileged_actions import (
    _actor,
    admit_admin_action,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.web.refusals import UserFacingDenied

from .auth_builders import signed_in

pytestmark = pytest.mark.django_db(transaction=True)


def store(browser):
    """The signed-in browser's server-side Admin session store."""
    return import_module(settings.SESSION_ENGINE).SessionStore(
        browser.cookies["pk_admin"].value
    )


def web_request(browser, method="get"):
    """A request carrying the browser's Admin session, as middleware builds it."""
    request = getattr(RequestFactory(), method)("/admin/configuration")
    request.session = store(browser)
    return request


def test_web_caller_admission_mirrors_onto_the_request(auth_service, google):
    """A caller admits, renews activity and leaves the request as before."""
    browser, _ = signed_in()
    row = PortalSession.objects.get()
    request = web_request(browser)
    caller = AdminCaller.from_request(request)
    principal = sessions.authenticated_admin(
        caller, store=auth_service.store, activity=True
    )
    assert principal is not None and "administrator" in principal.roles
    assert caller.principal is principal and request.principal is principal
    assert caller.portal_session.pk == row.pk == request.portal_session.pk
    assert PortalSession.objects.get().version == row.version + 1
    assert admin_editing.principal(caller, auth_service) == principal


def test_web_caller_rotation_replaces_the_request_session_and_csrf_token(
    auth_service, google, monkeypatch
):
    """Changed authority still rotates the cookie and CSRF token for the page."""
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    staff = Principal(original.principal_id, frozenset({"staff"}))
    monkeypatch.setattr(sessions, "current_principal", lambda *args: staff)
    request = web_request(browser)
    old = request.session
    caller = AdminCaller.from_request(request)
    assert sessions.authenticated_admin(caller, store=auth_service.store) == staff
    assert request.session is caller.session and caller.session is not old
    assert caller.session.session_key != old.session_key
    assert request.META.get("CSRF_COOKIE_NEEDS_UPDATE") is True
    replacement = PortalSession.objects.get(revoked_at__isnull=True)
    assert replacement.session_id == caller.session.session_key
    assert request.portal_session.pk == replacement.pk != original.pk
    assert replacement.authenticated_at == original.authenticated_at
    assert AuditEvent.objects.filter(event_type="admin_privileges_changed").count() == 1


def test_automation_caller_is_never_rotated(auth_service, google, monkeypatch):
    """Changed authority refuses an automation caller and writes nothing."""
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    staff = Principal(original.principal_id, frozenset({"staff"}))
    monkeypatch.setattr(sessions, "current_principal", lambda *args: staff)
    caller = AdminCaller(
        session=store(browser),
        channel=AUTOMATION,
        automation_session_id=uuid4(),
        scope=FULL,
    )
    assert sessions.authenticated_admin(caller, store=auth_service.store) is None
    assert caller.portal_session is None
    row = PortalSession.objects.get()
    assert row.session_id == original.session_id == caller.session.session_key
    assert row.revoked_at is None
    assert not AuditEvent.objects.filter(event_type="admin_privileges_changed")


def test_read_only_automation_caller_may_still_read(auth_service, google):
    """A read-only scope narrows activity only; passive admission still works."""
    browser, _ = signed_in()
    version = PortalSession.objects.get().version
    caller = AdminCaller(
        session=store(browser),
        channel=AUTOMATION,
        automation_session_id=uuid4(),
        scope=READ_ONLY,
    )
    principal = sessions.authenticated_admin(caller, store=auth_service.store)
    assert principal is not None and caller.principal is principal
    with pytest.raises(PermissionError):
        sessions.authenticated_admin(caller, store=auth_service.store, activity=True)
    assert PortalSession.objects.get().version == version


def test_actor_takes_an_explicit_caller(auth_service, google):
    """The actor comes from the caller's live session, as from its request."""
    browser, _ = signed_in()
    actor = PortalSession.objects.get().principal_id
    request = web_request(browser)
    assert _actor(AdminCaller.from_request(request)) == actor == _actor(request)


def test_privileged_intake_takes_a_state_changing_web_caller(auth_service, google):
    """Only a caller built with the CSRF assertion reaches privileged intake."""
    browser, _ = signed_in()
    request = web_request(browser, "post")
    request.csrf_processing_done = True
    actor = PortalSession.objects.get().principal_id
    plain = AdminCaller.from_request(request)
    asserted = AdminCaller.from_request(request, state_changing=True)
    with transaction.atomic():
        with pytest.raises(PermissionError):
            admit_admin_action(
                plain, actor_id=actor, action=Action.CONFIGURATION_REQUEST
            )
        assert admit_admin_action(
            asserted, actor_id=actor, action=Action.CONFIGURATION_REQUEST
        )
        assert asserted.portal_session.principal_id == actor
        # Destructive intake also needs freshness, now read from the caller.
        assert admit_admin_action(
            asserted, actor_id=actor, action=Action.DESTRUCTIVE_CONFIRMATION
        )


def test_signed_out_caller_is_told_to_sign_in(auth_service):
    """A caller without a live session gets the page's signed-out refusal."""
    request = RequestFactory().get("/admin/")
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    with pytest.raises(UserFacingDenied):
        admin_editing.principal(
            AdminCaller.from_request(request), SimpleNamespace(store=None)
        )
