"""Fast closed-input and private-recovery contracts for Staff follow-up."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.http import QueryDict
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.reports import information_views
from parishkit.stewardship.reports.information import InformationQuery


@pytest.mark.parametrize(
    "values",
    [
        "search=a&search=b",
        "email=private@example.org",
        "sort=notes",
        "page=0",
        "page=01",
        "page=10001",
        "page=１２",
        "disposition=deleted",
        "needed=true",
        "completed=1",
        "start=20260101",
        "end=not-a-date",
        "start=2026-02-01&end=2026-01-01",
        "search=" + "x" * 201,
    ],
)
def test_information_filters_are_closed_bounded_and_canonical(values):
    """Malformed filters never select arbitrary columns or unbounded offsets."""
    with pytest.raises(ValueError):
        InformationQuery.parse(QueryDict(values))


def test_private_query_values_are_post_state_not_repr():
    """Pagination retains private text only as form data, not a printable URL."""
    query = InformationQuery.parse(
        QueryDict("search=private+Family&disposition=all&page=2")
    )
    assert query.form_values()["search"] == "private Family"
    assert "page" not in query.form_values()
    assert "private Family" not in repr(query)


@pytest.mark.parametrize(
    "extra",
    [
        "&notes=duplicate",
        "&unknown=yes",
        "&followed_up=true",
        "&confirm_clear=no",
        "&follow_up_needed=yes&follow_up_needed=yes",
    ],
)
def test_workflow_command_rejects_duplicate_unknown_or_ambiguous_values(extra):
    """Native checkbox parsing does not turn arbitrary strings into approval."""
    values = QueryDict(f"expected_version=1&request_key={uuid4()}&notes=text" + extra)
    with pytest.raises(ValueError):
        information_views.change_values(values)


def test_unchecked_flags_are_false_and_notes_remain_plain_text():
    """The server preserves submitted text while enforcing checkbox grammar."""
    key = uuid4()
    result = information_views.change_values(
        QueryDict(f"expected_version=2&request_key={key}&notes=%3Cscript%3E")
    )
    assert result == dict(
        expected_version=2,
        request_key=key,
        notes="<script>",
        followed_up=False,
        follow_up_needed=False,
        confirm_clear=False,
    )


@pytest.mark.parametrize("action", ["queue", "detail", "update"])
def test_information_outage_recovery_has_no_private_values(monkeypatch, action):
    """Recovery works even if database-backed layout context cannot be loaded."""
    monkeypatch.setattr(
        information_views, "runtime", Mock(side_effect=DatabaseError("private value"))
    )
    campaign, item = uuid4(), uuid4()
    factory = RequestFactory()
    if action == "queue":
        response = information_views.queue(factory.get("/"), campaign)
    elif action == "detail":
        response = information_views.detail(factory.get("/"), campaign, item)
    else:
        response = information_views.update(factory.post("/"), campaign, item)
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response["Retry-After"] == "5"
    assert response.stewardship_safe_error
    assert b"private value" not in response.content
    # The way back names no campaign: reports show the current one (NAV-11).
    assert reverse("admin:information_queue").encode() in response.content


def test_workflow_get_cannot_mutate():
    """Prefetch or navigation must not record a Staff edit."""
    response = information_views.update(RequestFactory().get("/"), uuid4(), uuid4())
    assert response.status_code == 405


def test_native_multiline_notes_keep_browser_length_and_stable_resaves():
    """Transport CRLF must not count twice or change the replay intent."""
    values = QueryDict("", mutable=True)
    values.update(expected_version="1", request_key=str(uuid4()))
    canonical = "x\n" * 2500
    values["notes"] = canonical.replace("\n", "\r\n")
    first = information_views.change_values(values)
    assert first["notes"] == canonical and len(first["notes"]) == 5000
    values["notes"] = first["notes"].replace("\n", "\r\n")
    assert information_views.change_values(values) == first
    values["notes"] = "one\rtwo"
    assert information_views.change_values(values)["notes"] == "one\ntwo"


def test_name_families_names_rows_from_their_snapshot_as_the_sql_does(monkeypatch):
    """The page and the export name Families from the rows' own snapshot (#932).

    A Family without a surname falls back to "Family", as the report's SQL
    names it, rather than the helper's general default.
    """
    from parishkit.stewardship.reports import information

    calls = []

    def name_rows(snapshot_id, rows, default):
        """Record the call and add heads, as the shared helper does."""
        calls.append((snapshot_id, default))
        for row in rows:
            row["family_name"] += ", Ann and Bo"
        return rows

    monkeypatch.setattr(information, "name_rows", name_rows)
    result = {
        "metadata": {"source_id": "snap"},
        "rows": [{"family_duid": 1, "family_name": "Example"}],
    }
    assert information.name_families(result) is result
    assert result["rows"][0]["family_name"] == "Example, Ann and Bo"
    assert calls == [("snap", "Family")]
