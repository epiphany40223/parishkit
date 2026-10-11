"""Campaign artwork (#248): per-campaign images and the per-email banner choice."""

import io
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from PIL import Image

from parishkit.stewardship.accounts import branding_validation
from parishkit.stewardship.accounts.branding_cleanup import unpinned_bundles
from parishkit.stewardship.accounts.branding_context import (
    banner_for_email,
    campaign_artwork,
)
from parishkit.stewardship.accounts.branding_models import BrandingAsset
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .campaign_builders import add_draft, change, command
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_branding_views_postgresql import post, preview_token
from .test_campaign_views_postgresql import apply
from .test_content_views_postgresql import values as content_values
from .test_parish_views_postgresql import region

pytestmark = pytest.mark.django_db(transaction=True)
ORIGIN = "https://parish.example.org"


@pytest.fixture
def media(tmp_path, settings):
    """Separate private media from the fixture's already durable authority store."""
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_MEDIA_ROOT = root
    return root


def image(width, height):
    """A real image the server must re-encode; its filename never becomes a URL."""
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "green").save(stream, format="PNG")
    return SimpleUploadedFile(
        "private-original.png", stream.getvalue(), content_type="image/png"
    )


def draft(store):
    """A real applied current draft campaign, and its artwork page path."""
    result, _, _ = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    row = Campaign.objects.get()
    return row, "/admin/campaign/images/"


def artwork(row):
    """The campaign's current artwork value (empty when absent)."""
    row.refresh_from_db()
    return row.active_configuration.values.get("artwork", {})


def address(response):
    """Where a redirect leads, without its region fragment."""
    assert response.status_code == 302, response.content
    return response["Location"].split("#")[0]


def confirmed(browser, review_path):
    """Review at ``review_path`` (Campaign images naming the change, #750) and
    confirm it there; the answer is Campaign images naming the request."""
    review = browser.get(review_path)
    assert flow_steps(review.content) == (STEPS, "Review")
    # The review and its Apply are in the page's review region.
    assert "Apply changes" in region(review.content)
    token = preview_token(review)
    response = post(browser, review_path, {"action": "confirm", "preview": token})
    assert address(response).startswith("/admin/campaign/images/?request=")
    assert response["Location"].endswith("#settings-review")
    # Where the one-time review was, for checks after it is confirmed.
    response.review_path = review_path
    return response


def select(browser, service, path, slot, upload):
    """Upload, review and confirm one slot as the web role; returns the receipt."""
    with task_login(ServiceRole.WEB):
        staged = post(
            browser,
            f"{path}{slot}/",
            {"base_digest": service.store.active().digest, "image": upload},
        )
        # The upload answers with Campaign images naming the staged image.
        assert address(staged).startswith(f"{path}?review={slot}&bundle=")
        return confirmed(browser, address(staged))


def remove(browser, path, slot):
    """Review and confirm removing one slot's image."""
    with task_login(ServiceRole.WEB):
        return confirmed(browser, f"{path}?remove={slot}")


def test_images_belong_to_the_campaign_publish_and_clear(auth_service, google, media):
    """Each slot takes one normalized image, is public once applied, and clears."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    page = browser.get(path)
    assert page.status_code == 200 and b"No image is set." in page.content
    assert flow_steps(page.content) == (STEPS, "Make changes")
    accepted = select(browser, auth_service, path, "banner", image(1625, 345))
    apply(store, accepted)
    # Campaign images follows the change in place (#750): Applied in its
    # review region, the new image among the slots.
    followed = browser.get(address(accepted))
    assert flow_steps(followed.content) == (STEPS, "Apply")
    assert "Applied: your change is saved" in region(followed.content)
    assert b"No image is set." in followed.content
    assert followed.content.count(b"Current image") == 1
    # Change status itself still returns to Campaign images (#196).
    status = browser.get(
        "/admin/changes/" + address(accepted).rsplit("=", 1)[1] + "/"
    ).content
    assert f'<a href="{path}">Return to Campaign images</a>'.encode() in status
    # That review no longer opens now its image is chosen.
    assert browser.get(accepted.review_path).status_code != 200
    apply(store, select(browser, auth_service, path, "financial", image(600, 600)))
    images = artwork(row)["images"]
    banner = BrandingAsset.objects.get(pk=images["banner"])
    icon = BrandingAsset.objects.get(pk=images["financial"])
    # Resized for email and phones; the aspect ratio is kept.
    assert (banner.label, banner.width, banner.height) == ("banner", 1024, 217)
    assert (icon.label, icon.width, icon.height) == ("section", 256, 256)
    # Nothing is stored on the parish.
    parish = store.active().document()["sections"]["parish"][0]["values"]
    assert "artwork" not in parish
    for asset in (banner, icon):
        response = browser.get(f"/branding/{asset.pk}.png")
        assert response.status_code == 200
        assert b"".join(response.streaming_content).startswith(b"\x89PNG")
    values = row.active_configuration.values
    resolved = campaign_artwork(values, origin=ORIGIN)
    assert set(resolved) == {"banner", "financial"}
    assert resolved["banner"]["url"] == f"{ORIGIN}/branding/{banner.pk}.png"
    assert banner_for_email(values, "initial", origin=ORIGIN) == resolved["banner"]
    # Selected images are pinned against staging cleanup.
    assert not unpinned_bundles().filter(pk=banner.bundle_id).exists()
    removed = remove(browser, path, "financial")
    apply(store, removed)
    # The removal review now refuses (nothing left to remove).
    assert browser.get(f"{path}?remove=financial").status_code != 200
    followed = browser.get(address(removed))
    assert flow_steps(followed.content) == (STEPS, "Apply")
    assert "Applied: your change is saved" in region(followed.content)
    apply(store, remove(browser, path, "banner"))
    # With no image left, the optional value is absent, not empty.
    assert artwork(row) == {}


def test_artwork_stays_editable_while_the_campaign_is_live(auth_service, google, media):
    """Theme images are presentation, not structure, so a live campaign can change."""
    store = auth_service.store
    row, path = draft(store)
    command(row, uuid4(), Action.ACTIVATE)
    browser, _ = signed_in()
    apply(store, select(browser, auth_service, path, "welcome", image(90, 90)))
    assert set(artwork(row)["images"]) == {"welcome"}


def test_a_slot_refuses_the_wrong_image_kind(auth_service, google, media, monkeypatch):
    """A section icon cannot be selected as the banner, in Python or in SQL."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        staged = post(
            browser,
            f"{path}member/",
            {"base_digest": store.active().digest, "image": image(90, 90)},
        )
        bundle = address(staged).rsplit("=", 1)[-1]
        # The same staged icon is not an image for the banner slot.
        assert browser.get(f"{path}?review=banner&bundle={bundle}").status_code == 404
        assert browser.get(f"{path}?review=unknown&bundle={bundle}").status_code == 404
        # Only the change, or the request, the page names may be read.
        assert browser.get(f"{path}?review=member").status_code == 400
        assert browser.get(f"{path}?remove=member&extra=1").status_code == 400
    section = BrandingAsset.objects.get(bundle_id=bundle)
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"artwork": {"images": {"banner": str(section.pk)}}},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "failed"
    # SQL refuses it on its own too, even with the Python check bypassed.
    monkeypatch.setattr(
        branding_validation, "_validate_campaign_artwork", lambda *a, **k: None
    )
    with pytest.raises(IntegrityError, match="ready normalized image"):
        change(store, store.active(), uuid4(), patch)
    assert artwork(row) == {}


def test_upload_rejects_non_images(auth_service, google, media):
    """A non-image upload stays on the page with a field error; nothing is staged."""
    store = auth_service.store
    _, path = draft(store)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        result = post(
            browser,
            f"{path}banner/",
            {
                "base_digest": store.active().digest,
                "image": SimpleUploadedFile("x.png", b"not an image"),
            },
        )
    assert result.status_code == 400
    assert b"Choose a supported, bounded static image:" in result.content
    # Summarized in the review region too, linked to the slot's field (#750).
    assert 'href="#id_banner_image"' in region(result.content)
    assert not BrandingAsset.objects.exists()


def change_parish(store):
    """Apply an unrelated settings change, as another Administrator might."""
    parish = store.active().document()["sections"]["parish"][0]
    patch = [
        {
            "operation": "update",
            "section": "parish",
            "id": parish["id"],
            "values": {"name": "Changed parish"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def test_a_refused_apply_is_explained_in_place(auth_service, google, media):
    """An Apply refused because the settings changed since the review is the
    page again, explained in its review region (#750): an upload staged
    against the old settings, and a removal whose preview is out of date."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        staged = post(
            browser,
            f"{path}welcome/",
            {"base_digest": store.active().digest, "image": image(90, 90)},
        )
        review_path = address(staged)
        upload_token = preview_token(browser.get(review_path))
    set_banner(store, row.pk)
    with task_login(ServiceRole.WEB):
        removal_token = preview_token(browser.get(f"{path}?remove=banner"))
    change_parish(store)
    with task_login(ServiceRole.WEB):
        refused = post(
            browser, review_path, {"action": "confirm", "preview": upload_token}
        )
        assert refused.status_code == 409
        assert "Upload the image again." in region(refused.content)
        refused = post(
            browser,
            f"{path}?remove=banner",
            {"action": "confirm", "preview": removal_token},
        )
        assert refused.status_code == 409
        assert "This preview is out of date." in region(refused.content)
        # Apply posts only to an address naming the change.
        assert (
            post(
                browser, path, {"action": "confirm", "preview": removal_token}
            ).status_code
            == 400
        )
    assert set(artwork(row)["images"]) == {"banner"}


def test_each_family_email_chooses_whether_to_show_the_banner(auth_service, google):
    """The email editor's checkbox stores a per-campaign, per-email choice."""
    store = auth_service.store
    row, _ = draft(store)
    browser, _ = signed_in()
    path = "/admin/campaign/content/email/reminder/"
    page = browser.get(path)
    assert b"Show the campaign banner at the top of this email" in page.content
    assert b'name="show_banner"' in page.content and b"checked" in page.content
    html = "<p>{{ family_code }} {{ family_url }}</p>"
    # Unchecked: the reminder hides the banner.
    proposal = preview_token(
        post(browser, path, content_values(store, subject="Reminder", html=html))
    )
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))
    assert artwork(row) == {"hide_banner": ["reminder"]}
    values = row.active_configuration.values
    assert banner_for_email(values, "reminder", origin=ORIGIN) is None
    # Checked again: the choice is removed and the value disappears.
    proposal = preview_token(
        post(
            browser,
            path,
            content_values(store, subject="Reminder", html=html, show_banner="on"),
        )
    )
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))
    assert artwork(row) == {}
    # Admin-only emails and pages offer no banner choice.
    assert (
        b"show_banner"
        not in browser.get("/admin/campaign/content/email/daily_digest/").content
    )
    assert (
        b"show_banner"
        not in browser.get("/admin/campaign/content/page/welcome/").content
    )


def set_banner(store, campaign_id, *, hide=None):
    """Select a real ready banner for a campaign through the actual installer.

    Dispatch reads only database rows, so no media file is needed. Returns the
    banner asset's id.
    """
    from datetime import timedelta

    from django.db.models import F

    from parishkit.stewardship.accounts.branding_models import BrandingBundle
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedConfigurationVersion,
    )
    from parishkit.stewardship.accounts.sessions import database_now

    actor = uuid4()
    version = store.active()
    bundle = BrandingBundle.objects.create(
        owner_id=actor,
        actor_id=actor,
        session_id=uuid4(),
        base=AppliedConfigurationVersion.objects.get(digest=version.digest),
        expires_at=database_now() + timedelta(hours=1),
        correlation_id=uuid4(),
    )
    asset = BrandingAsset.objects.create(
        id=uuid4(),
        bundle=bundle,
        label="banner",
        width=1024,
        height=217,
        size=1,
        sha256="0" * 64,
        actor_id=actor,
        correlation_id=uuid4(),
    )
    BrandingBundle.objects.filter(pk=bundle.pk).update(
        state="ready", version=F("version") + 1, actor_id=actor
    )
    artwork = {"images": {"banner": str(asset.pk)}}
    if hide:
        artwork["hide_banner"] = hide
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign_id),
            "values": {"artwork": artwork},
        }
    ]
    assert change(store, version, actor, patch).state == "applied"
    return asset.pk
