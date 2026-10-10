"""The shared export page never reveals an id's kind before its view checks access.

A query string or an outage during the lookup is refused before the owning
view is chosen, with the same page for a latest-data export as for a report
export (NAV-12 review).
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from parishkit.stewardship.reports import export_pages


def _records(exact, *, fail=False):
    """A stand-in for ``ExactExportRequest``: the id is ``exact`` or not.

    With ``fail`` the lookup raises, as a database outage does, whatever the
    id names.
    """

    def filter(**_):
        """The lookup."""
        if fail:
            raise DatabaseError("private outage detail")
        return SimpleNamespace(exists=lambda: exact)

    return SimpleNamespace(objects=SimpleNamespace(filter=filter))


@pytest.fixture
def views(monkeypatch):
    """Record which owning view was called instead of running it."""
    called = []
    for module in (export_pages.exact_ui, export_pages.export_ui):
        name = module.__name__.rsplit(".", 1)[-1]
        monkeypatch.setattr(
            module, "detail", lambda request, request_id, n=name: called.append(n)
        )
        monkeypatch.setattr(
            module,
            "command",
            lambda request, request_id, *, action, n=name: called.append(n),
        )
    return called


@pytest.mark.parametrize("exact", [True, False])
def test_each_record_reaches_the_view_that_owns_it(monkeypatch, views, exact):
    """A latest-data export goes to its view; anything else to report exports."""
    monkeypatch.setattr(export_pages, "ExactExportRequest", _records(exact))
    request_id = uuid4()
    export_pages.detail(RequestFactory().get("/"), request_id)
    export_pages.command(RequestFactory().post("/"), request_id, action="cancel")
    owner = "exact_ui" if exact else "export_ui"
    assert views == [owner, owner]


@pytest.mark.parametrize(
    ("query", "fail", "status"), [("?x=1", False, 400), ("", True, 503)]
)
def test_early_refusals_look_the_same_for_both_kinds(
    monkeypatch, views, query, fail, status
):
    """A query string or a failed lookup gets one page, whatever the id names.

    Each case runs once for an id naming a latest-data export and once for
    one naming a report export; the page and every action answer the same.
    """
    request_id = uuid4()
    answers = []
    for exact in (True, False):
        monkeypatch.setattr(
            export_pages, "ExactExportRequest", _records(exact, fail=fail)
        )
        answers.append(
            export_pages.detail(RequestFactory().get("/" + query), request_id)
        )
        answers.append(
            export_pages.command(
                RequestFactory().post("/" + query), request_id, action="retry"
            )
        )
    assert views == []
    for answer in answers:
        assert answer.status_code == status
        assert b"private outage detail" not in answer.content
    assert len({answer.content for answer in answers}) == 1
