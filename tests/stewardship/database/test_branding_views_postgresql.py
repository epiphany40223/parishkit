"""Logo upload/preview/application through real Admin HTTP and durable media files."""

import io
import re
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

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
        assert region(page.content).count("<figure>") == 4
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
    assert b'<img src="/branding/' in followed.content
    # Change status itself still returns to Parish logos (#196).
    status = browser.get(f"/admin/changes/{request.pk}/").content
    assert f'<a href="{URL}">Return to Parish logos</a>'.encode() in status
    # The one-time review no longer opens once its logos are chosen.
    assert browser.get(preview).status_code != 200
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
    assert another.get(preview).status_code == 404
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
        """Change only the subsequent external read, after the coherent SQL check."""
        result = original(service)
        monkeypatch.setattr(
            service.store,
            "active",
            lambda: None if missing else SimpleNamespace(digest="f" * 64),
        )
        return result

    monkeypatch.setattr(
        branding_views, "editable_configuration", changed_after_verification
    )
    with task_login(ServiceRole.WEB):
        response = browser.get(preview)
    assert response.status_code == 409
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
