"""Archived previews retain exact content/branding and cannot mutate or send."""

from uuid import uuid4

import pytest
from django.test import override_settings

from parishkit.stewardship.accounts.branding_models import BrandingAsset
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change, restored_runtime
from .retained_content_urls import RETAINED
from .test_background_grants_postgresql import task_login
from .test_branding_postgresql import branding_patch, ready
from .test_clone_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


@override_settings(ROOT_URLCONF="tests.stewardship.database.retained_content_urls")
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
    # No longer the current campaign, so only the test route reaches it.
    url = RETAINED.format(campaign=campaign.pk)
    with task_login(ServiceRole.WEB):
        catalog = browser.get(url)
        assert catalog.status_code == 200
        response = browser.get(url + record["id"] + "/")
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


def test_history_is_get_only_and_revision_is_campaign_scoped(auth_service, google):
    """A UUID cannot select an absent revision or turn a preview into a write."""
    campaign, _ = setup(auth_service.store, returned=False)
    browser, _ = signed_in()
    path = "/admin/campaign/content/history/"
    assert browser.get(path).status_code == 200
    # The archived current campaign's settings link its history, not editing.
    settings = browser.get("/admin/campaign/settings/")
    assert path.encode() in settings.content
    assert b"Edit Pages and emails" not in settings.content
    count = ConfigurationChangeRequest.objects.count()
    assert (
        browser.post(
            path, {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
        ).status_code
        == 405
    )
    assert browser.get(f"{path}{uuid4()}/").status_code == 404
    assert browser.get(path, {"configuration": str(uuid4())}).status_code == 400
    assert browser.get(f"/admin/campaign/{uuid4()}/content/history").status_code == 410
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
    assert browser.get("/admin/campaign/content/history/").status_code == 403


def test_current_campaign_without_content_has_read_only_empty_catalog(
    auth_service, google
):
    """A blank selected slot is not filled from another campaign's configuration."""
    from .campaign_builders import add_draft

    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    campaign = Campaign.objects.get()
    browser, _ = signed_in()
    path = "/admin/campaign/content/history/"
    response = browser.get(path)
    assert response.status_code == 200
    assert b"No content was configured" in response.content
    with restored_runtime(campaign.active_configuration.starts_at):
        response = browser.get(path)
        assert response.status_code == 302
        assert response["Location"] == "/admin/maintenance"
        assert response["Cache-Control"] == "no-store"


def test_history_never_waits_behind_the_work_lock(auth_service, google):
    """A writer holding the work-order lock does not delay retained content.

    Another session holds the lock, as a source promotion or installer does.
    The history catalog and a revision still render under a short statement
    timeout; a regression back to the work lock fails fast with 503.
    """
    from django.db import connection

    from parishkit.stewardship.campaigns.work_locks import WORK_ORDER_LOCK

    from .test_schedule_views_postgresql import other_session

    campaign, _ = setup(auth_service.store, returned=False)
    record = campaign.active_configuration.configuration.canonical_document["sections"][
        "content"
    ][0]
    browser, _ = signed_in()
    path = "/admin/campaign/content/history/"
    with other_session() as holder:
        holder.execute("SELECT pg_advisory_lock(%s,%s)", WORK_ORDER_LOCK)
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '3s'")
        try:
            assert browser.get(path).status_code == 200
            assert browser.get(path + record["id"] + "/").status_code == 200
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET statement_timeout")
