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
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED


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


def test_date_filters_need_the_browsers_zone_and_an_unused_one_is_dropped():
    """Days are the viewer's browser zone (#558): never guessed, never another."""
    from parishkit.stewardship.web.dates import UnknownZone

    for zone in ("", "Etc/Unknown", "UTC ", "../../etc/passwd"):
        with pytest.raises(UnknownZone):
            InformationQuery.parse({"start": "2026-01-01", "zone": zone})
    assert InformationQuery.parse({"zone": "Mars/Base"}).zone == ""
    query = InformationQuery.parse({"end": "2026-01-01", "zone": "Pacific/Auckland"})
    assert query.form_values()["zone"] == "Pacific/Auckland"


def test_a_retained_capture_from_before_zones_is_retried_unchanged():
    """A pre-0064 capture's filters have no zone; its retry asks for the same."""
    legacy = {
        "search": "",
        "disposition": "all",
        "needed": "any",
        "completed": "any",
        "start": "2026-01-01",
        "end": "2026-01-31",
        "sort": "newest",
    }
    query = InformationQuery.retained(legacy)
    assert query.zone is None and query.form_values() == legacy
    zoned = legacy | {"zone": "America/Chicago"}
    assert InformationQuery.retained(zoned).form_values() == zoned


@pytest.mark.parametrize("action", ["queue", "detail"])
def test_information_outage_recovery_has_no_private_values(monkeypatch, action):
    """Recovery works even if database-backed layout context cannot be loaded."""
    monkeypatch.setattr(
        information_views, "runtime", Mock(side_effect=DatabaseError("private value"))
    )
    campaign, item = uuid4(), uuid4()
    factory = RequestFactory()
    if action == "queue":
        response = information_views.queue(factory.get("/"), campaign)
    else:
        response = information_views.detail(factory.get("/"), campaign, item)
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response["Retry-After"] == "5"
    assert response.stewardship_safe_error
    assert b"private value" not in response.content
    # The way back names no campaign: reports show the current one (NAV-11).
    assert reverse("admin:information_queue").encode() in response.content


def test_the_retired_request_save_is_gone():
    """A request's Save route is retired with no redirect (#1685): decisions
    post to the worklist itself, and a GET of the worklist decides nothing."""
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("admin:information_update", args=[uuid4()])
    assert not hasattr(information_views, "update")


def test_name_families_names_rows_from_their_snapshot_as_the_sql_does(monkeypatch):
    """The page and the export name Families from the rows' own snapshot (#932).

    A Family without a surname falls back to "Family", as the report's SQL
    names it, rather than the helper's general default.
    """
    from parishkit.stewardship.reports import information

    calls = []

    def name_file_rows(metadata, rows, default):
        """Record the call and add heads, as the shared helper does."""
        calls.append((metadata["source_id"], default))
        for row in rows:
            row["family_name"] += ", Ann and Bo"

    monkeypatch.setattr(information, "name_file_rows", name_file_rows)
    result = {
        "metadata": {"source_id": "snap"},
        "rows": [{"family_duid": 1, "family_name": "Example"}],
    }
    assert information.name_families(result) is result
    assert result["rows"][0]["family_name"] == "Example, Ann and Bo"
    assert calls == [("snap", NAME_NOT_RECORDED)]
