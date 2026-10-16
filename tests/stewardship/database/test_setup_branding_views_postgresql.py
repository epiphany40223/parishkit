"""Actual wizard logo upload, original-login private PNGs and terminal scrubbing."""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from parishkit.stewardship.accounts.branding_models import BrandingAsset, BrandingBundle
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES

from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_views_postgresql import browser_post, media, upload  # noqa: F401
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
        assert result["Location"] == "/admin/setup/access"
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


def test_setup_logo_left_empty_is_not_refused_as_an_extra_field(request):
    """A browser sends the logo input left empty (Save and continue with no
    file chosen) as a part with an empty filename, which Django keeps as an
    empty POST value (#1396). With no logo saved it is refused on the field,
    not as an unexpected field; with one saved the step continues and keeps
    it."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    url = "/admin/setup/branding"

    def version():
        """The step's current draft version, as its page carries it."""
        page = browser.get(url).content.decode()
        return re.search(r'name="version" value="(\d+)"', page).group(1)

    with web_login():
        browser = started()
        empty = [("logo", "", b"")]
        refused = browser_post(browser, {"version": version()}, empty, url=url)
        assert refused.status_code == 400
        assert refused["Content-Type"].startswith("text/html")
        assert 'aria-invalid="true"' in refused.content.decode()
        assert not BrandingBundle.objects.exists()
        saved = post(browser, url, {"version": version(), "logo": upload()})
        assert saved.status_code == 302, saved.content
        kept = browser_post(browser, {"version": version()}, empty, url=url)
        assert kept.status_code == 302, kept.content
        assert kept["Location"] == "/admin/setup/access"
        assert BrandingBundle.objects.count() == 1


def test_setup_logo_over_the_limit_is_refused_at_its_field(request):
    """A hand-built upload over 5 MB, which the page would never send, is
    still refused (400), at the logo field, in the words the page uses for
    it, and stages nothing (#1332 item 29)."""
    request.getfixturevalue("setup_http")
    request.getfixturevalue("google")
    request.getfixturevalue("media")
    with web_login():
        browser = started()
        large = SimpleUploadedFile(
            "large.png", b"\0" * (MAX_IMAGE_BYTES + 1), content_type="image/png"
        )
        result = post(browser, "/admin/setup/branding", {"version": "1", "logo": large})
        assert result.status_code == 400
        html = result.content.decode()
        (line,) = re.findall(
            r'<ul class="errorlist" id="id_logo_error">(.*?)</ul>', html
        )
        assert "Parish logo: the file is " in line
        assert "the limit is 5 MB." in line
        assert not BrandingBundle.objects.exists()
        assert not SetupDraftSection.objects.exists()
