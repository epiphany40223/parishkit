"""Past campaigns (#1018, slice 4): the page's pure parts, without a database.

The header-only CSRF check (``csrf_header_only``) must refuse or admit an
upload without reading its body; the Review card's notes are flagged as the
helper flags them; and the review dialog's card follows the prerequisite
gates (a replacement holds Apply for its acknowledgement, a file already
imported offers no Apply).
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.middleware.csrf import _get_new_csrf_string, _mask_cipher_secret
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts.sessions import (
    NamespacedCsrfMiddleware,
    csrf_header_only,
)
from parishkit.stewardship.reports import past_campaign_views as views

REVIEW = "/admin/parish/past-campaigns/review/"


class Tripwire:
    """A request body that counts every read and yields nothing."""

    def __init__(self):
        self.reads = 0

    def read(self, *args):
        """Count the read."""
        self.reads += 1
        return b""

    readline = read


def upload_request(header=None):
    """A multipart POST to the Review route whose body counts its reads."""
    secret = _get_new_csrf_string()
    factory = RequestFactory()
    factory.cookies["pk_admin_csrf"] = secret
    extra = {} if header is None else {"HTTP_X_CSRFTOKEN": header(secret)}
    request = factory.post(
        REVIEW,
        data=b"--x\r\n",
        content_type="multipart/form-data; boundary=x",
        **extra,
    )
    request._stream = Tripwire()
    return request


def check(request, view):
    """The CSRF middleware's answer for ``view``: None admits it."""
    middleware = NamespacedCsrfMiddleware(lambda request: None)
    middleware.process_request(request)
    return middleware.process_view(request, view, (), {})


def marked(request):
    """A view marked to take its token from the header only."""
    raise AssertionError("never called")


csrf_header_only(marked)


def plain(request):
    """An ordinary view: Django looks for the form's token first."""
    raise AssertionError("never called")


@pytest.mark.parametrize(
    ("header", "admitted"),
    [
        (None, False),
        (lambda secret: "x" * 32, False),
        (lambda secret: _mask_cipher_secret(_get_new_csrf_string()), False),
        (lambda secret: _mask_cipher_secret(secret), True),
        (lambda secret: secret, True),
    ],
    ids=["missing", "malformed", "wrong", "masked", "unmasked"],
)
def test_the_header_check_never_reads_the_body(header, admitted):
    """Only the X-CSRFToken header counts; the body is never read either way."""
    request = upload_request(header)
    answer = check(request, marked)
    assert (answer is None) is admitted
    if not admitted:
        assert answer.status_code == 403
    assert request._stream.reads == 0


def test_an_ordinary_view_reads_the_form_first():
    """Without the mark Django reads the body for the form's token: the reason
    the upload route needs the mark at all."""
    request = upload_request(lambda secret: secret)
    assert check(request, plain) is None
    assert request._stream.reads > 0


def test_the_route_bounds_match_the_ingress():
    """21 MiB for the request: the 20 MiB file plus form overhead."""
    assert views.MAX_REVIEW_REQUEST == 21 * 1024 * 1024
    assert views.MAX_ARCHIVE_BYTES == 20 * 1024 * 1024
    assert views.REMOVE_VALUE.fullmatch("2025:3")
    for value in ("2025", "25:3", "2025:-1", "2025:3:4", "2025:x"):
        assert not views.REMOVE_VALUE.fullmatch(value)


def archive(**fields):
    """A pending archive row as the card reads it."""
    values = {
        "pk": uuid4(),
        "campaign_year": 2025,
        "campaign_name": "Stewardship 2025",
        "file_name": "stewardship-2025.xlsx",
        "is_final": True,
        "family_rows_kept": True,
        "active_families_provenance": "Families with a code in 2024",
        "source_system": "Old system",
        "dedup_rule": "Last submission per Family",
        "notes": {"note.01": "Converted by hand", "count.active_now": "1450"},
        "imported_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
    }
    return SimpleNamespace(**(values | fields))


def test_notes_are_flagged_as_the_helper_flags_them():
    """A long note, a dollar amount or five digits in a row is flagged; a kept
    count is listed but never flagged, since no person wrote it."""
    row = archive(
        dedup_rule="x" * 501,
        notes={
            "note.01": "Pledges over $5 were rounded",
            "note.02": "Envelope 12345 was merged",
            "note.03": "Plain words",
            "count.active_now": "123456",
        },
    )
    flags = {label: flagged for label, _, flagged in views.note_rows(row)}
    assert flags == {
        "Campaign Families counted from": False,
        "Source system": False,
        "Repeat submissions": True,
        "count.active_now": False,
        "note.01": True,
        "note.02": True,
        "note.03": False,
    }


def card(action, **fields):
    """The review dialog's answer with one Review card."""
    pending = archive()
    current = archive(pk=uuid4(), campaign_name="Stewardship 2025 (old)")
    values = {
        "archive": pending,
        "action": action,
        "current": None if action == "add" else current,
        "current_by": "admin@example.org",
        "replace_blocked": False,
        "day_1": "September 14, 2024",
        "last_day": "November 30, 2024",
        "families": "812",
        "pledged": "$1,234,567.00",
        "active": "1,480",
        "notes": views.note_rows(pending),
        "family_rows": "790",
        "expires_at": datetime(2026, 10, 11, 12, 15, tzinfo=UTC),
    }
    return render_to_string(
        "stewardship/past-campaigns-review.html",
        {"card": values | fields, "problems": []},
    )


def test_a_replacement_waits_for_its_acknowledgement():
    """Replace shows the acknowledgement box, a field of the dialog's Apply form
    that holds Apply until it is ticked; Add has none. Both carry the preview
    id Apply posts and name the dialog's question and status line."""
    replace = card("replace")
    assert 'data-review-title="Replace 2025?"' in replace
    assert 'data-review-done="Replaced 2025."' in replace
    assert (
        'name="replace" value="yes" form="archive-review-dialog-apply" required'
        " data-review-require" in replace
    )
    assert "This replaces the 2025 figures already imported" in replace
    added = card("add")
    assert 'data-review-title="Add 2025?"' in added
    assert 'data-review-done="Imported 2025."' in added
    assert 'name="replace"' not in added
    for text in (replace, added):
        assert text.count("data-review-preview") == 1
        assert "data-review-answer" in text and "data-review-of" not in text


def test_a_file_already_imported_has_nothing_to_apply():
    """No preview: the card's reason, marked data-review-unavailable, says who
    imported it and when, so the dialog shows Apply greyed with it
    (Administrator decision 12); its title is a statement, not a question."""
    text = card("already")
    assert 'data-review-title="2025 is already imported"' in text
    assert (
        "<p data-review-unavailable>Unavailable: imported by admin@example.org on"
        in text
    )
    assert "Apply would change nothing." not in text
    assert "data-review-preview" not in text and 'name="preview"' not in text
    blocked = card("replace", replace_blocked=True)
    assert 'data-review-title="2025 has a final import"' in blocked
    assert "Replace 2025?" not in blocked and "Apply replaces" not in blocked
    assert (
        "<p data-review-unavailable>Unavailable: a partial file can't replace the"
        " final 2025 import.</p>" in blocked
    )
    assert 'name="replace"' not in blocked and "data-review-preview" not in blocked
    for answer in (card("add"), card("replace")):
        assert "data-review-unavailable" not in answer


def test_problems_name_places_never_values():
    """A refused file lists each problem's place and rule, and says how many
    more there are."""
    text = render_to_string(
        "stewardship/past-campaigns-review.html",
        {
            "card": None,
            "problems": [
                {
                    "code": "money",
                    "place": "families!J17",
                    "rule": "annual_pledge must be greater than zero",
                },
                {"code": "year_unresolved", "place": "", "rule": "set the year"},
            ],
            "more_problems": 3,
        },
    )
    assert "<code>families!J17</code>: annual_pledge must be greater" in text
    assert "Set the year" in text and 'href="/admin/campaign/settings/"' in text
    assert "3 more problems are not shown." in text
    assert "data-review-refusal" in text and "This file can't be imported:" in text
    assert "data-review-answer" not in text


def test_the_table_dates_are_compact():
    """Day 1 uses the parish format's compact variant, as dense tables do."""
    assert views._compact(date(2024, 9, 14)) != ""
