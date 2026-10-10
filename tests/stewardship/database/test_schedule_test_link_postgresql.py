"""A schedule email's test link never dead-ends on a live campaign (#923).

Dates and mail schedules links each chosen email's test page (#446), but that
page sends only while the campaign is a Testing draft, or in Production while
live email delivery is paused. A live campaign used to land on "This
information changed. Reload before trying again.", which no reload fixes.
"""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.campaigns.models import Campaign

from ..content_factory import content
from . import campaign_builders
from .test_campaign_mail_postgresql import campaign_test  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)

pytestmark = pytest.mark.django_db(transaction=True)

NOT_OPEN = (
    "Test emails can be sent only while the campaign is being tested, or while "
    "live email delivery is paused."
)


def test_live_campaign_explains_instead_of_asking_for_a_reload(scheduled):
    """No test link while delivery runs; the test page says why, then works paused.

    The test page used to answer the SQL admission's refusal with the stale
    message (409); it now refuses with the reason and a link to Pause and
    resume mail. Pausing live delivery opens both the link and the page.
    """
    item = scheduled
    schedules = reverse("admin:schedule_settings")
    with web_login():
        test_path = reverse(
            "admin:campaign_mail",
            args=[commands.page(*item.arguments)["test_template"]],
        )
        page = item.browser.get(schedules)
        assert page.status_code == 200
        assert b"data-test-url" not in page.content
        assert NOT_OPEN.encode() in page.content
        assert b"data-edit-url" in page.content
        refused = item.browser.get(test_path)
        assert refused.status_code == 404, refused.content
        refusal = refused.json()["refusal"]
        assert refusal["message"] == NOT_OPEN
        assert refusal["link"] == {
            "url": reverse("admin:delivery_control"),
            "label": "Pause and resume mail",
        }
        # A person sees the same explanation as a page, not "This
        # information changed".
        shown = item.browser.get(test_path, HTTP_ACCEPT="text/html")
        assert shown.status_code == 404
        assert NOT_OPEN.encode() in shown.content
        assert b"This information changed" not in shown.content
        _, token = commands.preview_pause(*item.arguments, reason="Check sender")
        commands.confirm(*item.arguments, token=token)
        page = item.browser.get(schedules)
        assert b"data-test-url" in page.content
        assert b"data-test-note" not in page.content
        assert item.browser.get(test_path).status_code == 200


@pytest.mark.parametrize(
    "slot", ["initial", "reminder", "daily_digest", "weekly_digest"]
)
def test_every_schedulable_email_previews_in_a_testing_draft(campaign_test, slot):
    """Each mail type a schedule sends, both digests included, opens its test page."""
    service, browser, _path, _ = campaign_test
    campaign = Campaign.objects.get()
    template = content(str(campaign.pk), kind="email", slot=slot)
    assert (
        campaign_builders.change(
            service.store,
            service.store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **template}],
        ).state
        == "applied"
    )
    with web_login():
        page = browser.get(reverse("admin:schedule_settings"))
        assert page.status_code == 200
        test_path = reverse("admin:campaign_mail", args=[template["id"]])
        assert f'data-test-url="{test_path}"'.encode() in page.content
        assert b"data-test-note" not in page.content
        preview = browser.get(test_path)
        assert preview.status_code == 200, preview.content
        assert preview.context["form"]["preview_token"].value()
