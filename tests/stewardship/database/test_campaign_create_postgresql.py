"""Create the campaign: the first campaign, after system setup (#142).

Real Google sessions, YAML requests, the configuration installer, SQL guards
and a promoted ParishSoft snapshot. Creation is admitted only while the
deployment has never had a campaign; it is not a way to add a second one.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.campaign_create_views import SALT
from parishkit.stewardship.accounts.campaign_views import SALT as SETTINGS_SALT
from parishkit.stewardship.accounts.content_forms import (
    applicable_slots,
    matches_default,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import apply, fields, post, requested
from .test_current_chair_postgresql import publish
from .test_parish_views_postgresql import token
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
CREATE = "/admin/campaign/first/"


def test_first_campaign_is_created_with_default_content(auth_service, google):
    """Review, confirm and apply one request that adds the draft and its text.

    The review, the confirmation's answer and the change's status are all this
    page again, in its review region (#532), as on Campaign settings.
    """
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    home = browser.get("/admin/").content
    assert f'href="{CREATE}"'.encode() in home
    response = browser.get(CREATE)
    assert response.status_code == 200 and b"Create the campaign" in response.content
    assert flow_steps(response.content) == (STEPS, "Make changes")
    preview = post(browser, CREATE, fields(store))
    assert b"Pages and emails start with the default text." in preview.content
    assert b'id="settings-review"' in preview.content
    assert b"Nothing is saved until you choose Create the campaign." in preview.content
    assert flow_steps(preview.content) == (STEPS, "Review")
    proposal = token(preview)
    assert not Campaign.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()
    accepted = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    request_id = requested(accepted)
    assert accepted["Location"] == (f"{CREATE}?request={request_id}#settings-review")
    assert (
        post(browser, CREATE, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )
    # Still pending: the region polls Change status, and offers no refresh
    # yet. The offer is drawn inside the live region, so it appears in place.
    pending = browser.get(accepted["Location"]).content.decode()
    assert "data-live-pending" in pending and "data-campaign-refresh" not in pending
    # The poll names no in-place page: once applied, the campaign exists, so
    # nothing refreshes this page (its follow-up is for settings pages only).
    status_url = reverse("admin:configuration_request", args=[request_id])
    assert f'data-live-url="{status_url}"' in pending
    apply(store, accepted)
    row = Campaign.objects.get()
    assert row.state == "draft" and not row.structural_locked
    assert SystemConfiguration.objects.get().current_campaign_id == row.pk
    # Once applied, the change's live status offers the campaign's full
    # refresh, posted to Refresh from ParishSoft's own action (#142): on this
    # page's region (a reload of the confirmation's answer, though the
    # campaign now exists), in the poll it reads, and on Change status, which
    # names Create the campaign but never links it (#196).
    for status in (
        browser.get(accepted["Location"]).content,
        browser.get(status_url).content,
    ):
        assert f'href="{CREATE}"'.encode() not in status
        assert b"Load the campaign's ParishSoft data" in status
        region = status.split(b'data-live-status="configuration"', 1)[1]
        assert b"data-campaign-refresh" in region.split(b"</section>", 1)[0]
        assert f'action="{reverse("admin:source_refresh")}"'.encode() in status
        assert b"data-live-follow" not in status
    # An identical retry after the campaign exists still returns the receipt.
    late = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    assert late["Location"] == accepted["Location"]
    assert ConfigurationChangeRequest.objects.count() == 1
    document = store.active().document()["sections"]
    records = [
        record
        for record in document["content"]
        if record["values"]["campaign_id"] == str(row.pk)
    ]
    values = row.active_configuration.values
    assert sorted((r["values"]["kind"], r["values"]["slot"]) for r in records) == (
        sorted(applicable_slots(values))
    )
    assert records and all(matches_default(record["values"]) for record in records)
    # Once a campaign exists the page and Home no longer offer creation.
    assert browser.get(CREATE).status_code == 409
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content


def test_creation_is_refused_once_any_campaign_exists(auth_service, google):
    """A preview signed with no campaign cannot add a second one later."""
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, fields(store)))
    add_draft(store, store.active(), uuid4())
    requests = ConfigurationChangeRequest.objects.count()
    # Both are refused in place: the page again, the explanation in its
    # review region, and no review or Create button.
    for refused in (
        post(browser, CREATE, {"action": "confirm", "preview": proposal}),
        post(browser, CREATE, fields(store)),
    ):
        assert refused.status_code == 409
        content = refused.content.decode()
        region = content.split('id="settings-review"', 1)[1]
        assert "The campaign can&#x27;t be created right now." in region
        assert 'name="preview"' not in content
    assert ConfigurationChangeRequest.objects.count() == requests
    assert Campaign.objects.count() == 1


def test_creation_needs_loaded_parish_data(auth_service, google):
    """Without a promoted ParishSoft snapshot there is nothing to choose from."""
    browser, _ = signed_in()
    assert browser.get(CREATE).status_code == 409
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content


def test_creation_and_settings_previews_cannot_be_swapped(auth_service, google):
    """Each page signs with its own salt, so neither accepts the other's review."""
    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, fields(store)))
    assert SALT != SETTINGS_SALT
    refused = post(
        browser, "/admin/campaign/settings/", {"action": "confirm", "preview": proposal}
    )
    assert refused.status_code >= 400
    assert not ConfigurationChangeRequest.objects.exists()


def test_an_ordinary_change_offers_no_campaign_refresh(auth_service, google):
    """Only the change that created the current campaign offers its refresh."""
    from parishkit.stewardship.campaigns.models import Campaign as Row

    from .test_campaign_views_postgresql import url

    publish(source())
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Row.objects.get()
    browser, _ = signed_in()
    proposal = token(post(browser, url(row), fields(store, row, name="Renamed")))
    accepted = post(browser, url(row), {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    status_url = reverse("admin:configuration_request", args=[requested(accepted)])
    # The ordinary status renders (a regression check for #786's H1) and
    # offers no campaign refresh: on Change status, and in Campaign settings'
    # own in-place region (#532).
    for response in (browser.get(status_url), browser.get(accepted["Location"])):
        assert response.status_code == 200
        assert b"Applied: your change is saved" in response.content
        assert b"data-campaign-refresh" not in response.content
        assert b"Load the campaign's ParishSoft data" not in response.content
    # Change status answers only safe methods: a POST with a valid CSRF token
    # is refused by the method guard itself.
    assert post(browser, status_url, {}).status_code == 405


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_only_an_administrator_may_create_the_campaign(auth_service, google, role):
    """Other roles can neither open the page nor post to it."""
    publish(source())
    store = auth_service.store
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
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    requests = ConfigurationChangeRequest.objects.count()
    assert browser.get(CREATE).status_code == 403
    assert post(browser, CREATE, fields(store)).status_code == 403
    assert f'href="{CREATE}"'.encode() not in browser.get("/admin/").content
    assert ConfigurationChangeRequest.objects.count() == requests


@pytest.mark.parametrize("lost", ["capability", "identity"])
def test_an_identical_retry_still_needs_current_access(
    auth_service, google, monkeypatch, lost
):
    """confirm_intent checks capability and identity before an existing key.

    An identical retry returns its receipt even when the page's own scope
    would now refuse (#786), but never for an Administrator who has lost the
    capability, or for a session that now belongs to someone else.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import admin_editing

    publish(source())
    browser, _ = signed_in()
    store = auth_service.store
    proposal = token(post(browser, CREATE, fields(store)))
    accepted = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    # Only confirm_intent's own recheck (a read-only re-read of the session)
    # sees the change; the page's admission before it still passes.
    real_admin, real_allows = admin_editing.authenticated_admin, admin_editing.allows
    rechecked = []

    def recheck(*args, **kwargs):
        """The session re-read; inside admit() it may belong to someone else."""
        actor = real_admin(*args, **kwargs)
        if kwargs.get("read_only"):
            rechecked.append(True)
            if lost == "identity":
                return SimpleNamespace(**vars(actor) | {"identity": uuid4()})
        return actor

    def capability(actor, wanted):
        """The capability, lost by the time admit() rechecks it."""
        return (
            False if lost == "capability" and rechecked else real_allows(actor, wanted)
        )

    monkeypatch.setattr(admin_editing, "authenticated_admin", recheck)
    monkeypatch.setattr(admin_editing, "allows", capability)
    retry = post(browser, CREATE, {"action": "confirm", "preview": proposal})
    assert rechecked, "the retry reached confirm_intent's recheck"
    assert retry.status_code == 403
    assert ConfigurationChangeRequest.objects.count() == 1
