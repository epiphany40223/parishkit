"""Native queued-export recovery remains private without database-backed layout."""

from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.reports import exact_ui


@pytest.mark.parametrize("action", ["detail", "cancel", "retry"])
def test_native_exact_outage_has_safe_recovery_link(monkeypatch, action):
    """A failing owner yields fixed text and the correct native status namespace."""
    identifier = uuid4()

    def unavailable():
        """Synthetic private details must not leak through error presentation."""
        raise DatabaseError("private source value")

    monkeypatch.setattr(exact_ui, "runtime", unavailable)
    factory = RequestFactory()
    if action == "detail":
        response = exact_ui.detail(factory.get("/"), identifier)
        link = reverse("admin:report_export", args=[identifier])
    else:
        response = exact_ui.command(factory.post("/"), identifier, action=action)
        link = reverse("admin:report_export", args=[identifier])
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert link.encode() in response.content
    assert b"private source value" not in response.content


@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_native_exact_mutations_reject_get(action):
    """Link prefetching and reload cannot issue an export mutation."""
    request, identifier = RequestFactory().get("/"), uuid4()
    response = exact_ui.command(request, identifier, action=action)
    assert response.status_code == 405


def test_the_latest_data_form_is_gone():
    """The Response dashboard offers no latest-data form (#1537): its
    address is gone, with no redirect, and ``export exact create`` is the
    way to request one."""
    from django.urls import NoReverseMatch, Resolver404, resolve

    assert not hasattr(exact_ui, "create")
    with pytest.raises(NoReverseMatch):
        reverse("admin:report_exact_create")
    with pytest.raises(Resolver404):
        resolve("/admin/reports/participation/exact-exports/")
