"""Campaign images saves directly in the campaign wizard (#1404 PR 4c).

Real Google sessions, the restricted web role and the installer: inside the
campaign wizard Campaign images draws no Upload and review. Its action row's
Save and continue stages each chosen image at its slot's upload address, as
an upload outside the wizard does, and then posts ``save`` naming the staged
bundle of each slot with the page's request key. The server records one
change setting every slot at once; a repeat of the same save returns that
change's status, and a save naming no image, an unavailable slot, an image
of the wrong kind or another sign-in's, a stale version or a wizard that no
longer applies is refused with nothing recorded.
"""

import re
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.accounts import artwork_views, campaign_wizard
from parishkit.stewardship.accounts.admin_editing import direct_key
from parishkit.stewardship.accounts.branding_models import BrandingBundle
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in, unguarded
from .test_artwork_views_postgresql import (
    artwork,
    carried,
    draft,
    image,
    media,  # noqa: F401 (the private media fixture, used below)
    refusal,
    staged,
)
from .test_branding_views_postgresql import post
from .test_campaign_direct_save_postgresql import drawn_key
from .test_campaign_setup_review_postgresql import web
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_campaign_views_postgresql import apply, requested

pytestmark = pytest.mark.django_db(transaction=True)


def bundle(browser, service, path, slot, upload):
    """Stage ``upload`` at ``slot``'s upload address, as Save and continue
    does first; return the staged bundle id its answer carries."""
    with web():
        return carried(staged(browser, service, path, slot, upload))["bundle"]


def save(browser, path, data):
    """Post Campaign images' direct save, as the action row's form does."""
    with web():
        return post(browser, path, data)


@pytest.mark.usefixtures("media")
def test_campaign_images_save_directly_and_complete_the_step(auth_service, google):
    """Save and continue sets every chosen slot in one change, at once."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(path).content.decode()
    # The wizard's page: no Upload and review, each slot's form holds only
    # its file field, and the row's Save and continue posts the save form.
    assert "Upload and review" not in body
    assert body.count("data-wizard-upload=") == 5
    assert "data-require-complete" not in body
    assert '<form id="artwork-save-form"' in body
    assert re.search(
        r'<button type="submit" form="artwork-save-form" [^>]*data-campaign-save'
        r"[^>]*>Save and continue</button>",
        body,
    )
    # Remove this image keeps its confirmation in the review dialog.
    assert 'id="artwork-review"' in body
    data = {
        "action": "save",
        "request_key": str(drawn_key(body)),
        "base_digest": store.active().digest,
        "bundle_banner": bundle(
            browser, auth_service, path, "banner", image(1625, 345)
        ),
        "bundle_welcome": bundle(browser, auth_service, path, "welcome", image(90, 90)),
    }
    requests = ConfigurationChangeRequest.objects.count()
    response = save(browser, path, data)
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert response["Location"] == status_url
    recorded = ConfigurationChangeRequest.objects.get(pk=requested(response))
    # The change's key comes from the page's key and the staged ids.
    assert recorded.request_key == direct_key(QueryDict(urlencode(data)))
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    apply(store, response)
    assert set(artwork(row)["images"]) == {"banner", "welcome"}
    with web():
        region = stepper(browser.get(path).content)
    assert status_of(region, "Campaign images") == "Current step — Completed"
    # A repeat of the same save (its answer lost) is the same change.
    again = save(browser, path, data)
    assert again.status_code == 302 and again["Location"] == status_url
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # A save from the page drawn before that change, with a new key, is stale.
    stale = save(browser, path, data | {"request_key": str(uuid4())})
    assert stale.status_code == 409
    assert stale.stewardship_reason == "stale_page"
    assert ConfigurationChangeRequest.objects.count() == requests + 1


@pytest.mark.usefixtures("media")
def test_a_direct_save_refuses_what_it_cannot_set(auth_service, google, monkeypatch):
    """Each refusal records nothing and leaves the images as they were."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with web():
        key = drawn_key(browser.get(path).content.decode())
    banner = bundle(browser, auth_service, path, "banner", image(1625, 345))
    data = {
        "action": "save",
        "request_key": str(key),
        "base_digest": store.active().digest,
    }
    requests = ConfigurationChangeRequest.objects.count()
    # No image named, or a field the save does not have.
    assert save(browser, path, data).status_code == 400
    assert save(browser, path, data | {"slot": "banner"}).status_code == 400
    # The census-only draft shows no Financial page, so its slot takes none.
    unavailable = save(browser, path, data | {"bundle_financial": banner})
    assert unavailable.status_code == 400
    assert refusal(unavailable).startswith("Families see no Financial stewardship page")
    # A banner is not an icon: the staged image cannot be saved there.
    wrong = save(browser, path, data | {"bundle_welcome": banner})
    assert wrong.status_code == 409
    assert "Reload the page, then choose the image again." in refusal(wrong)
    # A stale version.
    older = data | {"base_digest": "f" * 64, "bundle_banner": banner}
    assert save(browser, path, older).status_code == 409
    # Setup ended meanwhile: refused under the work lock.
    monkeypatch.setattr(campaign_wizard, "current_wizard", lambda *_, **__: None)
    ended = save(browser, path, data | {"bundle_banner": banner})
    monkeypatch.undo()
    assert ended.status_code == 409
    assert refusal(ended) == "Campaign setup has ended, so nothing was saved."

    # The upload expires between the page's checks and the work lock.
    def expired(*_, **__):
        """The lock-time check finding the staged upload gone stale."""
        raise StaleRecordError("expired")

    monkeypatch.setattr(artwork_views, "applied_upload", expired)
    late = save(browser, path, data | {"bundle_banner": banner})
    monkeypatch.undo()
    assert late.status_code == 409
    assert "Reload the page, then choose the image again." in refusal(late)
    # Another sign-in's upload (seeded past the bundle's SQL guard).
    with unguarded():
        BrandingBundle.objects.filter(pk=UUID(banner)).update(session_id=uuid4())
    other = save(browser, path, data | {"bundle_banner": banner})
    assert other.status_code == 409
    assert "Reload the page, then choose the image again." in refusal(other)
    assert ConfigurationChangeRequest.objects.count() == requests
    assert artwork(row) == {}
