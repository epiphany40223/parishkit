"""All resolved portal routes share setup/current-role/maintenance admission."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone

from parishkit.stewardship.accounts.authentication import AuthRuntime
from parishkit.stewardship.accounts.models import PortalSession

from ..policy_factory import address, domain
from .auth_builders import signed_in
from .campaign_builders import change, restored_runtime

pytestmark = pytest.mark.django_db(transaction=True)


def test_missing_setup_marker_never_infers_completion_from_prepared_yaml(
    auth_service, google, settings
):
    """An Admin is routed to setup; the future wizard is not represented as complete."""
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        auth_service.store, auth_service.limiter
    )
    browser, response = signed_in()
    assert response.status_code == 302
    redirected = browser.get("/admin/")
    assert redirected.status_code == 302
    assert redirected["Location"] == "/admin/setup"
    setup = browser.get("/admin/setup")
    assert setup.status_code == 503
    assert b"not configured yet" in setup.content
    assert b"/admin/logout" in setup.content
    # Object lookup and report production cannot run while setup is incomplete.
    report = browser.get(f"/admin/campaign/{uuid4()}/family-codes")
    assert report.status_code == 302 and report["Location"] == "/admin/setup"


@pytest.mark.parametrize(
    "path", ["/", "/family/", "/family/keepalive", "/access/opaque"]
)
def test_incomplete_setup_blocks_family_routes_and_partial_posts(
    auth_service, settings, path
):
    """No Family lookup/session/workflow is reached in an unconfigured deployment."""
    settings.STEWARDSHIP_AUTH_RUNTIME = replace(
        auth_service, setup_complete=lambda: False
    )
    for method in ("get", "post"):
        response = getattr(Client(), method)(path)
        assert response.status_code == 503
        assert b"not configured yet" in response.content
        assert b"/admin/login" not in response.content


def test_non_admin_cannot_enter_setup_or_maintenance_workflows(
    auth_service, google, settings
):
    """A direct endpoint request cannot give Staff setup authority."""
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, _ = signed_in()
    settings.STEWARDSHIP_AUTH_RUNTIME = replace(
        auth_service, setup_complete=lambda: False
    )
    for path in ("/admin/", "/admin/setup", "/admin/maintenance"):
        response = browser.get(path)
        assert response.status_code == 503
        assert b"not configured yet" in response.content
    with restored_runtime(timezone.now() - timedelta(hours=1)):
        response = browser.get("/admin/maintenance")
        assert response.status_code == 503
        assert b"temporarily unavailable" in response.content


@pytest.mark.parametrize("signed_domain", [None, "wrong.example", "example.org"])
def test_http_hosted_domain_grants_require_signed_matching_domain(
    auth_service, google, signed_domain
):
    """An email suffix alone does not grant Staff through the actual Google callback."""
    rule = domain()
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    google[0].pop("hd", None)
    if signed_domain is not None:
        google[0]["hd"] = signed_domain
    browser, response = signed_in()
    if signed_domain == "example.org":
        assert response.status_code == 302
        assert browser.get("/admin/").status_code == 200
    else:
        assert response.status_code == 403
        assert not PortalSession.objects.exists()


def test_current_marker_changes_and_invalid_marker_results_fail_closed(
    auth_service, google, settings
):
    """Read setup completion on each request and reject merely truthy values."""
    marker = [True]
    settings.STEWARDSHIP_AUTH_RUNTIME = replace(
        auth_service, setup_complete=lambda: marker[0]
    )
    browser, _ = signed_in()
    assert browser.get("/admin/").status_code == 200
    assert browser.get("/admin/setup")["Location"] == "/admin/"
    marker[0] = False
    assert browser.get("/admin/")["Location"] == "/admin/setup"
    marker[0] = "synthetic-private-value"
    response = browser.get("/admin/")
    assert response.status_code == 503
    assert b"synthetic-private-value" not in response.content
