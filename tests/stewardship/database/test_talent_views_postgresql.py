"""Member talent changes pass through the real installer and web grants (#247)."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.service import default_talent_options

from ..campaign_factory import campaign
from ..test_share_forms import data_for
from .auth_builders import signed_in
from .campaign_builders import add_draft, command
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, post, requested
from .test_parish_views_postgresql import region, token

pytestmark = pytest.mark.django_db(transaction=True)


def setup(store):
    """A Ministry draft that never edited its talents (it shows the defaults)."""
    result, _, _ = add_draft(
        store,
        store.active(),
        uuid4(),
        campaign(modules=["ministry"], ministry_duids=[7]),
    )
    assert result.state == "applied"
    row = Campaign.objects.get()
    return row, default_talent_options(), "/admin/campaign/talents/"


def test_first_save_keeps_the_default_identities(auth_service, google):
    """Editing the defaults stores them with their fixed identities, reordered."""
    store = auth_service.store
    row, previous, path = setup(store)
    assert "talent_options" not in row.active_configuration.values
    browser, _ = signed_in()
    page = browser.get(path)
    assert page.status_code == 200 and b"Painter" in page.content
    assert flow_steps(page.content) == (STEPS, "Make changes")
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Artist",
        "options-0-ORDER": "2",
        "options-1-ORDER": "1",
    }
    preview = post(browser, path, data)
    assert flow_steps(preview.content) == (STEPS, "Review")
    proposal = token(preview)
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))
    row.refresh_from_db()
    options = row.active_configuration.values["talent_options"]
    assert [item["id"] for item in options[:2]] == [
        previous[1]["id"],
        previous[0]["id"],
    ]
    assert options[1]["label"] == "Artist"


def test_restricted_web_may_empty_the_list(auth_service, google):
    """Deleting every talent stores an explicit empty list, not the defaults."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {"base_digest": store.active().digest}
    data.update({f"options-{index}-DELETE": "on" for index in range(len(previous))})
    with task_login(ServiceRole.WEB):
        assert browser.get(path).status_code == 200
        proposal = token(post(browser, path, data))
        response = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.values["talent_options"] == []


def test_talents_lock_when_live_and_need_the_ministry_module(auth_service, google):
    """Talents are structural, and a census-only campaign has none to edit."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Changed",
    }
    proposal = token(post(browser, path, data))
    command(row, uuid4(), Action.ACTIVATE)
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    assert browser.get(path + "?extra=1").status_code == 400


def test_census_only_campaign_has_no_talent_controls(auth_service, google):
    """Without the Ministry page there is nothing to edit."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    assert browser.get("/admin/campaign/talents/").status_code == 409


def test_talents_review_apply_and_status_stay_on_the_page(auth_service, google):
    """Member talents reviews, applies and follows a change in place (#750).

    The review shows the order and labels before and after under the form,
    which is redrawn with the values sent; Apply answers with the page,
    whose review region shows the change's status, polled from Change
    status's passive read, and then the applied list.
    """
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Artist",
    }
    review = post(browser, path, data)
    shown = region(review.content)
    assert "Review your changes" in shown and "Proposed order and labels" in shown
    assert "Artist" in shown and "Painter" in shown
    assert b'id="settings-editor" data-in-place-region' in review.content
    assert b'value="Artist"' in review.content
    response = post(browser, path, {"action": "confirm", "preview": token(review)})
    request_id = requested(response)
    assert response["Location"] == f"{path}?request={request_id}#settings-review"
    page = browser.get(response["Location"])
    assert flow_steps(page.content) == (STEPS, "Apply")
    assert "?in_place=talent_settings" in region(page.content)
    apply(store, response)
    assert "Applied: your change is saved" in region(
        browser.get(response["Location"]).content
    )
    row.refresh_from_db()
    assert row.active_configuration.values["talent_options"][0]["label"] == "Artist"
