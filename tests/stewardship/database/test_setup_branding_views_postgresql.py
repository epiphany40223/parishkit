"""Actual wizard logo upload, original-login private PNGs and terminal scrubbing."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from parishkit.stewardship.accounts.branding_models import BrandingAsset, BrandingBundle
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection

from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_views_postgresql import media, upload  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_setup_upload_selects_only_temporary_receipt_and_serves_private_png(request):
    """Actual web media writes never activate a Parish or expose original filenames."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    with web_login():
        browser = started()
        result = post(
            browser, "/admin/setup/branding", {"version": "1", "logo": upload()}
        )
        assert result.status_code == 302, result.content
        assert result["Location"] == "/admin/setup/branding"
        bundle = BrandingBundle.objects.get()
        assert SetupDraftSection.objects.get().values == {"bundle_id": str(bundle.pk)}
        page = browser.get("/admin/setup/branding")
        assert page.status_code == 200
        assert b"private-original.png" not in page.content
        for asset in BrandingAsset.objects.all():
            image = browser.get(f"/admin/setup/branding/assets/{asset.pk}.png")
            assert image.status_code == 200
            assert image["Cache-Control"] == "no-store"
            assert b"".join(image.streaming_content).startswith(b"\x89PNG\r\n\x1a\n")
            assert browser.get(f"/branding/{asset.pk}.png").status_code == 404


def test_new_login_and_cancelled_attempt_cannot_fetch_staged_image(request):
    """Knowing the UUID does not expose another setup's private logo."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    with web_login():
        browser = started()
        post(browser, "/admin/setup/branding", {"version": "1", "logo": upload()})
        asset = BrandingAsset.objects.first()
        url = f"/admin/setup/branding/assets/{asset.pk}.png"
        another, _ = signed_in()
        assert another.get(url).status_code == 403
        attempt = SetupAttempt.objects.get()
        post(browser, "/admin/setup", {"action": "cancel", "attempt": str(attempt.pk)})
        assert browser.get(url).status_code == 403
        assert SetupDraftSection.objects.get().values == {}


def test_setup_logo_rejects_invalid_stale_and_extra_inputs_before_selection(request):
    """A failed upload never substitutes its original bytes or creates a draft ref."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    with web_login():
        browser = started()
        assert browser.get("/admin/setup/branding").status_code == 200
        for values, status in (
            ({"version": "2", "logo": upload()}, 409),
            ({"version": "1", "logo": upload(), "path": "/private"}, 400),
            (
                {
                    "version": "1",
                    "logo": SimpleUploadedFile("invalid.png", b"not-a-png"),
                },
                400,
            ),
        ):
            assert post(browser, "/admin/setup/branding", values).status_code == status
        assert not BrandingBundle.objects.exists()
        assert not SetupDraftSection.objects.exists()
