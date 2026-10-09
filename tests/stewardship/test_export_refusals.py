"""Export refusals say what happened, not a sign-in denial (#388 L3 and L4).

A reused one-time export form is a 409 on every export page, and an authorized
download of an expired file is a 410 that points at regeneration.
"""

import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.reports import (
    directory_export_views,
    exact_ui,
    exact_views,
    export_ui,
    export_views,
    financial_export_views,
    information_export_views,
    ministry_export_views,
)
from parishkit.stewardship.reports.export_services import (
    ExportExpired,
    ExportRequestBound,
)

BOUND = b"already used for a different export"
# Every create view, native and JSON, that allocates an export from a form key.
CREATE_VIEWS = {
    "participation": (export_ui, export_ui.create),
    "exact": (exact_ui, exact_ui.create),
    "directory": (directory_export_views, directory_export_views.create),
    "financial": (financial_export_views, financial_export_views.create),
    "information": (information_export_views, information_export_views.create),
    "ministry": (ministry_export_views, ministry_export_views.create),
    "ministry-packet": (ministry_export_views, ministry_export_views.create_packet),
    "participation-json": (export_views, export_views.create),
    "exact-json": (exact_views, exact_views.create),
}


def _failing(monkeypatch, module, error):
    """Make the view's first call raise ``error``, before any form parsing."""
    monkeypatch.setattr(module, "runtime", Mock(side_effect=error))


@pytest.mark.parametrize("name", sorted(CREATE_VIEWS))
def test_every_export_form_reuse_is_a_conflict(monkeypatch, name):
    """A reused request key is 409 with recovery text, never 400 invalid filters."""
    module, view = CREATE_VIEWS[name]
    _failing(monkeypatch, module, ExportRequestBound("private detail"))
    response = view(RequestFactory().post("/"), uuid4())
    assert response.status_code == 409
    assert response["Cache-Control"] == "no-store"
    assert BOUND in response.content
    assert b"private detail" not in response.content


def test_regeneration_form_reuse_is_a_conflict(monkeypatch):
    """Regeneration allocates through the same keyed services."""
    identifier = uuid4()
    _failing(monkeypatch, export_ui, ExportRequestBound("private detail"))
    response = export_ui.command(
        RequestFactory().post("/"), identifier, action="regenerate"
    )
    assert response.status_code == 409 and BOUND in response.content
    page = reverse("admin:report_export", args=[identifier])
    assert page.encode() in response.content


def test_native_expired_download_points_to_regeneration(monkeypatch):
    """The requester sees expiry and a way back, not the sign-in denial."""
    identifier = uuid4()
    _failing(monkeypatch, export_ui, ExportExpired("This export has expired."))
    response = export_ui.command(
        RequestFactory().post("/"), identifier, action="download"
    )
    assert response.status_code == 410
    assert response.stewardship_safe_error
    assert b"file has expired" in response.content
    page = reverse("admin:report_export", args=[identifier])
    assert page.encode() in response.content


@pytest.mark.parametrize(
    ("error", "status"),
    [(ExportExpired("private detail"), 410), (PermissionError("private detail"), 403)],
    ids=["expired", "denied"],
)
@pytest.mark.parametrize("name", ["download", "download_grant"])
def test_json_expired_download_is_not_a_denial(monkeypatch, name, error, status):
    """Expiry is its own answer; every other refusal stays the uniform denial."""
    _failing(monkeypatch, export_views, error)
    view = getattr(export_views, name)
    request = RequestFactory().post("/")
    response = view(request) if name == "download" else view(request, uuid4())
    assert response.status_code == status
    assert b"private detail" not in response.content
    if status == 410:
        assert json.loads(response.content) == {"error": export_views.EXPIRED}
