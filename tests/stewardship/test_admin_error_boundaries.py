"""Optional chrome and editor errors retain closed, meaningful HTTP boundaries."""

import json
from unittest.mock import Mock

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import branding_context
from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.storage import StaleRecordError


@pytest.mark.parametrize(
    "error_type,status,code",
    [
        (ConfigError, 503, "unavailable"),
        (DatabaseError, 503, "unavailable"),
        (LimiterUnavailable, 503, "unavailable"),
        (ValueError, 400, "invalid"),
        (PermissionError, 403, "denied"),
        (StaleRecordError, 409, "stale_version"),
        (LookupError, 404, "unavailable"),
    ],
)
def test_editor_errors_distinguish_unavailability_from_invalid_input(
    error_type, status, code
):
    """Exception inheritance cannot turn a deployment outage into a field mistake."""
    response = error_response(error_type("synthetic-private-error"))
    assert response.status_code == status
    assert json.loads(response.content)["errors"][0]["code"] == code
    assert b"synthetic-private-error" not in response.content
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    if status == 503:
        assert response["Retry-After"] == "5"


@pytest.mark.parametrize(
    "error_type", [ConfigError, DatabaseError, LimiterUnavailable, OSError]
)
def test_unavailable_branding_cannot_break_the_error_page(monkeypatch, error_type):
    """A missing startup runtime or storage must not recursively break status pages."""
    monkeypatch.setattr(
        branding_context, "runtime", Mock(side_effect=error_type("synthetic-private"))
    )
    assert branding_context.parish_branding(RequestFactory().get("/admin/setup")) == {}


def test_branding_reuses_one_verified_display_projection_per_request(monkeypatch):
    """Repeated rendering shares presentation data without caching authorization."""
    from types import SimpleNamespace

    parish = object()
    root = SimpleNamespace(
        restore_review_required=False,
        active_configuration=SimpleNamespace(parish=parish),
    )
    verify = Mock(return_value=root)
    service = Mock()
    service.configured.return_value = True
    monkeypatch.setattr(branding_context, "runtime", Mock(return_value=service))
    monkeypatch.setattr(branding_context, "coherent_configuration", verify)
    render = Mock(return_value={"name": "Parish"})
    monkeypatch.setattr(branding_context, "branding_for", render)
    request = RequestFactory().get("/admin/")
    for _ in range(2):
        assert branding_context.parish_branding(request) == {
            "parish_branding": {"name": "Parish"}
        }
    verify.assert_called_once_with(service.store)
    assert request._stewardship_display_configuration is root
    render.assert_called_with(parish)
