"""Reports show the current campaign only until #145 (ADM-12.05).

Navigation rule 10: report addresses name no campaign, so a report shows
the current campaign. An archived campaign that is no longer current is the
realistic case. The old addresses that named a campaign are gone (#864).
"""

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_clone_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


def test_report_roots_show_no_archived_campaign(auth_service, google):
    """With no current campaign, the report roots say so (rule 10).

    The reports root and Ministry requests never open the archived campaign.
    """
    setup(auth_service.store)
    browser, _ = signed_in()
    for root in (reverse("admin:reports"), reverse("admin:ministry_report")):
        response = browser.get(root)
        assert response.status_code == 200, root
        assert b"There is no current campaign report for you to view." in (
            response.content
        )


@pytest.mark.parametrize("role", ["signed_out", "ministry_leader"])
def test_access_is_checked_before_the_current_campaign(auth_service, google, role):
    """Without report access, a report is a 403.

    A signed-out request, or a Ministry leader on an Admin/Staff report, is
    refused for access at the report's address, which names no campaign.
    """
    store = auth_service.store
    setup(store)
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
