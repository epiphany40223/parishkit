"""Native queued-export recovery remains private without database-backed layout."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from parishkit.stewardship.reports import exact_ui, export_ui
from parishkit.stewardship.reports.export_services import ExportConflict


@pytest.mark.parametrize("action", ["create", "detail", "cancel", "retry"])
def test_native_exact_outage_has_safe_recovery_link(monkeypatch, action):
    """A failing owner yields fixed text and the correct native status namespace."""
    identifier = uuid4()

    def unavailable():
        """Synthetic private details must not leak through error presentation."""
        raise DatabaseError("private source value")

    monkeypatch.setattr(exact_ui, "runtime", unavailable)
    factory = RequestFactory()
    if action == "create":
        response = exact_ui.create(factory.post("/"), identifier)
        link = f"/admin/reports/{identifier}/participation/"
    elif action == "detail":
        response = exact_ui.detail(factory.get("/"), identifier)
        link = f"/admin/reports/exact-exports/{identifier}/"
    else:
        response = exact_ui.command(factory.post("/"), identifier, action=action)
        link = f"/admin/reports/exact-exports/{identifier}/"
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert link.encode() in response.content
    assert b"private source value" not in response.content


@pytest.mark.parametrize("action", ["create", "cancel", "retry"])
def test_native_exact_mutations_reject_get(action):
    """Link prefetching and reload cannot issue an export mutation."""
    request, identifier = RequestFactory().get("/"), uuid4()
    response = (
        exact_ui.create(request, identifier)
        if action == "create"
        else exact_ui.command(request, identifier, action=action)
    )
    assert response.status_code == 405


def test_regeneration_rejects_get_and_has_private_outage_recovery(monkeypatch):
    """The new renderer action uses the same native safety contract as exact jobs."""
    identifier, factory = uuid4(), RequestFactory()
    unavailable = Mock(side_effect=DatabaseError("private source value"))
    monkeypatch.setattr(export_ui, "runtime", unavailable)
    response = export_ui.command(factory.get("/"), identifier, action="regenerate")
    assert response.status_code == 405
    unavailable.assert_not_called()
    response = export_ui.command(factory.post("/"), identifier, action="regenerate")
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert f"/admin/reports/exports/{identifier}/".encode() in response.content
    assert b"private source value" not in response.content


@pytest.mark.parametrize(
    "values",
    [{}, {"request_key": "bad-key"}, {"request_key": [str(uuid4()), str(uuid4())]}],
)
def test_regeneration_rejects_malformed_command_before_service(monkeypatch, values):
    """Missing, malformed and duplicate keys cannot allocate replacement work."""
    monkeypatch.setattr(export_ui, "runtime", Mock())
    monkeypatch.setattr(export_ui, "_principal", Mock())
    regenerate = Mock()
    monkeypatch.setattr(export_ui, "regenerate_export", regenerate)
    response = export_ui.command(
        RequestFactory().post("/", values), uuid4(), action="regenerate"
    )
    assert response.status_code == 400
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert "Retry-After" not in response
    regenerate.assert_not_called()


def test_regeneration_conflict_retains_safe_native_link(monkeypatch):
    """A valid but premature regeneration is a conflict, not an outage."""
    identifier = uuid4()
    monkeypatch.setattr(export_ui, "runtime", Mock())
    monkeypatch.setattr(export_ui, "_principal", Mock())
    monkeypatch.setattr(
        export_ui,
        "regenerate_export",
        Mock(side_effect=ExportConflict("private original value")),
    )
    response = export_ui.command(
        RequestFactory().post("/", {"request_key": str(uuid4())}),
        identifier,
        action="regenerate",
    )
    assert response.status_code == 409
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert "Retry-After" not in response
    assert f"/admin/reports/exports/{identifier}/".encode() in response.content
    assert b"private original value" not in response.content
