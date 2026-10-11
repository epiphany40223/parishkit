"""The Past campaigns page (#1018, slice 4), on PostgreSQL.

Every workbook is synthetic (``archive_factory``) and is checked in this
process (the isolated helper has its own tests). The page's Review route
takes a large upload, so the refusals it owes before reading the body are
asserted with a request body that counts its reads: POST only, the
X-CSRFToken header, the role, ``Content-Length`` and one Review at a time.
"""

import threading
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

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


def query(response):
    """The redirect's query values."""
    assert response.status_code == 302, response.content
    return {
        key: value[0]
        for key, value in parse_qs(urlsplit(response["Location"]).query).items()
    }


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
        assert reviewed["Location"].endswith("#archive-review")
        preview = query(reviewed)["preview"]
        card = browser.get(PAGE, {"preview": preview})
        shown = text(card)
        assert "Add 2054" in shown and "stewardship-2054.xlsx" in shown
        assert "Synthetic data for tests" in shown
        assert "Apply adds 2054" in shown
        applied = post(browser, APPLY, {"preview": preview})
        assert query(applied) == {
            "applied": str(CampaignArchive.objects.get(state="active").pk),
            "outcome": "imported",
        }
        result = text(browser.get(applied["Location"].split("#")[0]))
        assert "Imported 2054" in result
        assert "by admin<wbr>@example.org" in result and "Stewardship 2054" in result
        # The same file again: already imported, so Apply has nothing to do.
        again = text(browser.get(PAGE, query(review(browser, first))))
        assert "2054 is already imported" in again
        assert "Unavailable: this file is already imported." in again
        # A corrected file replaces the year only with the acknowledgement.
        corrected = query(review(browser, workbook(2054, note="Corrected")))
        assert "This replaces the 2054 figures already imported" in text(
            browser.get(PAGE, corrected)
        )
        refused = post(browser, APPLY, {"preview": corrected["preview"]})
        assert refused.status_code == 400
        assert "acknowledge that to apply it" in text(refused)
        assert 'data-review-of="archive-upload"' in text(refused)
        replaced = post(
            browser, APPLY, {"preview": corrected["preview"], "replace": "yes"}
        )
        assert query(replaced)["outcome"] == "replaced"
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
    assert "data-error-summary" in shown
    assert not CampaignArchive.objects.exists()
    unreadable = text(review(browser, b"not a workbook"))
    assert "This file can't be imported" in unreadable


def test_keep_family_rows_unticked_keeps_none(auth_service, google):
    """The box is the service's keep_family_rows."""
    browser, _ = signed_in()
    preview = query(review(browser, workbook(2052), keep=False))["preview"]
    archive = CampaignArchive.objects.get(pk=preview)
    assert not archive.family_rows_kept
    assert not CampaignArchiveFamily.objects.filter(archive=archive).exists()
    assert "Family rows kept" in text(browser.get(PAGE, {"preview": preview}))


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
    assert response.status_code == 413 and reads == 0
    assert "The file is larger than 20 MB" in text(response)
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
    assert review(browser, workbook(2048)).status_code == 302


def test_an_expired_or_foreign_preview_shows_no_card(auth_service, google, monkeypatch):
    """The card is only the reviewer's own, unexpired preview."""
    browser, _ = signed_in()
    preview = query(review(browser, workbook(2047)))["preview"]
    monkeypatch.setattr(imports, "PREVIEW_SECONDS", 0)
    shown = text(browser.get(PAGE, {"preview": preview}))
    assert "Add 2047" not in shown
    assert "This review expired or was already applied" in shown
    stale = post(browser, APPLY, {"preview": preview})
    assert stale.status_code == 409
    assert browser.get(PAGE, {"preview": "not-a-uuid"}).status_code == 400
    assert browser.get(PAGE, {"other": "1"}).status_code == 400


def test_a_review_through_the_real_helper(auth_service, google, monkeypatch):
    """The page's Review runs the isolated helper (``python -I -m``) as the
    web process does, and its card shows the helper's figures."""
    monkeypatch.setattr(imports, "check_archive", check_archive)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        preview = query(review(browser, workbook(2046)))["preview"]
        shown = text(browser.get(PAGE, {"preview": preview}))
    archive = CampaignArchive.objects.get(pk=preview)
    assert archive.state == "pending" and archive.campaign_year == 2046
    assert archive.families_responded == len(campaign()[1]["families"])
    assert "Add 2046" in shown
