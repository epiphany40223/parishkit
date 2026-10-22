"""The Past campaigns page (#1018, slice 4), on PostgreSQL.

Every workbook is synthetic (``archive_factory``) and is checked in this
process (the isolated helper has its own tests). The page's Review route
takes a large upload, so the refusals it owes before reading the body are
asserted with a request body that counts its reads: POST only, the
X-CSRFToken header, the role, ``Content-Length`` and one Review at a time.
Review answers the review dialog's card (or its refusal) and Apply, posted
as the dialog posts it (``action=confirm``), redirects to the page (#750).
"""

import re
import threading
from html import unescape
from uuid import UUID, uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from parishkit.stewardship.archive import imports
from parishkit.stewardship.archive.check import check_archive
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.archive_models import (
    CampaignArchive,
    CampaignArchiveFamily,
)

from ..archive_factory import campaign, set_metadata, workbook_bytes
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_campaign_archive_import_postgresql import in_process

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/parish/past-campaigns/"
REVIEW = PAGE + "review/"
APPLY = PAGE + "imports/"
REMOVE = PAGE + "removal/"


@pytest.fixture(autouse=True)
def checked_here(monkeypatch):
    """Check workbooks in this process; no current campaign by default."""
    monkeypatch.setattr(imports, "check_archive", in_process)
    monkeypatch.setattr(imports, "stewardship_year", lambda: None)


class Tripwire:
    """A request body that counts every read and yields nothing."""

    def __init__(self):
        self.reads = 0

    def read(self, *args):
        """Count the read."""
        self.reads += 1
        return b""

    readline = read


def workbook(year=2054, *, note="Synthetic data for tests", adjust=None):
    """A consistent synthetic archive for ``year``; ``note`` varies its bytes."""
    metadata, sheets = campaign()
    metadata = set_metadata(metadata, "campaign_year", year)
    metadata = set_metadata(metadata, "campaign_name", f"Stewardship {year}")
    metadata = set_metadata(metadata, "note.01", note)
    return workbook_bytes(metadata, sheets, adjust=adjust)


def token(browser):
    """The Admin CSRF secret, as the page script sends it in the header."""
    return browser.cookies["pk_admin_csrf"].value


def review(browser, data, *, keep=True, name="stewardship-2054.xlsx"):
    """Post one workbook as the page's Review does: token in the header only."""
    fields = {"file": SimpleUploadedFile(name, data)}
    if keep:
        fields["keep_family_rows"] = "yes"
    return browser.post(REVIEW, fields, HTTP_X_CSRFTOKEN=token(browser))


def post(browser, path, values):
    """An ordinary form post with the page's token."""
    return browser.post(path, values | {"csrfmiddlewaretoken": token(browser)})


def apply(browser, preview, **values):
    """Apply a preview as the review dialog's Apply posts it."""
    return post(browser, APPLY, {"action": "confirm", "preview": preview} | values)


def preview_of(response):
    """The preview id a Review card carries for Apply (the dialog's answer)."""
    assert response.status_code == 200, response.content
    shown = text(response)
    assert "data-review-answer" in shown
    found = re.search(r'name="preview" value="([0-9a-f-]+)" data-review-preview', shown)
    assert found, shown
    return found.group(1)


def text(response):
    """The page's text, unescaped."""
    return unescape(response.content.decode())


def test_review_apply_replace_and_remove_in_the_page(auth_service, google):
    """The whole flow as the web login, with the service's audit and rows."""
    browser, _ = signed_in()
    first = workbook(2054)
    with task_login(ServiceRole.WEB):
        page = browser.get(PAGE)
        assert page.status_code == 200 and page["Cache-Control"] == "no-store"
        assert "No past campaigns are imported yet" in text(page)
        reviewed = review(browser, first, name="C:\\archive\\stewardship-2054.xlsx")
        assert reviewed["Cache-Control"] == "no-store"
        preview = preview_of(reviewed)
        shown = text(reviewed)
        assert 'data-review-title="Add 2054?"' in shown
        assert "stewardship-2054.xlsx" in shown
        assert "Synthetic data for tests" in shown
        assert "Apply adds 2054" in shown
        # Without the dialog's action, Apply is refused and nothing changes.
        assert post(browser, APPLY, {"preview": preview}).status_code == 400
        assert not CampaignArchive.objects.filter(state="active").exists()
        applied = apply(browser, preview)
        assert applied.status_code == 302 and applied["Location"] == PAGE
        assert CampaignArchive.objects.get(state="active").pk == UUID(preview)
        result = text(browser.get(PAGE))
        assert "by admin<wbr>@example.org" in result and "Stewardship 2054" in result
        assert 'id="archive-outcome"' in result
        # The same file again: already imported, so Apply is unavailable.
        again_response = review(browser, first)
        assert again_response.status_code == 200
        again = text(again_response)
        assert "2054 is already imported" in again
        assert "Unavailable: imported by admin@example.org on" in again
        assert "data-review-unavailable" in again
        assert "data-review-preview" not in again
        # A corrected file replaces the year only with the acknowledgement.
        corrected_response = review(browser, workbook(2054, note="Corrected"))
        corrected = preview_of(corrected_response)
        assert "This replaces the 2054 figures already imported" in text(
            corrected_response
        )
        refused = apply(browser, corrected)
        assert refused.status_code == 400
        assert "acknowledge that to apply it" in text(refused)
        assert "data-review-refusal" in text(refused)
        assert "This was not applied." in text(refused)
        replaced = apply(browser, corrected, replace="yes")
        assert replaced.status_code == 302 and replaced["Location"] == PAGE
        assert CampaignArchive.objects.get(state="active").pk == UUID(corrected)
        active = CampaignArchive.objects.get(state="active")
        # Remove: the confirmation dialog's <year>:<version>.
        stale = post(browser, REMOVE, {"archive": f"2054:{active.version - 1}"})
        assert stale.status_code == 409
        removed = post(browser, REMOVE, {"archive": f"2054:{active.version}"})
        assert removed.status_code == 302 and removed["Location"] == PAGE
        after = text(browser.get(PAGE))
    assert not CampaignArchive.objects.filter(state="active").exists()
    assert "No past campaigns are imported yet" in after
    # Technical details list every import with its checksum, tombstones too.
    assert active.checksum in after and "Removed or replaced" in after
    kinds = list(
        AuditEvent.objects.filter(event_type__startswith="campaign_archive_")
        .order_by("created_at")
        .values_list("event_type", flat=True)
    )
    assert kinds.count("campaign_archive_imported") == 2
    assert kinds.count("campaign_archive_replaced") == 1
    assert kinds.count("campaign_archive_removed") == 1


def test_a_refused_file_lists_cells_never_values(auth_service, google):
    """A formula cell is located by sheet and cell; nothing found is repeated."""

    def formula(book):
        """A formula where families!J2 holds a literal."""
        book["families"]["J2"] = "=SECRETFORMULA(1)"

    browser, _ = signed_in()
    response = review(browser, workbook(2053, adjust=formula))
    assert response.status_code == 400
    shown = text(response)
    assert "This file can't be imported" in shown
    assert "families!J2" in shown and "SECRETFORMULA" not in shown
    # The dialog's refusal, not the page: the form keeps its chosen file.
    assert "data-review-refusal" in shown and 'id="archive-upload"' not in shown
    assert not CampaignArchive.objects.exists()
    unreadable = text(review(browser, b"not a workbook"))
    assert "This file can't be imported" in unreadable


def test_keep_family_rows_unticked_keeps_none(auth_service, google):
    """The box is the service's keep_family_rows."""
    browser, _ = signed_in()
    reviewed = review(browser, workbook(2052), keep=False)
    archive = CampaignArchive.objects.get(pk=preview_of(reviewed))
    assert not archive.family_rows_kept
    assert not CampaignArchiveFamily.objects.filter(archive=archive).exists()
    assert "Family rows kept" in text(reviewed)


def test_an_unresolvable_year_makes_review_unavailable(
    auth_service, google, monkeypatch
):
    """The page knows the refusal, so Review is unavailable with its reason."""

    def unresolved():
        """The current campaign's year cannot be resolved."""
        raise imports.refusal("year_unresolved", "cannot be resolved")

    monkeypatch.setattr(imports, "stewardship_year", unresolved)
    browser, _ = signed_in()
    shown = text(browser.get(PAGE))
    assert 'aria-describedby="archive-year-reason"' in shown
    assert "Set the year label in Campaign settings" in shown
    # The server refuses anyway.
    assert review(browser, workbook(2051)).status_code == 400


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_review_takes_post_only(auth_service, google, method):
    """Any other method is refused before the body is read."""
    browser, _ = signed_in()
    body = Tripwire()
    response = getattr(browser, method)(
        REVIEW, HTTP_X_CSRFTOKEN=token(browser), **{"wsgi.input": body}
    )
    assert response.status_code == 405
    assert body.reads == 0


def untouched(browser, *, csrf=True, length="1000"):
    """Post to Review with a body that counts reads; return (response, reads)."""
    body = Tripwire()
    extra = {
        "wsgi.input": body,
        "CONTENT_LENGTH": length,
        "CONTENT_TYPE": "multipart/form-data; boundary=x",
    }
    if csrf:
        extra["HTTP_X_CSRFTOKEN"] = token(browser)
    response = browser.generic("POST", REVIEW, **extra)
    return response, body.reads


def test_refusals_come_before_the_body_is_read(auth_service, google):
    """No header, no or oversize Content-Length: refused with the body unread."""
    browser, _ = signed_in()
    response, reads = untouched(browser, csrf=False)
    assert response.status_code == 403 and reads == 0
    response, reads = untouched(browser, length="")
    assert response.status_code == 400 and reads == 0
    response, reads = untouched(browser, length=str(21 * 1024 * 1024 + 1))
    # Too large is the file field's error: the page again, field marked.
    assert response.status_code == 400 and reads == 0
    shown = text(response)
    assert 'id="archive-file_error"' in shown and 'aria-invalid="true"' in shown
    assert "This file is larger than 20 MB." in shown
    assert "data-error-summary" in shown and "data-review-refusal" not in shown
    # The form's token in the body is not enough: only the header counts.
    response = browser.post(
        REVIEW,
        {
            "file": SimpleUploadedFile("a.xlsx", workbook()),
            "csrfmiddlewaretoken": token(browser),
        },
    )
    assert response.status_code == 403
    assert not CampaignArchive.objects.exists()


def test_a_file_over_the_limit_is_the_file_fields_error(auth_service, google):
    """A file just over 20 MiB inside the request bound: the page again (400)
    with the file field marked, never a dialog refusal; nothing stored."""
    browser, _ = signed_in()
    response = review(browser, b"x" * (20 * 1024 * 1024 + 1))
    assert response.status_code == 400
    shown = text(response)
    assert 'id="archive-file_error"' in shown and 'aria-invalid="true"' in shown
    assert "data-review-refusal" not in shown and "data-review-answer" not in shown
    assert not CampaignArchive.objects.exists()


def test_staff_are_refused_before_the_body_is_read(auth_service, google):
    """Staff hold FINANCIAL_DETAIL, not CONFIGURE: no page, no Review."""
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, _ = signed_in()
    assert browser.get(PAGE).status_code == 403
    response, reads = untouched(browser)
    assert response.status_code == 403 and reads == 0
    assert post(browser, REMOVE, {"archive": "2054:2"}).status_code == 403


def test_one_file_per_request(auth_service, google):
    """Two files, or an unknown field, are refused and nothing is stored."""
    browser, _ = signed_in()
    two = browser.post(
        REVIEW,
        {
            "file": [
                SimpleUploadedFile("a.xlsx", workbook(2050)),
                SimpleUploadedFile("b.xlsx", workbook(2049)),
            ]
        },
        HTTP_X_CSRFTOKEN=token(browser),
    )
    assert two.status_code == 400
    other = browser.post(
        REVIEW,
        {"file": SimpleUploadedFile("a.xlsx", workbook(2050)), "extra": "x"},
        HTTP_X_CSRFTOKEN=token(browser),
    )
    assert other.status_code == 400
    assert not CampaignArchive.objects.exists()


def test_one_review_at_a_time(auth_service, google):
    """While another Review holds the lock, Review is busy at once, unread."""
    browser, _ = signed_in()
    held, release = threading.Event(), threading.Event()

    def other():
        """Another Review's hold, on another connection."""
        try:
            with imports.ReviewLock():
                held.set()
                release.wait(10)
        finally:
            connection.close()

    thread = threading.Thread(target=other)
    thread.start()
    try:
        assert held.wait(10)
        response, reads = untouched(browser)
    finally:
        release.set()
        thread.join(10)
    assert response.status_code == 503 and reads == 0
    assert "Another archive is being checked" in text(response)
    # Once it is released, a Review goes ahead.
    preview_of(review(browser, workbook(2048)))


def test_an_expired_preview_is_refused_in_the_dialog(auth_service, google, monkeypatch):
    """An expired preview's Apply is the dialog's refusal; the page takes no
    query but its sort (no ?preview= or ?applied=, #750)."""
    browser, _ = signed_in()
    preview = preview_of(review(browser, workbook(2047)))
    monkeypatch.setattr(imports, "PREVIEW_SECONDS", 0)
    stale = apply(browser, preview)
    assert stale.status_code == 409
    shown = text(stale)
    assert "data-review-refusal" in shown
    assert "This review expired or was already applied" in shown
    assert CampaignArchive.objects.get(pk=preview).state == "pending"
    for query in ({"preview": preview}, {"applied": preview}, {"other": "1"}):
        assert browser.get(PAGE, query).status_code == 400
    assert browser.get(PAGE, {"sort": "year"}).status_code == 200


def test_a_review_through_the_real_helper(auth_service, google, monkeypatch):
    """The page's Review runs the isolated helper (``python -I -m``) as the
    web process does, and its card shows the helper's figures."""
    monkeypatch.setattr(imports, "check_archive", check_archive)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        reviewed = review(browser, workbook(2046))
        preview = preview_of(reviewed)
        shown = text(reviewed)
    archive = CampaignArchive.objects.get(pk=preview)
    assert archive.state == "pending" and archive.campaign_year == 2046
    assert archive.families_responded == len(campaign()[1]["families"])
    assert 'data-review-title="Add 2046?"' in shown
