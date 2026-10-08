"""Reports show the current campaign only until #145 (ADM-12.05).

Navigation rule 10: choosing a campaign other than the current one for a
report is refused (410), and the retired choosers redirect. An archived
campaign that is no longer current is the realistic case; unknown campaign
identifiers are covered by each report's own suite.
"""

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_clone_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)

REFUSAL = "This campaign is no longer the current campaign."
REPORTS = (
    "participation/",
    "ministries/",
    "ministries/follow-up/",
    "financial/",
    "responses/",
    "talents/",
    "families/",
    "information/",
)


def test_reports_refuse_a_campaign_that_is_no_longer_current(auth_service, google):
    """Every report page refuses the archived campaign; the roots show none."""
    source, _ = setup(auth_service.store)
    browser, _ = signed_in()
    for report in REPORTS:
        response = browser.get(f"/admin/reports/{source.pk}/{report}")
        assert response.status_code == 410, report
        assert response["Cache-Control"] == "no-store"
        assert response.json()["refusal"]["message"] == REFUSAL, report
    # A person sees a plain page explaining it, never a sign-in prompt.
    page = browser.get(
        f"/admin/reports/{source.pk}/participation/", HTTP_ACCEPT="text/html"
    )
    assert page.status_code == 410
    assert REFUSAL.encode() in page.content
    assert b"Reports show the current campaign only" in page.content
    # (The error page's own sign-in link; the session dialog has another.)
    assert b'<p><a href="/admin/login">Sign in again</a></p>' not in page.content
    # Refused before the report's access audit starts.
    assert (
        not AuditEvent.objects.filter(campaign_reference=source.pk)
        .filter(event_type="participation_viewed")
        .exists()
    )
    # With no current campaign, the reports root and Ministry requests (where
    # the retired Ministry reports root leads) say so instead of opening the
    # archived one.
    moved = browser.get("/admin/ministry-reports/")
    assert moved.status_code == 301
    assert moved["Location"] == reverse("admin:ministry_report")
    for root in (reverse("admin:reports"), reverse("admin:ministry_report")):
        response = browser.get(root)
        assert response.status_code == 200, root
        assert b"There is no current campaign report for you to view." in (
            response.content
        )


@pytest.mark.parametrize("role", ["signed_out", "ministry_leader"])
def test_access_is_checked_before_the_current_campaign(auth_service, google, role):
    """No 410-versus-403 signal: without report access, a report is a 403.

    A signed-out request, or a Ministry leader on an Admin/Staff report, is
    refused for access at the report's address, which names no campaign. An
    old address naming a campaign answers the signed-out the same refusal for
    any campaign, so the 410 never tells a stranger that the campaign exists
    or what it was; a signed-in portal user may learn only that it is not the
    current one (admin-portal spec, "URL scheme").
    """
    store = auth_service.store
    source, _ = setup(store)
    if role == "signed_out":
        browser = Client()
    else:
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "add",
                        "section": "login_rules",
                        **address("reader@example.org", roles=(role,)),
                    }
                ],
            ).state
            == "applied"
        )
        google[0].update(email="reader@example.org", sub="reader-subject")
        browser, _ = signed_in()
    for name in (
        "participation",
        "financial_report",
        "response_dashboard",
        "talents_report",
    ):
        response = browser.get(reverse(f"admin:{name}"))
        assert response.status_code == 403, (role, name)
    if role == "signed_out":
        for campaign in (source.pk, uuid4()):
            for report in ("participation/", "financial/", "responses/", "talents/"):
                response = browser.get(f"/admin/reports/{campaign}/{report}")
                assert response.status_code == 403, (role, report)


def test_retired_choosers_redirect(auth_service, google):
    """The two campaign choosers open their reports; report filters are kept.

    Their target names no campaign, so the redirect is permanent (NAV-11).
    """
    browser, _ = signed_in()
    response = browser.get("/admin/reports/campaigns/?scope=current&size=50")
    assert response.status_code == 301
    assert response["Location"] == (
        reverse("admin:participation") + "?scope=current&size=50"
    )
    response = browser.get("/admin/ministry-reports/campaigns/")
    assert response.status_code == 301
    assert response["Location"] == reverse("admin:ministry_report")
