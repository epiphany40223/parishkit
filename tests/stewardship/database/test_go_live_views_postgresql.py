"""Current Admin authority protects the Go live page's passive Check step (#1585)."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.core import signing
from django.db.models import F
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.accounts import go_live_commands, go_live_views
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .leader_builders import grant_role
from .test_admin_navigation_postgresql import flow_steps
from .test_campaign_mail_postgresql import campaign_test  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)


def test_web_preview_is_passive_current_and_never_starts_deletion(campaign_test):
    service, browser, _, _ = campaign_test
    path = reverse("admin:go_live")
    before = PortalSession.objects.get().last_activity_at
    tasks = TaskRun.objects.count()
    with web_login():
        response = browser.get(path)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        body = response.content.decode()
        # One step bar, Go live's own: not the old step list, not the
        # Campaign setup stepper (#1585).
        assert flow_steps(response.content) is None
        assert 'id="campaign-stepper"' not in body
        assert "<h1>Go live</h1>" in body
        assert "Step 1 of 4:" in body and "<strong>Check</strong>" in body
        assert "0 of 4 steps completed" in body and "Next: Prepare" in body
        # Three later steps: in the bar (name and tooltip) and in both lists.
        first = "Not available yet: Start go-live on the Check step first."
        assert body.count(first) == 12
        preview = response.context["preview"]
        assert "full_refresh_required" in preview.problems
        assert "mail_template_unavailable" in preview.problems
        assert "family_test_mail_required" in preview.problems
        assert preview.target_state == "scheduled"
        assert response.context["state"] == "not_ready"
        # The one primary is the check; no Refresh link, no Start (#1586).
        assert "Check readiness and public web address" in body
        assert "Refresh readiness preview" not in body
        assert "data-review-open" not in body
        assert "things to fix before going live" in body
        # Each problem links to the page that fixes it.
        assert f'href="{reverse("admin:source_refresh")}"' in body
        # The page updates itself in place (live-status-v1.js).
        assert 'data-live-status="go-live-check"' in body
        assert 'data-live-interval="30000"' in body
        again = browser.get(path)
        assert again.context["preview"].digest == preview.digest
        assert PortalSession.objects.get().last_activity_at == before
        assert not ProductionTransitionRequest.objects.exists()
        assert TaskRun.objects.count() == tasks
        families_path = reverse("admin:go_live_families")
        families = browser.get(families_path)
        assert families.status_code == 200
        # A sub-page of Check: no step indicator of its own.
        assert flow_steps(families.content) is None
        assert b'class="table-nav"' in families.content
        assert browser.get(families_path + "?size=25").status_code == 200
        assert browser.get(families_path + "?page=0").status_code == 400
        assert browser.get(families_path + "?size=500").status_code == 400
        sorted_page = browser.get(families_path + "?sort=-name")
        assert sorted_page.status_code == 200
        assert b'aria-sort="descending"' in sorted_page.content
        assert b"Page 1 of 1" in sorted_page.content
        assert b"sort=name" in sorted_page.content
        for token in ("family_duid", "name;", "-id"):
            assert browser.get(families_path + f"?sort={token}").status_code == 400
        assert browser.get(path + "?actor=other").status_code == 400
        assert browser.post(path, {}).status_code == 403  # missing CSRF


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_staff_and_ministry_leaders_cannot_read_readiness(campaign_test, google, role):
    service, _, _, _ = campaign_test
    grant_role(service.store, "other@example.org", role)
    google[0].update(email="other@example.org", sub="another-google-subject")
    browser, login = signed_in()
    assert login.status_code == 302
    path = reverse("admin:go_live")
    with web_login():
        assert browser.get(path).status_code == 403
        assert browser.get(reverse("admin:go_live_families")).status_code == 403
        assert browser.get(reverse("admin:go_live_prepare")).status_code == 403
        assert Client().get(path).status_code in {302, 403}


def test_testing_submissions_after_go_live_say_so_plainly(
    live_response_service, google
):
    """After go-live the Testing list refuses with its own notice (#867).

    The refusal is not a permission problem, so it never offers "Sign in
    again"; Go live's Check step, the only page linking here, refuses too.
    """
    browser, _ = signed_in()
    with web_login():
        response = browser.get(
            reverse("admin:go_live_families"), HTTP_ACCEPT="text/html"
        )
        assert response.status_code == 410
        body = response.content.decode()
        assert "Testing submissions are only available before go-live." in body
        assert "Going live removes the Testing submissions." in body
        # The error page's own "Sign in again" link (the Admin base also
        # carries a hidden signed-out dialog with that text).
        assert '<p><a href="/admin/login">' not in body
        readiness = browser.get(reverse("admin:go_live"), HTTP_ACCEPT="text/html")
        assert readiness.status_code != 200
        assert reverse("admin:go_live_families") not in readiness.content.decode()
        # Scripts get the same refusal as JSON.
        script = browser.get(
            reverse("admin:go_live_families"), HTTP_ACCEPT="application/json"
        )
        assert script.status_code == 410
        assert script.json()["refusal"]["message"] == (
            "Testing submissions are only available before go-live."
        )


def test_revocation_during_render_prevents_private_response(campaign_test, monkeypatch):
    _, browser, _, _ = campaign_test
    original = go_live_views.render

    def revoke(*args, **kwargs):
        """Use the actual stored revocation, not a mocked authorization decision."""
        result = original(*args, **kwargs)
        PortalUser.objects.update(disabled=True, version=F("version") + 1)
        return result

    monkeypatch.setattr(go_live_views, "render", revoke)
    response = browser.get(reverse("admin:go_live"))
    assert response.status_code == 403
    assert b"Testing data to delete" not in response.content


def test_exact_close_is_a_readiness_blocker_not_active_target(campaign_test):
    _, browser, _, _ = campaign_test
    campaign = Campaign.objects.select_related("active_configuration").get()
    with campaign_clock(campaign.active_configuration.ends_at), web_login():
        response = browser.get(reverse("admin:go_live"))
    assert response.status_code == 200, response.content
    assert response.context["preview"].target_state == "closed"
    assert "campaign_closed" in response.context["preview"].problems
    # Its own state, whose one primary opens Dates and mail schedules.
    assert response.context["state"] == "closed"
    body = response.content.decode()
    assert "The campaign has closed and cannot go live with these dates." in body
    assert "Review campaign dates" in body
    assert "Check readiness and public web address" not in body


def test_explicit_origin_check_does_not_mistake_dns_for_complete_readiness(
    campaign_test, monkeypatch, settings
):
    """Only explicit POST checks DNS; unresolved inputs still block cleanup.

    The check answers a redirect back to Check (no POST draws a page), and
    its result is kept in the session, so a reload shows it again (#1585).
    """
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
    path = reverse("admin:go_live")
    with web_login():
        assert browser.get(path).status_code == 200
        assert not calls
        response = post(browser, path, {"action": "verify"})
        assert response.status_code == 303, response.content
        assert response["Location"] == path
        for _ in range(2):
            page = browser.get(path)
            assert page.context["origin_verified"] is True
            assert page.context["preview"].problems
            assert page.context["state"] == "not_ready"
            assert b"passed the URL and DNS checks" in page.content
        assert not ProductionTransitionRequest.objects.exists()
        # Start go-live's review refuses while a problem remains.
        review = post(browser, path, {"action": "review"})
        assert review.status_code == 409
        assert b'data-go-live-start-refused="changed"' in review.content
        assert post(browser, path, {"action": "other"}).status_code == 400
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
    response = browser.get(reverse("admin:go_live"))
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
