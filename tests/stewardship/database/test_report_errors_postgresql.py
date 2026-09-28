"""A report outage shows Admins a report error page, not sign-in trouble (#132)."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.reports import ministry_views

from .auth_builders import signed_in

pytestmark = pytest.mark.django_db(transaction=True)


def test_ministry_report_outage_renders_the_admin_report_page(
    auth_service, google, monkeypatch
):
    """A swallowed runtime failure keeps 503 and Retry-After with Admin wording."""
    browser, _ = signed_in()

    def unavailable():
        """A configuration outage carrying detail that must never be shown."""
        raise ConfigError("synthetic private detail")

    monkeypatch.setattr(ministry_views, "runtime", unavailable)
    response = browser.get(
        f"/admin/reports/{uuid4()}/ministries/",
        HTTP_ACCEPT="text/html,*/*;q=0.8",
        HTTP_SEC_FETCH_MODE="navigate",
    )
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert b"This report is temporarily unavailable." in response.content
    assert b"Sign-in" not in response.content
    assert b"synthetic private detail" not in response.content
