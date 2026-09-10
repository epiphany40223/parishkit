"""Reject unbounded/filter/type ambiguity and never reflect submitted values."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.audit.schemas import ContextKind, Outcome, sanitize
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    PageWindow,
    check_version,
    expected_version,
    filters,
    validation_response,
)


@pytest.mark.parametrize("kind", list(ContextKind))
def test_secret_fields_rejected_for_every_context(kind):
    for key in ("url", "token", "message", "exception", "family_code", "password"):
        with pytest.raises(ValueError, match="fields"):
            sanitize(kind, {key: "synthetic-private"})
    assert sanitize(kind, {"outcome": Outcome.DENIED}) == {"outcome": "denied"}


@pytest.mark.parametrize("value", ["synthetic-secret", None, 1, True])
def test_arbitrary_values_cannot_masquerade_as_safe_outcomes(value):
    with pytest.raises(ValueError):
        sanitize(ContextKind.REQUEST, {"outcome": value})


def test_context_value_shapes_are_strict():
    identifier = uuid4()
    assert sanitize(ContextKind.TASK, {"task_id": identifier}) == {
        "task_id": str(identifier)
    }
    for value in (True, -1, 2**64, 1.2, "1"):
        with pytest.raises(ValueError):
            sanitize(ContextKind.TASK, {"count": value})
    for kind, data in [
        (ContextKind.REQUEST, {"method": "private"}),
        (ContextKind.PROVIDER, {"status": 999}),
        (ContextKind.REQUEST, {"source_fingerprint": "private"}),
        (ContextKind.EXCEPTION, {"retryable": 1}),
    ]:
        with pytest.raises(ValueError):
            sanitize(kind, data)


def test_errors_are_bounded_safe_and_machine_readable():
    response = validation_response([FieldError(ErrorCode.STALE)], status=409)
    assert response.status_code == 409
    assert b"stale_version" in response.content
    assert response["Cache-Control"] == "no-store"
    assert (
        validation_response([FieldError(ErrorCode.UNAVAILABLE)], status=503)[
            "Retry-After"
        ]
        == "5"
    )
    for value in ("<script>", "#field", "field name"):
        with pytest.raises(ValueError):
            FieldError(ErrorCode.INVALID, value)
    with pytest.raises(ValueError):
        validation_response([])
    with pytest.raises(ValueError):
        validation_response([FieldError(ErrorCode.INVALID)], status=200)


@pytest.mark.parametrize(
    "value", [None, True, 1, "0", "-1", "01", " 1", "1.0", str(2**63)]
)
def test_expected_version_requires_canonical_bounded_decimal(value):
    with pytest.raises(ValueError):
        expected_version(value)


def test_version_helpers_require_locked_record_version_and_not_browser_flags():
    assert expected_version("42") == 42
    assert check_version(SimpleNamespace(version=42), 42) is None
    with pytest.raises(StaleRecordError):
        check_version(SimpleNamespace(version=43), 42)
    with pytest.raises(ValueError):
        check_version(SimpleNamespace(version=1), True)


def test_page_window_and_filters_are_bounded():
    assert PageWindow(2, 2).rows([1, 2, 3, 4, 5]) == ([3, 4], True)
    assert PageWindow().rows([]) == ([], False)
    for values in ((0, 50), (1, 101), (True, 1), (10001, 1)):
        with pytest.raises(ValueError):
            PageWindow(*values)
    assert filters(QueryDict("name=Example"), allowed={"name"}) == {"name": "Example"}
    for query in ("name=one&name=two", "unexpected=one", "name=" + "x" * 201):
        with pytest.raises(ValueError):
            filters(QueryDict(query), allowed={"name"})
