"""Archived previews retain exact content/branding and cannot mutate or send."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.branding_models import BrandingAsset
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change, restored_runtime
from .test_background_grants_postgresql import task_login
from .test_branding_postgresql import branding_patch, ready
from .test_clone_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


def test_archived_samples_keep_old_parish_content_and_logo(auth_service, google):
    """A global Parish edit neither repaints an archive nor changes substitutions."""
    store = auth_service.store
    actor = uuid4()
    old_logo, values = ready(store.active(), actor)
    change(store, store.active(), actor, branding_patch(store.active(), values))
    campaign, _ = setup(store)
    old = campaign.active_configuration.configuration
    old_name = old.parish.name
    record = old.canonical_document["sections"]["content"][0]
    current = store.active()
    new_logo, values = ready(current, actor)
    patch = branding_patch(current, values)
    patch[0]["values"]["name"] = "New Parish Name"
    assert change(store, current, actor, patch).state == "applied"
    browser, _ = signed_in()
    url = f"/admin/campaign/{campaign.pk}/content/history"
    with task_login(ServiceRole.WEB):
        catalog = browser.get(url)
        assert catalog.status_code == 200
        response = browser.get(url + "/" + record["id"])
        assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    assert b"Read-only fictional sample" in response.content
    assert old_name.encode() in response.content
    assert b"New Parish Name" not in response.content
    old_menu = BrandingAsset.objects.get(bundle=old_logo, label="menu").pk
    new_menu = BrandingAsset.objects.get(bundle=new_logo, label="menu").pk
    assert str(old_menu).encode() in response.content
    assert str(new_menu).encode() not in response.content
    assert b"Welcome to" in response.content
    assert b"Apply changes" not in response.content
    assert b"contenteditable" not in response.content
    settings = browser.get(f"/admin/campaign/{campaign.pk}/settings")
    assert url.encode() in settings.content
    assert b"Edit campaign pages" not in settings.content


def test_history_is_get_only_and_revision_is_campaign_scoped(auth_service, google):
    """A UUID cannot select an absent revision or turn a preview into a write."""
    campaign, _ = setup(auth_service.store)
    browser, _ = signed_in()
    path = f"/admin/campaign/{campaign.pk}/content/history"
    assert browser.get(path).status_code == 200
    count = ConfigurationChangeRequest.objects.count()
    assert (
        browser.post(
            path, {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
        ).status_code
        == 405
    )
    assert browser.get(path + "/" + str(uuid4())).status_code == 404
    assert browser.get(path, {"configuration": str(uuid4())}).status_code == 400
    assert browser.get(f"/admin/campaign/{uuid4()}/content/history").status_code == 404
    assert ConfigurationChangeRequest.objects.count() == count


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_read_retained_configuration(auth_service, google, role):
    """Knowing an archived campaign UUID does not disclose its configuration."""
    store = auth_service.store
    campaign, _ = setup(store)
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("observer@example.org", [role]),
            }
        ],
    )
    google[0]["email"] = "observer@example.org"
    browser, _ = signed_in()
    assert (
        browser.get(f"/admin/campaign/{campaign.pk}/content/history").status_code == 403
    )


def test_current_campaign_without_content_has_read_only_empty_catalog(
    auth_service, google
):
    """A blank selected slot is not filled from another campaign's configuration."""
    from .campaign_builders import add_draft

    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    campaign = Campaign.objects.get()
    browser, _ = signed_in()
    path = f"/admin/campaign/{campaign.pk}/content/history"
    response = browser.get(path)
    assert response.status_code == 200
    assert b"No content was configured" in response.content
    with restored_runtime(campaign.active_configuration.starts_at):
        assert browser.get(path).status_code != 200
