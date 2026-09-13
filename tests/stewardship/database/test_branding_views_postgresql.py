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

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/configuration/branding"


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
        url, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def stage(browser, service):
    """Upload once and follow the owner-only preview route actually returned."""
    result = post(
        browser, URL, {"base_digest": service.store.active().digest, "logo": upload()}
    )
    assert result.status_code == 302, result.content
    return result["Location"]


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
        assert browser.get(URL).status_code == 200
        preview = stage(browser, auth_service)
        page = browser.get(preview)
        token = preview_token(page)
        assert b"private-original" not in page.content
        assets = list(BrandingAsset.objects.order_by("label"))
        for asset in assets:
            response = browser.get(f"{URL}/assets/{asset.pk}.png")
            assert response.status_code == 200
            assert b"".join(response.streaming_content).startswith(b"\x89PNG")
            assert response["Cache-Control"] == "no-store"
            assert browser.get(f"/branding/{asset.pk}.png").status_code == 404
        result = post(browser, preview, {"action": "confirm", "preview": token})
        assert result.status_code == 302, result.content
    assert auth_service.store.active() == old
    request = ConfigurationChangeRequest.objects.get(
        pk=result["Location"].rsplit("/", 1)[-1]
    )
    with task_login(ServiceRole.CONFIG_INSTALLER):
        assert (
            install_request(
                auth_service.store, request_id=request.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )
    for asset in assets:
        response = browser.get(f"/branding/{asset.pk}.png")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
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
    assert another.get(f"{URL}/assets/{asset.pk}.png").status_code == 404
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
    assert not BrandingBundle.objects.exists()
    assert not BrandingAsset.objects.exists()


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
