"""Current Admin authority protects the exact passive readiness/impact preview."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.core import signing
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import go_live_commands, go_live_views
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.storage import StaleRecordError

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_campaign_mail_postgresql import campaign_test  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)


def test_web_preview_is_passive_current_and_never_starts_deletion(campaign_test):
    service, browser, _, _ = campaign_test
    campaign = Campaign.objects.get()
    path = f"/admin/campaign/{campaign.pk}/go-live"
    before = PortalSession.objects.get().last_activity_at
    tasks = TaskRun.objects.count()
    with web_login():
        response = browser.get(path)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        preview = response.context["preview"]
        assert "full_refresh_required" in preview.problems
        assert "mail_template_unavailable" in preview.problems
        assert "family_test_mail_required" in preview.problems
        assert preview.target_state == "scheduled"
        assert b"No Testing records" in response.content
        again = browser.get(path)
        assert again.context["preview"].digest == preview.digest
        assert PortalSession.objects.get().last_activity_at == before
        assert not ProductionTransitionRequest.objects.exists()
        assert TaskRun.objects.count() == tasks
        assert browser.get(path + "/families").status_code == 200
        assert browser.get(path + "/families?page=0").status_code == 400
        assert browser.get(path + "?actor=other").status_code == 400
        assert browser.post(path, {}).status_code == 403  # missing CSRF


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_staff_and_ministry_leaders_cannot_read_readiness(campaign_test, google, role):
    service, _, _, _ = campaign_test
    record = address("other@example.org", roles=(role,))
    assert (
        change(
            service.store,
            service.store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **record}],
        ).state
        == "applied"
    )
    google[0].update(email="other@example.org", sub="another-google-subject")
    browser, login = signed_in()
    assert login.status_code == 302
    path = f"/admin/campaign/{Campaign.objects.get().pk}/go-live"
    with web_login():
        assert browser.get(path).status_code == 403
        assert browser.get(path + "/families").status_code == 403
        assert browser.get(path + f"/cleanup/{uuid4()}/links").status_code == 403
        assert Client().get(path).status_code in {302, 403}


def test_revocation_during_render_prevents_private_response(campaign_test, monkeypatch):
    _, browser, _, _ = campaign_test
    original = go_live_views.render

    def revoke(*args, **kwargs):
        """Use the actual stored revocation, not a mocked authorization decision."""
        result = original(*args, **kwargs)
        PortalUser.objects.update(disabled=True, version=F("version") + 1)
        return result

    monkeypatch.setattr(go_live_views, "render", revoke)
    response = browser.get(f"/admin/campaign/{Campaign.objects.get().pk}/go-live")
    assert response.status_code == 403
    assert b"Testing cleanup inventory" not in response.content


def test_exact_close_is_a_readiness_blocker_not_active_target(campaign_test):
    _, browser, _, _ = campaign_test
    campaign = Campaign.objects.select_related("active_configuration").get()
    with campaign_clock(campaign.active_configuration.ends_at), web_login():
        response = browser.get(f"/admin/campaign/{campaign.pk}/go-live")
    assert response.status_code == 200, response.content
    assert response.context["preview"].target_state == "closed"
    assert "campaign_closed" in response.context["preview"].problems


def test_explicit_origin_check_does_not_mistake_dns_for_complete_readiness(
    campaign_test, monkeypatch, settings
):
    """Only explicit POST checks DNS; unresolved inputs still block cleanup."""
    _, browser, _, _ = campaign_test
    settings.STEWARDSHIP_PUBLIC_ORIGIN = "http://localhost:8000"
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "test"
    calls = []

    def verified(origin, profile):
        """Replace only external DNS, asserting no transaction survives across it."""
        from django.db import connection

        assert not connection.in_atomic_block
        calls.append((origin, profile.value))
        return True

    monkeypatch.setattr(go_live_commands, "check_public_origin", verified)
    path = f"/admin/campaign/{Campaign.objects.get().pk}/go-live"
    with web_login():
        assert browser.get(path).status_code == 200
        assert not calls
        response = post(browser, path, {"action": "verify"})
        assert response.status_code == 200, response.content
        assert response.context["origin_verified"] is True
        assert response.context["cleanup_token"] is None
        assert response.context["preview"].problems
        assert not ProductionTransitionRequest.objects.exists()
        assert (
            post(
                browser, path, {"action": "verify", "origin": "https://evil.invalid"}
            ).status_code
            == 400
        )
    assert calls == [("http://localhost:8000", "test")]


def test_signature_alone_cannot_admit_cleanup_with_missing_current_proofs(
    campaign_test, settings
):
    """A server-signed input binding cannot substitute for recomputed readiness."""
    service, browser, _, _ = campaign_test
    settings.STEWARDSHIP_PUBLIC_ORIGIN = "http://localhost:8000"
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "test"
    campaign = Campaign.objects.get()
    response = browser.get(f"/admin/campaign/{campaign.pk}/go-live")
    token = signing.dumps(
        {
            "actor": str(PortalUser.objects.get().pk),
            "campaign": str(campaign.pk),
            "key": str(uuid4()),
            "digest": response.context["preview"].digest,
            "origin": "http://localhost:8000",
            "profile": "test",
        },
        salt=go_live_commands.SALT,
    )
    with web_login(), pytest.raises(StaleRecordError):
        go_live_commands.start_cleanup(
            response.wsgi_request,
            service,
            campaign.pk,
            preview_token=token,
            acknowledge=True,
        )
    assert not ProductionTransitionRequest.objects.exists()
