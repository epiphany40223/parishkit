"""Campaign artwork (#248): per-campaign images and the per-email banner choice."""

import io
import re
from html import unescape
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
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft, change, command
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_branding_views_postgresql import post, preview_token
from .test_campaign_views_postgresql import apply, requested
from .test_content_views_postgresql import values as content_values

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


def carried(review):
    """The hidden fields of a review dialog fragment, which its Apply posts:
    the signed preview and the slot (and staged bundle) it was signed for."""
    assert review.status_code == 200, review.content
    # The review dialog's fragment, never a page (#750).
    assert b"data-review-answer" in review.content
    assert b"<h1>" not in review.content
    return {
        name: unescape(value)
        for name, value in re.findall(
            r'<input type="hidden" name="(\w+)" value="([^"]*)"',
            review.content.decode(),
        )
    }


def confirmed(browser, path, review):
    """Apply a review as the dialog does: its fields posted to Campaign
    images, answered with the change's status, which the dialog follows."""
    response = post(browser, path, {"action": "confirm"} | carried(review))
    assert response.status_code == 302, response.content
    assert response["Location"].startswith("/admin/changes/")
    return response


def staged(browser, service, path, slot, upload):
    """Upload one slot's image; the answer is the review dialog's fragment."""
    return post(
        browser,
        f"{path}{slot}/",
        {"base_digest": service.store.active().digest, "image": upload},
    )


def select(browser, service, path, slot, upload):
    """Upload, review and confirm one slot as the web role; returns the receipt."""
    with task_login(ServiceRole.WEB):
        return confirmed(browser, path, staged(browser, service, path, slot, upload))


def removal(browser, service, path, slot, digest=None):
    """Review removing one slot's image (Remove this image's own form)."""
    return post(
        browser,
        path,
        {
            "action": "preview",
            "slot": slot,
            "base_digest": digest or service.store.active().digest,
        },
    )


def remove(browser, service, path, slot):
    """Review and confirm removing one slot's image."""
    with task_login(ServiceRole.WEB):
        return confirmed(browser, path, removal(browser, service, path, slot))


def refusal(response):
    """A plain refusal's message, as the review dialog shows it."""
    return response.json()["refusal"]["message"]


def test_images_belong_to_the_campaign_publish_and_clear(auth_service, google, media):
    """Each slot takes one normalized image, is public once applied, and clears."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    page = browser.get(path)
    assert page.status_code == 200 and b"No image is set." in page.content
    assert flow_steps(page.content) == (STEPS, "Make changes")
    with task_login(ServiceRole.WEB):
        review = staged(browser, auth_service, path, "banner", image(1625, 345))
    assert b"This image is uploaded but not yet in use." in review.content
    assert b'data-review-done="Campaign image changed."' in review.content
    accepted = confirmed(browser, path, review)
    apply(store, accepted)
    # Change status still returns to Campaign images (#196).
    status = browser.get(accepted["Location"]).content
    assert b"Applied: your change is saved" in status
    assert f'<a href="{path}">Return to Campaign images</a>'.encode() in status
    # The page the dialog redraws the slots from shows the new image.
    followed = browser.get(path)
    assert b"No image is set." in followed.content
    assert followed.content.count(b"Current image") == 1
    # Only a slot with an image offers Remove this image.
    assert followed.content.count(b'id="artwork-remove-') == 1
    assert b'id="artwork-remove-banner"' in followed.content
    # That upload's preview cannot be applied again as a new change: an
    # exact retry returns the same request.
    again = confirmed(browser, path, review)
    assert requested(again) == requested(accepted)
    apply(store, select(browser, auth_service, path, "closing", image(600, 600)))
    images = artwork(row)["images"]
    banner = BrandingAsset.objects.get(pk=images["banner"])
    icon = BrandingAsset.objects.get(pk=images["closing"])
    # The page shows exactly the applied images as the slots' current ones.
    shown = browser.get(path).content.decode()
    for asset in (banner, icon):
        assert f'<img src="/branding/{asset.pk}.png"' in shown
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
    assert set(resolved) == {"banner", "closing"}
    assert resolved["banner"]["url"] == f"{ORIGIN}/branding/{banner.pk}.png"
    assert banner_for_email(values, "initial", origin=ORIGIN) == resolved["banner"]
    # Selected images are pinned against staging cleanup.
    assert not unpinned_bundles().filter(pk=banner.bundle_id).exists()
    removed = remove(browser, auth_service, path, "closing")
    apply(store, removed)
    # The removal can no longer be reviewed (nothing left to remove): a
    # plain refusal, shown in the dialog.
    with task_login(ServiceRole.WEB):
        gone = removal(browser, auth_service, path, "closing")
    assert gone.status_code == 409
    assert "can no longer be reviewed" in refusal(gone)
    apply(store, remove(browser, auth_service, path, "banner"))
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
        review = carried(staged(browser, auth_service, path, "member", image(90, 90)))
        bundle = review["bundle"]
        # The same staged icon's preview is not one for the banner slot, nor
        # for a removal: its salt names the slot and the bundle.
        for fields in (
            review | {"slot": "banner"},
            {"preview": review["preview"], "slot": "member"},
        ):
            assert (
                post(browser, path, {"action": "confirm"} | fields).status_code == 400
            )
        assert (
            post(browser, path, {"action": "confirm"} | review | {"slot": "unknown"})
        ).status_code == 404
        # An image is reviewed only by its upload; a Review naming a bundle,
        # or any field beyond a removal's, is refused.
        digest = store.active().digest
        for fields in (
            {"slot": "member", "bundle": bundle, "base_digest": digest},
            {"slot": "member", "base_digest": digest, "extra": "1"},
        ):
            assert (
                post(browser, path, {"action": "preview"} | fields).status_code == 400
            )
        # The page takes no query (no legacy review addresses).
        for query in (f"review=member&bundle={bundle}", "remove=member", "request=1"):
            assert browser.get(f"{path}?{query}").status_code == 400
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
    # The page again (the dialog stays shut), its summary linked to the
    # slot's field, which ui-v1.js marks on the form (#750).
    assert b'<form id="artwork-upload-banner"' in result.content
    assert b'href="#id_banner_image"' in result.content
    assert b"data-error-summary" in result.content
    assert not BrandingAsset.objects.exists()
    # An upload from a page whose settings changed elsewhere is a plain
    # refusal, shown in the dialog, and stages nothing.
    with task_login(ServiceRole.WEB):
        stale = post(
            browser,
            f"{path}banner/",
            {"base_digest": "f" * 64, "image": image(1625, 345)},
        )
    assert stale.status_code == 409
    assert refusal(stale) == "This page changed in another tab or session."
    assert not BrandingAsset.objects.exists()


def test_a_slot_no_family_page_shows_cannot_take_an_upload(auth_service, google, media):
    """The census-only draft shows no Financial page, so its icon's Upload is
    unavailable, named by why, and the server refuses one at the field (#563).
    Every other slot waits for a chosen image (the complete gate)."""
    store = auth_service.store
    _, path = draft(store)
    browser, _ = signed_in()
    page = browser.get(path).content.decode()
    reason = "Families see no Financial page while Financial stewardship is off"
    assert (
        '<fieldset class="artwork-upload" disabled>' in page
        and page.count('<fieldset class="artwork-upload" disabled>') == 1
    )
    assert f'<p class="help" id="artwork-hint-financial">{reason}' in page
    # The greyed field is named by its help and that reason, as Upload is.
    assert (
        'aria-describedby="id_financial_image_helptext artwork-hint-financial"' in page
    )
    # Census is on, so the Member pages icon is available, gated on a file.
    assert '<p class="help" id="artwork-hint-member" data-complete-hint="reserve">' in (
        page
    )
    assert page.count("data-require-complete") == 4
    assert 'data-max-bytes="5242880"' in page
    with task_login(ServiceRole.WEB):
        refused = post(
            browser,
            f"{path}financial/",
            {"base_digest": store.active().digest, "image": image(600, 600)},
        )
    assert refused.status_code == 400
    assert reason in refused.content.decode()
    assert 'href="#id_financial_image"' in refused.content.decode()
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


def test_an_apply_needs_its_own_live_upload(auth_service, google, media, monkeypatch):
    """Apply uses only the staged upload its preview was signed for, and only
    while that upload is this sign-in's and unexpired: another staged
    upload for the same slot, an expired upload and another sign-in's are
    refused, and nothing is recorded."""
    from datetime import timedelta
    from uuid import UUID

    from parishkit.stewardship.accounts import branding_staging
    from parishkit.stewardship.accounts.branding_models import BrandingBundle
    from parishkit.stewardship.accounts.sessions import database_now

    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        first = carried(staged(browser, auth_service, path, "welcome", image(90, 90)))
        second = carried(staged(browser, auth_service, path, "welcome", image(80, 80)))
    assert first["bundle"] != second["bundle"]
    before = ConfigurationChangeRequest.objects.count()

    with task_login(ServiceRole.WEB):
        # One upload's preview with another staged upload for the same slot.
        swapped = first | {"bundle": second["bundle"]}
        assert post(browser, path, {"action": "confirm"} | swapped).status_code == 400
    # An expired upload (staging's clock past its day): refused in the
    # dialog, saying to start again.
    with monkeypatch.context() as clock, task_login(ServiceRole.WEB):
        later = database_now() + timedelta(days=2)
        clock.setattr(branding_staging, "database_now", lambda: later)
        expired = post(browser, path, {"action": "confirm"} | first)
    assert expired.status_code == 409
    assert "Reload the page, then upload the image again." in refusal(expired)
    # Another sign-in's upload is not this one's to apply (seeded past the
    # bundle's SQL guard).
    with unguarded():
        BrandingBundle.objects.filter(pk=UUID(second["bundle"])).update(
            session_id=uuid4()
        )
    with task_login(ServiceRole.WEB):
        other = post(browser, path, {"action": "confirm"} | second)
    assert other.status_code == 404
    assert ConfigurationChangeRequest.objects.count() == before
    assert artwork(row) == {}


def test_a_refused_apply_is_a_plain_refusal(auth_service, google, media):
    """An Apply refused because the settings changed since the review is a
    plain refusal, which the review dialog shows (#750): an upload staged
    against the old settings, and a removal whose preview is out of date.
    Nothing is recorded, and a preview is bound to its own change."""
    store = auth_service.store
    row, path = draft(store)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        upload = carried(staged(browser, auth_service, path, "welcome", image(90, 90)))
    set_banner(store, row.pk)
    with task_login(ServiceRole.WEB):
        removing = carried(removal(browser, auth_service, path, "banner"))
        # A removal reviewed from a page whose version is out of date is
        # refused before any preview is signed.
        stale = removal(browser, auth_service, path, "banner", digest="a" * 64)
        assert stale.status_code == 409
        assert refusal(stale) == "This page changed in another tab or session."
    change_parish(store)
    before = ConfigurationChangeRequest.objects.count()
    with task_login(ServiceRole.WEB):
        refused = post(browser, path, {"action": "confirm"} | upload)
        assert refused.status_code == 409
        assert "Reload the page, then upload the image again." in refusal(refused)
        refused = post(browser, path, {"action": "confirm"} | removing)
        assert refused.status_code == 409
        assert "This preview is out of date." in refusal(refused)
        # A preview signed for one change is refused for another.
        for fields in (
            removing | {"slot": "welcome"},
            removing | {"slot": "financial"},
            upload | {"slot": "closing"},
            {"preview": upload["preview"], "slot": "welcome"},
        ):
            assert (
                post(browser, path, {"action": "confirm"} | fields).status_code == 400
            )
    assert ConfigurationChangeRequest.objects.count() == before
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
