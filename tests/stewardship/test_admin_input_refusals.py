"""A refused Admin input's closed context, level and process-log line (#1403).

No database: the context's allow-list (``audit.refusal_context``), the
level each reason decides, the ``refusal`` context kind, the process-log
line's re-checked fields, and the writer's line-only paths (an anonymous
refusal, one inside an atomic block, a record that cannot even be read).
The durable entry, its cap and its limits are tested against PostgreSQL in
``database/test_admin_input_refusal_log_postgresql.py``.
"""

import json
import logging
from uuid import uuid4

import pytest

from parishkit.stewardship.audit import refusal_context, refusals
from parishkit.stewardship.audit.refusal_context import clean, complete, is_clean
from parishkit.stewardship.audit.refusals import (
    RefusalRecord,
    exception_class_of,
    level_of,
    record_refusal,
)
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.observability import Event, SafeJsonFormatter, emit
from parishkit.stewardship.storage import StaleRecordError

# Values a refusal must never record: an address, a credential-like token,
# a name and free text. A lower-case word is identifier-shaped and would
# pass as a field name, which is why fields and codes are server-owned names
# only; the shape keeps out what submitted values usually look like.
SENTINELS = (
    "jane.private@example.org",
    "sk_live_Synthetic9Secret",
    "Jane Q. Private",
    "my notes about the family",
)


def lines(caplog):
    """The formatted admin_input_refused and task_timed_out lines."""
    return [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.msg in {Event.ADMIN_INPUT_REFUSED, Event.TASK_TIMED_OUT}
    ]


def test_a_complete_refusal_keeps_every_allowed_key_in_order():
    """Closed values pass through; the entry lists them in one fixed order."""
    values = {
        "mode": "testing",
        "reason": "unexpected_fields",
        "route": "admin:setup_logo",
        "method": "post",
        "status": 400,
        "form": "logo",
        "fields": ["logo", "slots-0-time"],
        "codes": ["duplicate"],
        "unexpected_count": 3,
        "capped": True,
    }
    found = clean(values)
    assert found == values
    assert list(found) == [
        "route",
        "method",
        "status",
        "form",
        "fields",
        "codes",
        "reason",
        "unexpected_count",
        "mode",
        "capped",
    ]
    assert is_clean(found) and complete(found)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # Unknown keys, a path or a query never get in.
        (
            {"route": "admin:home", "reason": "denied", "path": "/admin/?q=x"},
            {"route": "admin:home", "reason": "denied"},
        ),
        # An unknown or missing reason is unclassified, so an entry says why.
        ({"route": "admin:home", "reason": "Because"}, None),
        ({"route": "admin:home"}, None),
        # Upper-case, spaced or overlong identifiers are dropped.
        (
            {"route": "Admin Home", "form": "x" * 65, "reason": "denied"},
            {"reason": "denied"},
        ),
        # Methods, statuses and modes are closed sets.
        (
            {
                "command": "whoami",
                "method": "POST",
                "status": 500,
                "mode": "live",
                "reason": "denied",
            },
            {"command": "whoami", "reason": "denied"},
        ),
        # A bool is not a status, and only true is a cap mark.
        (
            {"command": "whoami", "status": True, "capped": "yes", "reason": "denied"},
            {"command": "whoami", "reason": "denied"},
        ),
        # Both a page and a command: the caller is wrong, so neither is kept.
        (
            {"route": "admin:home", "command": "whoami", "reason": "denied"},
            {"reason": "denied"},
        ),
        # A class name only with unclassified; a count only with its reason.
        (
            {
                "route": "admin:home",
                "reason": "denied",
                "exception_class": "value_error",
                "unexpected_count": 2,
            },
            {"route": "admin:home", "reason": "denied"},
        ),
        # A command name keeps its catalog shape, never arguments.
        (
            {"command": "export create --to /tmp/x", "reason": "usage"},
            {"reason": "usage"},
        ),
    ],
)
def test_anything_off_the_list_is_dropped(values, expected):
    """Dropped, never redacted or refused."""
    if expected is None:
        expected = {"route": "admin:home", "reason": "unclassified"}
    assert clean(values) == expected


def test_fields_and_codes_are_names_at_most_ten_without_repeats():
    """Client text in a list is dropped item by item; the rest stays in order."""
    names = [f"field_{number}" for number in range(12)]
    found = clean(
        {
            "route": "admin:home",
            "reason": "stale_page",
            "fields": ["Jane", "field_0", "field_0", *names],
            "codes": "required",
        }
    )
    assert found["fields"] == names[:10]
    assert "codes" not in found


def test_expected_and_actual_need_a_setting_reason(monkeypatch):
    """Setting values only for an allow-listed reason, and never an address."""
    values = {
        "route": "admin:campaign_create",
        "reason": "stale_page",
        "expected": "Africa/Abidjan",
        "actual": "America/New_York",
    }
    assert "expected" not in clean(values)
    monkeypatch.setattr(refusal_context, "SETTING_REASONS", frozenset({"stale_page"}))
    assert clean(values)["actual"] == "America/New_York"
    assert "actual" not in clean({**values, "actual": "a@example.org"})
    assert "actual" not in clean({**values, "actual": "x" * 65})


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_no_sensitive_value_survives_in_any_key(sentinel):
    """Whatever key carries a sentinel, the cleaned context does not."""
    values = {key: sentinel for key in refusal_context.KEYS}
    values.update(fields=[sentinel], codes=[sentinel], reason="denied")
    assert sentinel not in json.dumps(clean(values))


def test_exception_class_is_the_snake_case_class_name():
    """Its own name only, never the module or text."""
    assert exception_class_of(ValueError("jane@example.org")) == "value_error"
    assert exception_class_of(StaleRecordError()) == "stale_record_error"
    assert exception_class_of(type("HTTPError", (Exception,), {})()) == "http_error"


class LocalDenial(PermissionError):
    """A PermissionError subclass, as the access checks raise."""


@pytest.mark.parametrize(
    ("reason", "error", "level"),
    [
        ("denied", None, "WARNING"),
        ("csrf_failed", None, "WARNING"),
        ("configuration", None, "WARNING"),
        ("fresh_authentication_required", PermissionError(), "INFO"),
        ("stale_page", None, "INFO"),
        ("unclassified", LocalDenial(), "WARNING"),
        ("unclassified", ValueError(), "INFO"),
        ("unclassified", None, "INFO"),
    ],
)
def test_the_reason_decides_the_level(reason, error, level):
    """Not the status: a step-up 403 is INFO, a PermissionError WARNING."""
    assert level_of(reason, error) == level
    record = RefusalRecord(reason=reason, route="admin:home", error=error)
    assert record.level == level


def test_an_unclassified_record_names_its_class():
    """A record with no reason of its own carries its exception's class."""
    record = RefusalRecord(
        reason="not_a_reason",
        route="admin:home",
        method="POST",
        error=ValueError("synthetic-secret"),
    )
    assert record.context() == {
        "route": "admin:home",
        "method": "post",
        "reason": "unclassified",
        "exception_class": "value_error",
    }


def test_the_refusal_context_kind_accepts_only_clean_complete_values():
    """sanitize refuses a value the allow-list would drop, or no page."""
    good = {"route": "admin:home", "reason": "denied", "fields": ["a"]}
    assert sanitize(ContextKind.REFUSAL, dict(good)) == good
    for bad in (
        {"route": "admin:home", "reason": "Denied"},
        {"reason": "denied"},
        {"route": "admin:home", "reason": "denied", "method": "POST"},
    ):
        with pytest.raises(ValueError):
            sanitize(ContextKind.REFUSAL, bad)
    with pytest.raises(ValueError, match="fields"):
        sanitize(ContextKind.REFUSAL, {"route": "admin:home", "url": "/x"})


def test_emit_takes_a_refusal_only_on_its_own_event_and_only_clean():
    """The line's refusal fields are typed like every other emit field."""
    context = {"route": "admin:home", "reason": "denied"}
    with pytest.raises(ValueError):
        emit(Event.TASK_FAILED, refusal=context)
    with pytest.raises(ValueError):
        emit(Event.TASK_FAILED, actor_id=uuid4())
    with pytest.raises(ValueError):
        emit(Event.ADMIN_INPUT_REFUSED, refusal={**context, "note": "x"})
    with pytest.raises(ValueError):
        emit(Event.ADMIN_INPUT_REFUSED, actor_id="jane@example.org")


def test_the_formatter_rechecks_a_hand_built_line(caplog):
    """A record that bypassed emit cannot smuggle text through the line."""
    logger = logging.getLogger("parishkit.stewardship")
    with caplog.at_level(logging.INFO):
        logger.info(
            Event.ADMIN_INPUT_REFUSED,
            extra={
                "extra": {
                    "refusal": {"route": "admin:home", "reason": SENTINELS[0]},
                    "actor_id": SENTINELS[0],
                }
            },
        )
    (line,) = lines(caplog)
    assert "refusal" not in line["extra"] and "actor_id" not in line["extra"]
    assert SENTINELS[0] not in json.dumps(line)


@pytest.mark.parametrize("actor", [None, "atomic"])
def test_a_refusal_with_no_durable_entry_has_its_line(caplog, monkeypatch, actor):
    """Anonymous, or inside an atomic block: the line only, with no actor for
    the anonymous one and the signed-in user's id otherwise. The anonymous
    case is outside any atomic block, so the actor check alone stops it."""
    from django.db import connection

    actor_id = None if actor is None else uuid4()
    if actor is not None:
        monkeypatch.setattr(type(connection), "in_atomic_block", True, raising=False)
    else:
        assert not connection.in_atomic_block

    def refused(*args, **kwargs):
        raise AssertionError("no durable write is attempted")

    monkeypatch.setattr(refusals.transaction, "atomic", refused)
    with caplog.at_level(logging.INFO):
        record_refusal(
            RefusalRecord(
                reason="csrf_failed",
                actor_id=actor_id,
                route="admin:home",
                method="post",
                status=403,
                fields=[SENTINELS[1]],
            )
        )
    (line,) = lines(caplog)
    assert line["level"] == "WARNING"
    assert line["extra"]["refusal"] == {
        "route": "admin:home",
        "method": "post",
        "status": 403,
        "reason": "csrf_failed",
    }
    expected = None if actor_id is None else str(actor_id)
    assert line["extra"].get("actor_id") == expected
    assert SENTINELS[1] not in json.dumps(line)


@pytest.mark.parametrize("value", [None, "name", 7])
def test_fields_and_codes_that_are_not_lists_are_dropped(caplog, value):
    """A caller passing None or a string still gets the refusal's line."""
    with caplog.at_level(logging.INFO):
        record_refusal(
            RefusalRecord(
                reason="denied", route="admin:home", fields=value, codes=value
            )
        )
    (line,) = lines(caplog)
    assert line["extra"]["refusal"] == {"route": "admin:home", "reason": "denied"}


def test_record_refusal_never_raises(caplog, monkeypatch):
    """A record whose context cannot be built logs a failure line instead."""

    def broken(values):
        raise RuntimeError("synthetic-secret")

    monkeypatch.setattr(refusals, "clean", broken)
    with caplog.at_level(logging.INFO):
        record_refusal(RefusalRecord(reason="denied", route="admin:home"))
    (line,) = lines(caplog)
    assert line["level"] == "WARNING"
    assert line["extra"]["failure_kind"] == "unexpected_failure"
    assert "synthetic-secret" not in json.dumps(line)
