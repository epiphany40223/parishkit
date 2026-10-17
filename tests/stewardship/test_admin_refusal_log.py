"""The refusal log middleware: which Admin responses it records, and why (#1403).

No database: ``record_refusal`` is replaced by a list, so these tests see
exactly the ``RefusalRecord`` the middleware builds from each response and
the level its reason decides. The durable entry each one leaves is tested
against PostgreSQL in ``database/test_admin_refusal_log_postgresql.py``.
"""

import json
from uuid import uuid4

import pytest
from django.core import signing
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory
from django.urls import resolve

from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.admin_editing import (
    step_up_response as step_up,
)
from parishkit.stewardship.accounts.automation_views import _fields
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.sessions import FreshAuthenticationRequired
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import refusal_log
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.refusal_log import AdminRefusalMiddleware
from parishkit.stewardship.web.refusals import (
    AccessDenied,
    UserFacingDenied,
    UserFacingError,
    UserFacingGone,
    expired_preview,
    mark_not_input,
    mark_recorded,
    reason_of,
    send_time_passed,
    stale_page,
    unexpected_fields,
    with_codes,
    with_default_code,
    with_reason,
)
from parishkit.stewardship.web.security import csrf_failure

ROUTE = "/admin/users/automation/sessions/"
ACTOR = uuid4()


@pytest.fixture
def recorded(monkeypatch):
    """Every RefusalRecord the middleware hands to the writer, in order."""
    records = []
    monkeypatch.setattr(refusal_log, "record_refusal", records.append)
    monkeypatch.setattr(refusal_log, "current_mode", lambda: "testing")
    return records


def request_for(path=ROUTE, *, actor=ACTOR, data=None):
    """A POST to ``path`` as Django routes it, signed in as ``actor``."""
    request = RequestFactory().post(path, data or {})
    try:
        request.resolver_match = resolve(path)
    except Http404:
        request.resolver_match = None
    if actor is not None:
        request.principal = Principal(actor, roles=frozenset({"administrator"}))
    return request


def answer(response, request=None):
    """Pass ``response`` through the middleware for ``request``."""
    request = request or request_for()
    returned = AdminRefusalMiddleware(lambda _: response)(request)
    assert returned is response
    return request


@pytest.mark.parametrize(
    ("error", "reason", "codes", "status", "level", "exception_class"),
    [
        (stale_page(), "stale_page", ["stale_version"], 409, "INFO", None),
        (unexpected_fields(), "unexpected_fields", ["invalid"], 400, "INFO", None),
        (expired_preview(), "expired_preview", ["stale_version"], 409, "INFO", None),
        # A reviewed send time that passed before Apply (#1464).
        (
            send_time_passed(),
            "send_time_passed",
            ["stale_version"],
            409,
            "INFO",
            None,
        ),
        (signing.BadSignature("x"), "bad_signature", ["invalid"], 400, "INFO", None),
        (
            FreshAuthenticationRequired("x"),
            "fresh_authentication_required",
            ["denied"],
            403,
            "INFO",
            None,
        ),
        (UserFacingDenied("Not now."), "denied", ["denied"], 403, "WARNING", None),
        (AccessDenied("x"), "denied", ["denied"], 403, "WARNING", None),
        (
            PermissionError("x"),
            "unclassified",
            ["denied"],
            403,
            "WARNING",
            "permission_error",
        ),
        (ValueError("x"), "unclassified", ["invalid"], 400, "INFO", "value_error"),
        (
            UserFacingError("Check it."),
            "unclassified",
            ["invalid"],
            400,
            "INFO",
            "user_facing_error",
        ),
        (
            StaleRecordError("x"),
            "unclassified",
            ["stale_version"],
            409,
            "INFO",
            "stale_record_error",
        ),
        # Its own closed reason, at INFO, although it is a PermissionError.
        (UserFacingGone("Gone."), "gone", ["gone"], 410, "INFO", None),
        (
            LookupError("x"),
            "unclassified",
            ["unavailable"],
            404,
            "INFO",
            "lookup_error",
        ),
    ],
)
def test_each_shared_refusal_is_recorded_with_its_reason_and_level(
    recorded, error, reason, codes, status, level, exception_class
):
    """``error_response`` carries the reason; the level follows the reason."""
    answer(error_response(error))
    (record,) = recorded
    context = record.context()
    assert context["route"] == "admin:automation_sessions"
    assert context["method"] == "post" and context["status"] == status
    assert context["reason"] == reason and context["codes"] == codes
    assert context.get("exception_class") == exception_class
    assert context["mode"] == "testing"
    assert record.level == level
    assert record.actor_id == ACTOR


def test_a_step_up_answer_is_an_info_fresh_authentication_refusal(recorded):
    """The ordinary first answer to a fresh-gated action (spec: INFO)."""
    answer(step_up("/admin/", "Home"))
    (record,) = recorded
    assert record.context()["reason"] == "fresh_authentication_required"
    assert record.level == "INFO"


def test_a_csrf_failure_is_a_warning_with_its_closed_reason_only(recorded):
    """Django's reason text is never used."""
    request = request_for()
    answer(csrf_failure(request, reason="CSRF token from POST incorrect."), request)
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "csrf_failed" and context["status"] == 403
    assert record.level == "WARNING"
    assert "incorrect" not in json.dumps(context)


def test_a_real_csrf_failure_reaches_the_hook(recorded):
    """Django's CSRF check refuses in ``process_view``, inside the hook."""
    response = Client(enforce_csrf_checks=True).post(ROUTE, {"session_id": "x"})
    assert response.status_code == 403
    (record,) = recorded
    assert record.context()["reason"] == "csrf_failed"
    assert record.context()["route"] == "admin:automation_sessions"
    # Nobody is signed in: the line only, with no actor.
    assert record.actor_id is None


def test_a_submitted_extra_key_never_appears_in_fields(recorded):
    """``fields`` holds only server-owned names, never a submitted key."""
    extra = "zz_submitted_extra_key"
    request = request_for(data={"session_id": "x", extra: "value"})

    def view(request):
        """Refuse as the Automation sessions view does."""
        try:
            _fields(request.POST, {"csrfmiddlewaretoken", "session_id"})
        except ValueError as error:
            return error_response(error)
        raise AssertionError("The extra key was not refused.")

    AdminRefusalMiddleware(view)(request)
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "unexpected_fields"
    assert "fields" not in context
    assert extra not in json.dumps(context) and "value" not in json.dumps(context)


def test_fields_are_the_typed_errors_server_owned_identifiers(recorded):
    """A FieldError's identifier, fixed by the view, is a refused field."""
    answer(
        validation_response(
            [
                FieldError(ErrorCode.REQUIRED, "name"),
                FieldError(ErrorCode.INVALID, "slots-0-time"),
            ]
        )
    )
    (record,) = recorded
    assert record.context()["fields"] == ["name", "slots-0-time"]
    assert record.context()["codes"] == ["required", "invalid"]


def test_a_view_exception_django_answers_names_its_class(recorded):
    """An uncaught ``Http404`` reaches the hook through ``process_exception``."""
    request = request_for()
    middleware = AdminRefusalMiddleware(lambda _: HttpResponse(status=404))
    assert middleware.process_exception(request, Http404("x")) is None
    middleware(request)
    (record,) = recorded
    assert record.context()["reason"] == "unclassified"
    assert record.context()["exception_class"] == "http404"


@pytest.mark.parametrize("target", ["request", "response"])
def test_a_refusal_its_own_code_recorded_is_skipped(recorded, target):
    """A refused sign-in or automation session keeps only its own record."""
    request = request_for()
    response = error_response(PermissionError("x"))
    mark_recorded(request if target == "request" else response)
    answer(response, request)
    assert recorded == []


def test_a_greyed_out_page_opened_directly_is_not_input(recorded):
    """The menu's reason page answers a visit, not submitted input."""
    answer(mark_not_input(error_response(stale_page())))
    assert recorded == []


def test_only_typed_refusals_carry_a_reason():
    """Another exception's own ``reason`` attribute is never trusted."""

    class Foreign(PermissionError):
        reason = "denied"

    assert reason_of(Foreign()) is None
    assert reason_of(AccessDenied()) == "denied"
    assert reason_of(FreshAuthenticationRequired()) == ("fresh_authentication_required")


def test_the_response_keeps_only_the_exception_class(recorded):
    """The refused exception, and so its text, is not kept on the response."""
    response = error_response(ValueError("secret text"))
    assert not hasattr(response, "stewardship_exception")
    assert response.stewardship_exception_class is ValueError


@pytest.mark.parametrize(
    ("path", "status"),
    [
        # A Family page is not an Admin page.
        ("/family/", 400),
        # An address no Admin page answers is not input.
        ("/admin/no/such/page/", 404),
        # Only a refusal's statuses: not success, not an outage.
        (ROUTE, 200),
        (ROUTE, 302),
        (ROUTE, 503),
    ],
)
def test_other_answers_are_not_refusals(recorded, path, status):
    """Only a resolved Admin page answered 400/403/404/409/410/422 counts."""
    answer(HttpResponse(status=status), request_for(path))
    assert recorded == []


def test_an_untyped_admin_refusal_is_unclassified(recorded, settings):
    """A plain 400 page, as production records it.

    The strict test settings refuse such an answer (#1444); this test turns
    that off to show what production records for one.
    """
    settings.STEWARDSHIP_REFUSAL_LOG_STRICT = False
    answer(HttpResponse(status=400))
    (record,) = recorded
    assert record.context()["reason"] == "unclassified"
    assert "exception_class" not in record.context()
    assert record.level == "INFO"


def test_the_strict_settings_note_a_refusal_that_says_nothing(recorded):
    """A deliberately bare 400 is noted for the test hook (#1444).

    Nothing is raised, so the answer and the connection stay as they were;
    the record is still written. This test takes the note itself, so the
    hook (``tests/stewardship/conftest.py``) does not fail it.
    """
    assert refusal_log.settings.STEWARDSHIP_REFUSAL_LOG_STRICT is True
    answer(HttpResponse(status=400))
    assert len(recorded) == 1
    (violation,) = refusal_log.take_strict_violations()
    assert violation.startswith("admin:automation_sessions answered 400")
    assert refusal_log.take_strict_violations() == []


@pytest.mark.parametrize(
    "response",
    [
        with_reason(HttpResponse(status=400), "stale_page"),
        with_codes(HttpResponse(status=409), "busy"),
        validation_response([FieldError(ErrorCode.INVALID, "name")]),
    ],
    ids=["reason", "codes", "typed error"],
)
def test_the_strict_settings_accept_a_reason_codes_or_typed_errors(recorded, response):
    """Any one of a reason, codes or a typed error is enough."""
    answer(response)
    assert len(recorded) == 1


def test_the_strict_settings_accept_an_unclassified_exception(recorded):
    """An unclassified refusal that names its exception's class says why."""
    request = request_for()
    middleware = AdminRefusalMiddleware(lambda _: HttpResponse(status=403))
    middleware.process_exception(request, PermissionError())
    middleware(request)
    (record,) = recorded
    assert record.context()["exception_class"] == "permission_error"


def test_an_anonymous_refusal_has_no_actor(recorded, monkeypatch):
    """No principal and no live session: the writer keeps the line only."""
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.signed_in_sessions.live_principal",
        lambda session: None,
    )
    answer(error_response(stale_page()), request_for(actor=None))
    (record,) = recorded
    assert record.actor_id is None


def test_a_refusal_before_admission_names_the_live_session_user(recorded, monkeypatch):
    """A CSRF failure has no principal yet: the live session names the user."""
    other = uuid4()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.signed_in_sessions.live_principal",
        lambda session: other,
    )
    request = request_for(actor=None)
    answer(csrf_failure(request), request)
    (record,) = recorded
    assert record.actor_id == other


def test_the_hook_never_raises(monkeypatch):
    """A record that cannot be built leaves the answer unchanged."""

    def broken(request, response):
        raise RuntimeError("broken")

    monkeypatch.setattr(refusal_log, "refusal_record", broken)
    response = error_response(stale_page())
    assert AdminRefusalMiddleware(lambda _: response)(request_for()) is response


def test_a_refusal_reason_must_be_closed():
    """A new kind of refusal adds its reason code to the closed set first."""
    with pytest.raises(TypeError):
        UserFacingError("Check it.", reason="not_a_reason")
    assert UserFacingError("Check it.", reason="stale_page").reason == "stale_page"


def test_a_pages_default_code_is_recorded_only_when_nothing_else_is(recorded):
    """A page drawn again with a refusal of its own: its status's code (and
    any of its own) say why only when no typed error or form code does."""
    answer(with_default_code(HttpResponse(status=409), "in_use"))
    answer(with_codes(with_default_code(HttpResponse(status=400)), "busy"))
    first, second = (record.context()["codes"] for record in recorded)
    assert first == ["stale_version", "in_use"]
    assert second == ["busy"]
