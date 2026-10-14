"""Logo upload/preview/application through real Admin HTTP and durable media files."""

import io
import re
from html import unescape
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from parishkit.stewardship.accounts.branding_models import BrandingAsset, BrandingBundle
from parishkit.stewardship.accounts.branding_staging import cleanup_branding
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.web.security import FILE_CSP

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_parish_views_postgresql import region

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/logos/"


@pytest.fixture
def media(tmp_path, settings):
    """Separate private media from the fixture's already durable authority store."""
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_MEDIA_ROOT = root
    return root


def upload():
    """A real re-encoded logo; its original filename must never become a URL."""
    stream = io.BytesIO()
    Image.new("RGB", (150, 80), "blue").save(stream, format="PNG")
    return SimpleUploadedFile(
        "private-original.png", stream.getvalue(), content_type="image/png"
    )


def post(browser, url, values):
    """Use the actual CSRF cookie instead of bypassing the middleware."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def stage(browser, service):
    """Upload once and return the owner-only review address actually returned:
    Parish logos naming the staged logos (#750), without its region fragment."""
    result = post(
        browser, URL, {"base_digest": service.store.active().digest, "logo": upload()}
    )
    assert result.status_code == 302, result.content
    location, fragment = result["Location"].split("#")
    assert location.startswith(f"{URL}?review=") and fragment == "settings-review"
    return location


def preview_token(response):
    """Confirm only the server's signed reference set, not hand-constructed IDs."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def test_logo_becomes_public_only_after_yaml_activation(auth_service, google, media):
    """Readiness is not selection; all four variants retain immutable public URLs."""
    browser, _ = signed_in()
    old = auth_service.store.active()
    with task_login(ServiceRole.WEB):
        listing = browser.get(URL)
        assert listing.status_code == 200
        assert flow_steps(listing.content) == (STEPS, "Make changes")
        preview = stage(browser, auth_service)
        page = browser.get(preview)
        assert flow_steps(page.content) == (STEPS, "Review")
        # Reviewed in place (#750): every size and Apply in the review region.
        assert "Apply changes" in region(page.content)
        assert region(page.content).count("<figure") == 4
        token = preview_token(page)
        assert b"private-original" not in page.content
        assets = list(BrandingAsset.objects.order_by("label"))
        for asset in assets:
            response = browser.get(f"{URL}assets/{asset.pk}.png")
            assert response.status_code == 200
            assert b"".join(response.streaming_content).startswith(b"\x89PNG")
            assert response["Cache-Control"] == "no-store"
            assert browser.get(f"/branding/{asset.pk}.png").status_code == 404
        result = post(browser, preview, {"action": "confirm", "preview": token})
        assert result.status_code == 302, result.content
    assert auth_service.store.active() == old
    # Apply answers with Parish logos naming the change, not Change status.
    location = result["Location"].split("#")[0]
    assert location.startswith(f"{URL}?request=")
    request = ConfigurationChangeRequest.objects.get(pk=location.rsplit("=", 1)[1])
    with task_login(ServiceRole.CONFIG_INSTALLER):
        assert (
            install_request(
                auth_service.store, request_id=request.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )
    # The page follows the change in place: Applied, and the new logos.
    followed = browser.get(location)
    assert flow_steps(followed.content) == (STEPS, "Apply")
    assert "Applied: your change is saved" in region(followed.content)
    # The current logos are the four just applied.
    for asset in assets:
        assert f'<img src="/branding/{asset.pk}.png"'.encode() in followed.content
    # Change status itself still returns to Parish logos (#196).
    status = browser.get(f"/admin/changes/{request.pk}/").content
    assert f'<a href="{URL}">Return to Parish logos</a>'.encode() in status
    # The one-time review no longer opens once its logos are chosen: the
    # page explains it where the review would be.
    reloaded = browser.get(preview)
    assert reloaded.status_code == 409
    assert "can no longer be reviewed" in region(reloaded.content)
    assert b'name="preview"' not in reloaded.content
    for asset in assets:
        response = browser.get(f"/branding/{asset.pk}.png")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        # The middleware keeps the view's long-lived public caching (#355).
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"
        assert response["Content-Security-Policy"] == FILE_CSP
        assert b"".join(response.streaming_content).startswith(b"\x89PNG")
    assert (
        post(browser, preview, {"action": "confirm", "preview": token})["Location"]
        == result["Location"]
    )
    page = browser.get(URL)
    assert b'rel="icon"' in page.content and b'<img src="/branding/' in page.content
    # The header draws the 150 x 80 menu logo 4rem (64 pixels) tall, at its
    # own shape, and says so before the file arrives (#1062).
    menu = BrandingAsset.objects.get(label="menu")
    assert (menu.width, menu.height) == (150, 80)
    assert (
        f'<img src="/branding/{menu.pk}.png" alt="" width="120" height="64">'
    ).encode() in page.content
    assert (
        len(list((media / "branding" / BrandingBundle.objects.get().pk.hex).iterdir()))
        == 4
    )


def test_other_login_cannot_preview_an_unapplied_upload(auth_service, google, media):
    """Even the same Google account does not adopt the previous session's staging."""
    browser, _ = signed_in()
    preview = stage(browser, auth_service)
    asset = BrandingAsset.objects.first()
    another, _ = signed_in()
    elsewhere = another.get(preview)
    assert elsewhere.status_code == 404
    assert "can no longer be reviewed" in region(elsewhere.content)
    assert another.get(f"{URL}assets/{asset.pk}.png").status_code == 404
    assert another.get(f"/branding/{asset.pk}.png").status_code == 404


@pytest.mark.parametrize("missing", [True, False])
def test_preview_refuses_a_changed_yaml_base(
    auth_service, google, media, monkeypatch, missing
):
    """A selection race cannot sign a preview against a different verified snapshot."""
    from parishkit.stewardship.accounts import branding_views

    browser, _ = signed_in()
    preview = stage(browser, auth_service)
    original = branding_views.editable_configuration

    def changed_after_verification(service):
        """Change only the next external read, after the coherent SQL check.

        The race is one read: the page that then explains the refusal in
        its review region (#750) reads the store as it really is.
        """
        result = original(service)
        active = service.store.active

        def raced():
            """The changed (or missing) version, once."""
            monkeypatch.setattr(service.store, "active", active)
            return None if missing else SimpleNamespace(digest="f" * 64)

        monkeypatch.setattr(service.store, "active", raced)
        return result

    monkeypatch.setattr(
        branding_views, "editable_configuration", changed_after_verification
    )
    with task_login(ServiceRole.WEB):
        response = browser.get(preview)
    assert response.status_code == 409
    assert "can no longer be reviewed" in region(response.content)
    assert b'name="preview"' not in response.content
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "kind", ["missing", "signature", "hidden", "repeated", "csrf", "stale"]
)
def test_bad_uploads_never_create_durable_bundles(auth_service, google, media, kind):
    """Invalid bodies and stale configuration are refused before image persistence."""
    browser, _ = signed_in()
    data = {"base_digest": auth_service.store.active().digest, "logo": upload()}
    if kind == "missing":
        data.pop("logo")
    elif kind == "signature":
        data["logo"] = SimpleUploadedFile("private.html", b"<script>private</script>")
    elif kind == "hidden":
        data["path"] = "/private/directory"
    elif kind == "repeated":
        data["logo"] = [upload(), upload()]
    elif kind == "stale":
        data["base_digest"] = "f" * 64
    response = browser.post(URL, data) if kind == "csrf" else post(browser, URL, data)
    assert response.status_code == (
        403 if kind == "csrf" else 409 if kind == "stale" else 400
    )
    assert b"<script>private" not in response.content
    # Explained in place, in the review region (#750).
    if kind == "stale":
        assert "This page changed in another tab or session." in region(
            response.content
        )
    elif kind == "signature":
        assert 'href="#id_logo"' in region(response.content)
    assert not BrandingBundle.objects.exists()
    assert not BrandingAsset.objects.exists()


def test_a_refused_apply_is_explained_in_place(auth_service, google, media):
    """Logos staged against settings that changed since are refused when
    applied, in the review region (#750); Apply needs a staged address."""
    browser, _ = signed_in()
    store = auth_service.store
    with task_login(ServiceRole.WEB):
        preview = stage(browser, auth_service)
        token = preview_token(browser.get(preview))
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
    with task_login(ServiceRole.WEB):
        refused = post(browser, preview, {"action": "confirm", "preview": token})
        assert refused.status_code == 409
        assert "Upload the logo again." in region(refused.content)
        # The form and the current logos are redrawn at the current version,
        # though Apply's answer otherwise takes only the review region, so
        # the next upload is accepted.
        body = refused.content.decode()
        assert 'id="branding-upload" data-in-place-region data-in-place-always' in body
        assert "data-in-place-region data-in-place-always aria-labelledby" in body
        assert f'name="base_digest" value="{store.active().digest}"' in body
        # Reloading the review explains it in place too.
        reloaded = browser.get(preview)
        assert reloaded.status_code == 409
        assert "can no longer be reviewed" in region(reloaded.content)
        assert stage(browser, auth_service).startswith(f"{URL}?review=")
        # Apply posts only to the address naming its staged logos.
        for elsewhere in (URL, f"{URL}?review={uuid4()}"):
            assert (
                post(
                    browser, elsewhere, {"action": "confirm", "preview": token}
                ).status_code
                == 400
            )
        assert browser.get(f"{URL}?review=x").status_code == 400
        assert browser.get(f"{URL}?review={uuid4()}&request=x").status_code == 400
    assert not ConfigurationChangeRequest.objects.filter(
        patch__0__section="parish", patch__0__values__has_key="branding"
    ).exists()


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_upload_or_list_staging(auth_service, google, media, role):
    """A guessed form URL does not grant public-asset configuration authority."""
    change(
        auth_service.store,
        auth_service.store.active(),
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
    assert browser.get(URL).status_code == 403
    assert (
        post(
            browser,
            URL,
            {"base_digest": auth_service.store.active().digest, "logo": upload()},
        ).status_code
        == 403
    )
    assert not BrandingBundle.objects.exists()


def test_failed_write_leaves_recoverable_nonpublic_staging(
    auth_service, google, media, monkeypatch
):
    """A failed external operation never fabricates ready or selected branding."""
    from parishkit.config import ConfigError

    browser, _ = signed_in()

    def fail(*args):
        raise ConfigError("synthetic file failure")

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.branding_staging.create_bundle", fail
    )
    response = post(
        browser,
        URL,
        {"base_digest": auth_service.store.active().digest, "logo": upload()},
    )
    assert response.status_code == 503
    row = BrandingBundle.objects.get()
    assert row.state == "writing" and not row.assets.exists()
    cleanup_branding(media, row.pk, admit=lambda row: True)
    cleanup_branding(media, row.pk, admit=lambda row: True)
    row.refresh_from_db()
    assert row.state == "scrubbed"


def test_cleanup_crash_after_file_removal_can_resume(auth_service, google, media):
    """An incomplete terminal checkpoint does not require the deleted files to exist."""
    browser, _ = signed_in()
    stage(browser, auth_service)
    row = BrandingBundle.objects.get()
    calls = []

    def interrupted(row):
        calls.append(row.pk)
        return len(calls) == 1

    with pytest.raises(PermissionError):
        cleanup_branding(media, row.pk, admit=interrupted)
    row.refresh_from_db()
    assert row.state == "cleanup_pending"
    assert not (media / "branding" / row.pk.hex).exists()
    cleanup_branding(media, row.pk, admit=lambda row: True)
    row.refresh_from_db()
    assert row.state == "scrubbed"


def png(width, height, color="blue"):
    """A posted PNG of the given size."""
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color).save(stream, format="PNG")
    return SimpleUploadedFile("size.png", stream.getvalue(), content_type="image/png")


def apply_staged(browser, service, preview):
    """Apply the staged logos at ``preview`` and install the change, each
    under its own database role."""
    with task_login(ServiceRole.WEB):
        token = preview_token(browser.get(preview))
        result = post(browser, preview, {"action": "confirm", "preview": token})
    assert result.status_code == 302, result.content
    request = UUID(result["Location"].split("#")[0].rsplit("=", 1)[1])
    with task_login(ServiceRole.CONFIG_INSTALLER):
        installed = install_request(
            service.store, request_id=request, correlation_id=uuid4()
        )
    assert installed.state == "applied"


def test_each_size_upload_changes_only_the_sizes_chosen(auth_service, google, media):
    """A favicon uploaded on its own (#1062) replaces only the favicon: the
    other sizes keep the current images, byte for byte, and the review says
    which sizes changed. A size breaking its rule stages nothing."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        preview = stage(browser, auth_service)
    apply_staged(browser, auth_service, preview)
    with task_login(ServiceRole.WEB):
        before = {row.label: row for row in BrandingAsset.objects.all()}
        digest = auth_service.store.active().digest
        refused = post(
            browser,
            URL,
            {
                "base_digest": digest,
                "editor_mode": "advanced",
                "favicon": png(400, 120),
            },
        )
        assert refused.status_code == 400
        assert (
            "Favicon: the image is 400 × 120 pixels; a favicon must be square "
            "(within 10%)."
        ) in unescape(refused.content.decode())
        assert 'href="#id_favicon"' in region(refused.content)
        assert BrandingBundle.objects.count() == 1
        result = post(
            browser,
            URL,
            {
                "base_digest": digest,
                "editor_mode": "advanced",
                "favicon": png(48, 48, "red"),
            },
        )
        assert result.status_code == 302, result.content
        review = browser.get(result["Location"].split("#")[0])
        shown = unescape(region(review.content))
        assert "Favicon — 32 × 32 pixels — Changed" in shown
        for title in ("Large logo", "Menu logo", "Icon"):
            assert re.search(rf"{title} — [0-9,]+ × [0-9,]+ pixels — Unchanged", shown)
    staged = BrandingBundle.objects.exclude(pk=before["large"].bundle_id).get()
    after = {row.label: row for row in staged.assets.all()}
    for label in ("large", "menu", "icon"):
        assert after[label].sha256 == before[label].sha256
    assert after["favicon"].sha256 != before["favicon"].sha256
    # A stored size whose file can no longer be read counts as missing:
    # refused at its field (no large logo to generate it from), not a 503.
    (media / "branding" / before["menu"].bundle_id.hex / "menu.png").unlink()
    with task_login(ServiceRole.WEB):
        missing = post(
            browser,
            URL,
            {"base_digest": digest, "editor_mode": "advanced", "favicon": png(32, 32)},
        )
    assert missing.status_code == 400
    assert "Menu logo: there is no current image to keep." in unescape(
        missing.content.decode()
    )


def test_each_size_upload_without_stored_logos_generates_from_large(
    auth_service, google, media
):
    """With no current logo images stored, an empty size cannot be kept: it
    is generated from the large logo uploaded with it, or refused at the
    first such size."""
    browser, _ = signed_in()
    digest = auth_service.store.active().digest
    with task_login(ServiceRole.WEB):
        refused = post(
            browser,
            URL,
            {"base_digest": digest, "editor_mode": "advanced", "favicon": png(32, 32)},
        )
        assert refused.status_code == 400
        assert (
            "Large logo: there is no current image to keep. Choose an image for "
            "it, or upload a large logo to generate it from."
        ) in unescape(refused.content.decode())
        assert not BrandingBundle.objects.exists()
        result = post(
            browser,
            URL,
            {
                "base_digest": digest,
                "editor_mode": "advanced",
                "large": png(600, 300),
                "favicon": png(32, 32, "red"),
            },
        )
        assert result.status_code == 302, result.content
    sizes = {
        row.label: (row.width, row.height)
        for row in BrandingAsset.objects.filter(bundle=BrandingBundle.objects.get())
    }
    assert sizes == {
        "large": (600, 300),
        "menu": (256, 128),
        "icon": (128, 64),
        "favicon": (32, 32),
    }


def test_each_size_upload_and_apply_are_refused_after_settings_change(
    auth_service, google, media, monkeypatch
):
    """A per-size upload (#1062) from a page whose settings changed
    elsewhere gets the usual changed-elsewhere refusal before the current
    logos are read, and Apply of a per-size bundle staged before such a
    change is refused in place, as for one image."""
    browser, _ = signed_in()
    store = auth_service.store
    with task_login(ServiceRole.WEB):
        preview = stage(browser, auth_service)
    apply_staged(browser, auth_service, preview)
    digest = store.active().digest
    with task_login(ServiceRole.WEB):
        result = post(
            browser,
            URL,
            {
                "base_digest": digest,
                "editor_mode": "advanced",
                "favicon": png(32, 32, "red"),
            },
        )
        assert result.status_code == 302, result.content
        staged = result["Location"].split("#")[0]
        token = preview_token(browser.get(staged))
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
    bundles = BrandingBundle.objects.count()

    def unread(*args):
        raise AssertionError("current logos read for a stale page")

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.branding_views.complete_bundle", unread
    )
    with task_login(ServiceRole.WEB):
        stale = post(
            browser,
            URL,
            {"base_digest": digest, "editor_mode": "advanced", "favicon": png(32, 32)},
        )
        assert stale.status_code == 409
        assert "This page changed in another tab or session." in region(stale.content)
        assert BrandingBundle.objects.count() == bundles
        refused = post(browser, staged, {"action": "confirm", "preview": token})
        assert refused.status_code == 409
        assert "Upload the logo again." in region(refused.content)


def opened(page):
    """The mode Parish logos is drawn in, and whether Basic can show the
    current logos (the editor's data-stored-basic)."""
    text = page.content.decode()
    checked = re.search(
        r'value="(basic|advanced)" data-editor-mode-choice checked', text
    )
    stored = re.search(r'data-stored-basic="(true|false)"', text)
    return checked.group(1), stored.group(1)


def test_the_page_opens_in_the_mode_that_saved_the_logos(auth_service, google, media):
    """Each staged bundle records its mode (#1062): one image is Basic, sizes
    uploaded separately are Advanced. The page opens in the mode that saved
    the current logos, and only Basic logos have a Basic equivalent."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert opened(browser.get(URL)) == ("basic", "true")
        preview = stage(browser, auth_service)
    assert BrandingBundle.objects.get().mode == "basic"
    apply_staged(browser, auth_service, preview)
    with task_login(ServiceRole.WEB):
        assert opened(browser.get(URL)) == ("basic", "true")
        digest = auth_service.store.active().digest
        result = post(
            browser,
            URL,
            {"base_digest": digest, "editor_mode": "advanced", "favicon": png(32, 32)},
        )
        assert result.status_code == 302, result.content
        staged = result["Location"].split("#")[0]
        # Staged, not applied: the current logos still came from one image.
        assert opened(browser.get(URL)) == ("basic", "true")
    assert BrandingBundle.objects.filter(mode="advanced").count() == 1
    apply_staged(browser, auth_service, staged)
    with task_login(ServiceRole.WEB):
        assert opened(browser.get(URL)) == ("advanced", "false")
        # A refused upload is drawn in the mode it was sent from.
        refused = post(browser, URL, {"base_digest": digest, "editor_mode": "basic"})
        assert refused.status_code == 400
        assert opened(refused) == ("basic", "false")
        # A mode the page never sends is refused outright.
        crafted = post(browser, URL, {"base_digest": digest, "editor_mode": "each"})
        assert crafted.status_code == 400
        # The mode field of the page before the toggle (#1062 slices 1-2)
        # is refused like any other unknown field (#1178).
        for values in (
            {"mode": "one", "logo": upload()},
            {"mode": "each", "favicon": png(32, 32)},
            {"mode": "each", "editor_mode": "advanced", "favicon": png(32, 32)},
        ):
            refused = post(browser, URL, {"base_digest": digest} | values)
            assert refused.status_code == 400
    assert BrandingBundle.objects.count() == 2
