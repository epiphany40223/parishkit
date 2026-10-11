"""The Reminder WorkGroup page reviews, applies and follows in place (#750).

A real signed Administrator session edits the current campaign's Reminder
WorkGroup name through the page: the review, Apply's answer, the change's
status and every refusal appear in the page's review region, and each
refusal keeps the version the reader's typing came from.
"""

import re
from html import unescape
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign

from ..campaign_factory import campaign
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import post, requested
from .test_parish_views_postgresql import digest, region

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/admin/campaign/reminder-workgroup/"
NAME = "Active: Stewardship 2027"


def setup(store):
    """The current draft campaign, with no Reminder WorkGroup yet."""
    result, _, _ = add_draft(store, store.active(), uuid4(), campaign())
    assert result.state == "applied"
    return Campaign.objects.get()


def token(response):
    """The signed preview the review's Apply posts."""
    assert response.status_code == 200, response.content
    found = re.search(r'name="preview" value="([^"]+)"', response.content.decode())
    return unescape(found.group(1))


def test_review_apply_and_status_happen_in_place(auth_service, google):
    """Review shows the change under the form; Apply answers with the page
    naming the change; its status, and once applied the new name in the
    form and in "Now", are drawn there."""
    store = auth_service.store
    row = setup(store)
    browser, _ = signed_in()
    page = browser.get(URL)
    assert page.status_code == 200
    assert flow_steps(page.content) == (STEPS, "Make changes")
    review = post(
        browser, URL, {"action": "preview", "name": NAME, "base_digest": digest(page)}
    )
    assert flow_steps(review.content) == (STEPS, "Review")
    shown = unescape(region(review.content))
    assert NAME in shown and "Apply changes" in shown
    confirmed = post(browser, URL, {"action": "confirm", "preview": token(review)})
    request = requested(confirmed)
    assert confirmed["Location"] == f"{URL}?request={request}#settings-review"
    pending = browser.get(confirmed["Location"])
    assert flow_steps(pending.content) == (STEPS, "Apply")
    assert "data-live-pending" in region(pending.content)
    install_request(store, request_id=request, correlation_id=uuid4())
    row.refresh_from_db()
    assert row.active_configuration.values["reminder_workgroup"] == NAME
    applied = browser.get(confirmed["Location"])
    assert "Applied: your change is saved" in region(applied.content)
    assert digest(applied) == store.active().digest
    assert b'data-workgroup-status="pending"' in applied.content
    # Change status itself still leads back to the page.
    status = browser.get(f"/admin/changes/{request}/").content
    assert f'<a href="{URL}">Return to Reminder WorkGroup</a>'.encode() in status


def test_refusals_are_explained_in_place_at_the_readers_version(auth_service, google):
    """Nothing changed, a stale page and an out-of-date Apply are refused in
    the review region; each keeps the version the reader's typing came from,
    so a page changed elsewhere is refused until it is reloaded."""
    store = auth_service.store
    row = setup(store)
    browser, _ = signed_in()
    loaded = digest(browser.get(URL))
    data = {"action": "preview", "name": "", "base_digest": loaded}
    unchanged = post(browser, URL, data)
    assert unchanged.status_code == 400
    assert "The Reminder WorkGroup has not changed." in region(unchanged.content)
    assert "Apply changes" not in region(unchanged.content)
    too_long = post(browser, URL, data | {"name": "x" * 500})
    assert too_long.status_code == 400
    assert "ParishSoft Family WorkGroup:" in unescape(region(too_long.content))
    stale = post(browser, URL, data | {"name": NAME, "base_digest": "a" * 64})
    assert stale.status_code == 409
    assert "This page changed in another tab or session." in region(stale.content)
    assert digest(stale) == "a" * 64
    proposal = token(post(browser, URL, data | {"name": NAME}))
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"reminder_workgroup": "Changed elsewhere"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    refused = post(browser, URL, {"action": "confirm", "preview": proposal})
    assert refused.status_code == 409
    assert "This preview is out of date." in region(refused.content)
    assert digest(refused) == loaded != store.active().digest
    assert not ConfigurationChangeRequest.objects.filter(
        patch__0__values__reminder_workgroup=NAME
    ).exists()
    # Only the change Apply names may be read from the address.
    assert browser.get(URL + "?extra=1").status_code == 400
