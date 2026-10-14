"""Logo upload, review dialog and application through real Admin HTTP and media."""

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


def stage(browser, service, values=None):
    """Upload once (one image, or ``values``) and return the review dialog's
    fragment of the staged logos (#750), which carries what Apply posts."""
    result = post(
        browser,
        URL,
        {"base_digest": service.store.active().digest}
        | (values if values is not None else {"logo": upload()}),
    )
    carried(result)
    return result


def carried(review):
    """The hidden fields of the review dialog's fragment, which its Apply
    posts: the signed preview and the staged bundle it was signed for."""
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


def confirm_review(browser, review, **changed):
    """Post a review's Apply as the dialog does, to Parish logos, with the
    carried fields (``changed`` replaces some)."""
    return post(browser, URL, {"action": "confirm"} | carried(review) | changed)


def refusal(response):
    """A plain refusal's message, as the review dialog shows it."""
    return response.json()["refusal"]["message"]


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
        review = stage(browser, auth_service)
        # Reviewed in the dialog (#750): every size, and the hidden fields
        # its Apply posts; the step indicator stays at the edit step.
        assert review.content.decode().count("<figure") == 4
        assert b"private-original" not in review.content
        assets = list(BrandingAsset.objects.order_by("label"))
        for asset in assets:
            response = browser.get(f"{URL}assets/{asset.pk}.png")
            assert response.status_code == 200
            assert b"".join(response.streaming_content).startswith(b"\x89PNG")
            assert response["Cache-Control"] == "no-store"
            assert browser.get(f"/branding/{asset.pk}.png").status_code == 404
        result = confirm_review(browser, review)
        assert result.status_code == 302, result.content
    assert auth_service.store.active() == old
    # Apply answers with the change's status, which the dialog follows.
    location = result["Location"]
    assert location.startswith("/admin/changes/")
    request = ConfigurationChangeRequest.objects.get(pk=location.split("/")[3])
    with task_login(ServiceRole.CONFIG_INSTALLER):
        assert (
            install_request(
                auth_service.store, request_id=request.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )
    # The page the dialog redraws from shows the four logos just applied.
    followed = browser.get(URL)
    assert flow_steps(followed.content) == (STEPS, "Make changes")
    for asset in assets:
        assert f'<img src="/branding/{asset.pk}.png"'.encode() in followed.content
    # Change status itself still returns to Parish logos (#196).
    status = browser.get(location).content
    assert f'<a href="{URL}">Return to Parish logos</a>'.encode() in status
    for asset in assets:
        response = browser.get(f"/branding/{asset.pk}.png")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        # The middleware keeps the view's long-lived public caching (#355).
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"
        assert response["Content-Security-Policy"] == FILE_CSP
        assert b"".join(response.streaming_content).startswith(b"\x89PNG")
    # An exact retry of the applied logos answers with the same change.
    assert confirm_review(browser, review)["Location"] == result["Location"]
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
    """Even the same Google account does not adopt the previous session's
    staging: its preview cannot be applied from another sign-in."""
    browser, _ = signed_in()
    review = stage(browser, auth_service)
    asset = BrandingAsset.objects.first()
    another, _ = signed_in()
    elsewhere = confirm_review(another, review)
    assert elsewhere.status_code == 404
    # The dialog says to reload (#750): a retry can never succeed, so it is
    # not "try again in a moment".
    assert "Reload the page, then upload the logo again." in refusal(elsewhere)
    assert not ConfigurationChangeRequest.objects.filter(
        patch__0__section="parish", patch__0__values__has_key="branding"
    ).exists()
    assert another.get(f"{URL}assets/{asset.pk}.png").status_code == 404
    assert another.get(f"/branding/{asset.pk}.png").status_code == 404


def test_an_expired_upload_is_refused_at_apply(
    auth_service, google, media, monkeypatch
):
    """Staged logos past their day (staging's clock moved on) are refused
    when applied, in the dialog's words saying to reload first, and
    nothing is recorded."""
    from datetime import timedelta

    from parishkit.stewardship.accounts import branding_staging
    from parishkit.stewardship.accounts.sessions import database_now

    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        review = stage(browser, auth_service)
    before = ConfigurationChangeRequest.objects.count()
    with monkeypatch.context() as clock, task_login(ServiceRole.WEB):
        later = database_now() + timedelta(days=2)
        clock.setattr(branding_staging, "database_now", lambda: later)
        expired = confirm_review(browser, review)
    assert expired.status_code == 409
    assert refusal(expired).startswith("These logos expired")
    assert "Reload the page, then upload the logo again." in refusal(expired)
    assert ConfigurationChangeRequest.objects.count() == before


@pytest.mark.parametrize("missing", [True, False])
def test_preview_refuses_a_changed_yaml_base(
    auth_service, google, media, monkeypatch, missing
):
    """A selection race cannot sign a preview against a different verified snapshot."""
    from parishkit.stewardship.accounts import branding_views

    browser, _ = signed_in()
    original = branding_views.editable_configuration

    def changed_after_verification(service):
        """Change only the next external read, after the coherent SQL check.

        The race is one read, between staging the upload and signing its
        review, which is then refused in the dialog (#750).
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
        response = post(
            browser,
            URL,
            {"base_digest": auth_service.store.active().digest, "logo": upload()},
        )
    assert response.status_code == 409
    assert "can no longer be reviewed" in refusal(response)
    assert "Reload the page" in refusal(response)
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
    # A stale page is refused in the review dialog; a refused image is the
    # page again, its summary linked to the field (#750).
    if kind == "stale":
        assert "This page changed in another tab or session." in refusal(response)
    elif kind == "signature":
        assert b'href="#id_logo"' in response.content
        assert b"data-review-answer" not in response.content
    assert not BrandingBundle.objects.exists()
    assert not BrandingAsset.objects.exists()


def test_a_refused_apply_is_explained_in_the_dialog(auth_service, google, media):
    """Logos staged against settings that changed since are refused when
    applied, in the dialog's words (reload first, #750); Apply needs the
    staged bundle its preview was signed for, and the page takes no
    query (no legacy review or status addresses)."""
    browser, _ = signed_in()
    store = auth_service.store
    with task_login(ServiceRole.WEB):
        review = stage(browser, auth_service)
        other = stage(browser, auth_service)
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
        refused = confirm_review(browser, review)
        assert refused.status_code == 409
        assert "Reload the page, then upload the logo again." in refusal(refused)
        # A preview posted with another staged bundle, with none, or with
        # a malformed one is refused outright.
        token = carried(review)["preview"]
        for bundle in (carried(other)["bundle"], str(uuid4()), "x"):
            assert confirm_review(browser, review, bundle=bundle).status_code == 400
        assert (
            post(browser, URL, {"action": "confirm", "preview": token}).status_code
            == 400
        )
        # Only Apply has an action: a Review is the upload itself.
        assert post(browser, URL, {"action": "preview"}).status_code == 400
        # No query is taken, so old review and status addresses are refused.
        for query in (f"?review={uuid4()}", "?request=x", "?settled=1"):
            assert browser.get(f"{URL}{query}").status_code == 400
        # After a reload the next upload is accepted.
        stage(browser, auth_service)
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


def apply_staged(browser, service, review):
    """Apply the staged logos ``review`` shows and install the change, each
    under its own database role."""
    with task_login(ServiceRole.WEB):
        result = confirm_review(browser, review)
    assert result.status_code == 302, result.content
    request = UUID(result["Location"].split("/")[3])
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
        assert b'href="#id_favicon"' in refused.content
        assert BrandingBundle.objects.count() == 1
        review = stage(
            browser,
            auth_service,
            {"editor_mode": "advanced", "favicon": png(48, 48, "red")},
        )
        shown = unescape(review.content.decode())
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
        stage(
            browser,
            auth_service,
            {
                "editor_mode": "advanced",
                "large": png(600, 300),
                "favicon": png(32, 32, "red"),
            },
        )
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
    change is refused in the dialog, as for one image."""
    browser, _ = signed_in()
    store = auth_service.store
    with task_login(ServiceRole.WEB):
        preview = stage(browser, auth_service)
    apply_staged(browser, auth_service, preview)
    digest = store.active().digest
    with task_login(ServiceRole.WEB):
        staged = stage(
            browser,
            auth_service,
            {"editor_mode": "advanced", "favicon": png(32, 32, "red")},
        )
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
        assert "This page changed in another tab or session." in refusal(stale)
        assert BrandingBundle.objects.count() == bundles
        refused = confirm_review(browser, staged)
        assert refused.status_code == 409
        assert "Reload the page, then upload the logo again." in refusal(refused)


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
        staged = stage(
            browser, auth_service, {"editor_mode": "advanced", "favicon": png(32, 32)}
        )
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
