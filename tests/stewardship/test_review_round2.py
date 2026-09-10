"""Focused second-review regressions without provider or database access."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from parishkit.stewardship.deployment import DeploymentProfile, _origin
from parishkit.stewardship.web.content import (
    MAX_TEXT_BYTES,
    render_template,
    sanitize_html,
)
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.security import (
    SecurityBoundaryMiddleware,
    browser_settings,
    login_denial,
)

from . import test_compose


@pytest.mark.parametrize("html", [False, True])
def test_template_expansion_is_rejected_before_combined_allocation(html):
    """A legal template and substitution cannot multiply into a giant output."""
    with pytest.raises(ValueError, match="output exceeds"):
        render_template(
            "{{pronoun}}" * 1000, {"pronoun": "&" * MAX_TEXT_BYTES}, html=html
        )
    assert (
        render_template("{{pronoun}}", {"pronoun": "a" * MAX_TEXT_BYTES})
        == "a" * MAX_TEXT_BYTES
    )


def test_sanitized_html_must_still_fit_storage_bound():
    """Escaping may grow the HTML beyond the admitted input size."""
    with pytest.raises(ValueError, match="bounded"):
        sanitize_html("&" * MAX_TEXT_BYTES)


def test_internal_liveness_exec_timeout_retries(monkeypatch):
    """A delayed docker exec is pending until the overall liveness deadline."""
    compose = Mock(
        side_effect=[
            subprocess.TimeoutExpired("synthetic", 5),
            SimpleNamespace(returncode=0, stdout="ok\n"),
        ]
    )
    monkeypatch.setattr(test_compose.time, "sleep", lambda _: None)
    test_compose.wait_internal_live(compose, b"ok\n")
    assert compose.call_count == 2


@pytest.mark.parametrize("kind", ["login", "validation"])
def test_explicit_safe_errors_survive_security_boundary(kind):
    """Marked HTML/JSON errors retain their accessible and structured contracts."""
    response = (
        login_denial(admin=True)
        if kind == "login"
        else validation_response([FieldError(ErrorCode.INVALID, "name")])
    )
    original = response.content
    request = RequestFactory().get("/admin/login")
    result = SecurityBoundaryMiddleware(lambda _: response)(request)
    assert result.stewardship_safe_error and result.content == original
    unmarked = HttpResponse("synthetic private error", status=403)
    result = SecurityBoundaryMiddleware(lambda _: unmarked)(RequestFactory().get("/"))
    assert b"synthetic" not in result.content


@pytest.mark.parametrize("port", ["", ":8443"])
def test_idna_origin_reaches_django_with_browser_hostname(port):
    """A validated IDN becomes the punycode Host and CSRF origin browsers send."""
    origin = _origin(f"https://tést.example{port}/", DeploymentProfile.PRODUCTION)
    assert origin == f"https://xn--tst-bma.example{port}"
    profile = browser_settings(
        SimpleNamespace(profile=DeploymentProfile.PRODUCTION, public_origin=origin)
    )
    with override_settings(**profile):
        request = RequestFactory().get("/", HTTP_HOST=f"xn--tst-bma.example{port}")
        assert request.get_host() == f"xn--tst-bma.example{port}"
    assert profile["CSRF_TRUSTED_ORIGINS"] == [origin]
