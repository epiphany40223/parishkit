"""Go-live readiness and System health list content to re-save (#838)."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.config import ConfigError

from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_campaign_mail_postgresql import campaign_test  # noqa: F401
from .test_content_views_postgresql import plant_stale
from .test_report_workspace_postgresql import read as get
from .test_setup_mail_views_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

HEALTH = "/admin/system/health/"
# An invitation whose Family code survives only in a comment cleaning removes.
LOST_CODE = "<p>Open {{ family_url }}</p><!-- {{ family_code }} -->"


def stale_rows(campaign_id):
    """A page that only loses a comment, and an invitation that lost its code."""
    page = content(
        campaign_id, html="<p>Welcome to {{ parish_name }}.</p><!-- old note -->"
    )
    email = content(
        campaign_id,
        kind="email",
        slot="initial",
        subject="Lost code",
        html=LOST_CODE,
        text="Open {{ family_url }} {{ family_code }}",
    )
    return page, email


def test_go_live_readiness_lists_content_and_blocks_a_selected_lost_code(
    campaign_test, monkeypatch
):
    """Both rows are listed with their editor links; only a schedule selecting
    the invitation that lost its code makes it a readiness problem."""
    from parishkit.stewardship.campaigns.models import Campaign

    service, browser, _, _ = campaign_test
    store = service.store
    campaign = Campaign.objects.get()
    page, email = stale_rows(str(campaign.pk))
    plant_stale(store, monkeypatch, page, email)
    path = reverse("admin:go_live")
    with web_login():
        response = browser.get(path)
        assert response.status_code == 200, response.content
        preview = response.context["preview"]
        editor = reverse(
            "admin:content_revision", args=["email", "initial", email["id"]]
        )
        assert [
            (row["label"], row["subject"], row["blocked"]) for row in preview.resave
        ] == [
            ("Family welcome", None, False),
            ("Initial invitation", "Lost code", True),
        ]
        body = response.content.decode()
        assert 'id="resave-content"' in body
        assert f'href="{editor}"' in body
        assert (
            f'href="{reverse("admin:content_edit", args=["page", "welcome"])}"' in body
        )
        assert "Re-save recommended" in body and "Can't be sent until fixed" in body
        # No schedule sends it yet, so it does not block.
        assert "family_email_unsendable" not in preview.problems
    schedule = next(
        row
        for row in store.active().document()["sections"]["schedules"]
        if row["values"]["campaign_id"] == str(campaign.pk)
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": schedule["id"],
                    "values": {
                        "template_version": email["id"],
                        "subject": "Lost code",
                    },
                }
            ],
        ).state
        == "applied"
    )
    with web_login():
        response = browser.get(path)
        assert "family_email_unsendable" in response.context["preview"].problems
        assert "Pages and emails marks" in response.content.decode()


def test_clean_readiness_lists_nothing(campaign_test):
    """Content already in today's cleaned form adds no list."""
    _, browser, _, _ = campaign_test
    with web_login():
        response = browser.get(reverse("admin:go_live"))
        assert response.context["preview"].resave == ()
        assert b'id="resave-content"' not in response.content


def test_system_health_lists_content_on_page_load_only(
    auth_service, google, monkeypatch
):
    """No campaign, a clean campaign, then flagged rows linked to their editors;
    the 10-second poll never reads them, and a failed read says so."""
    from parishkit.stewardship.accounts import system_health_views
    from parishkit.stewardship.campaigns.models import Campaign

    store = auth_service.store
    # The #774 line beside this one scans the current ParishSoft source, which
    # this fixture need not have; its own tests cover it.
    monkeypatch.setattr(system_health_views, "source_form_summary", lambda _: 0)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, HEALTH)
    assert response.status_code == 200
    assert b"There is no current campaign, so no saved page or email" in body
    assert add_draft(store, store.active(), uuid4())[0].state == "applied"
    _, body = get(browser, HEALTH)
    assert b"No saved page or email needs re-saving." in body
    campaign = Campaign.objects.get()
    page, email = stale_rows(str(campaign.pk))
    plant_stale(store, monkeypatch, page, email)
    response, body = get(browser, HEALTH)
    text = body.decode()
    assert "2 saved pages or emails should be re-saved" in text
    summary = text.split('id="resave-summary"', 1)[1].split("</div>", 1)[0]
    assert reverse("admin:content_catalog") in summary
    editor = reverse("admin:content_revision", args=["email", "initial", email["id"]])
    assert f'href="{editor}"' in summary
    assert "Initial invitation: Lost code" in summary
    assert "Can't be sent until fixed" in summary
    assert "Re-save recommended" in summary
    calls = []

    def unavailable(configuration, campaign):
        """The read cannot run now."""
        calls.append(campaign.pk)
        raise ConfigError("Configuration is unavailable.")

    monkeypatch.setattr(system_health_views, "resave_recommended", unavailable)
    response, body = get(browser, HEALTH + "status")
    assert response.status_code == 200 and calls == []
    assert b"re-sav" not in body
    response, body = get(browser, HEALTH)
    assert response.status_code == 200 and calls == [campaign.pk]
    assert b"needs re-saving could not be checked right now" in body
