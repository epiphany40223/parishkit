"""The Family timeline's infographic PDF (#926 slice 2), with synthetic data.

The text drawn on each page is collected (``test_file_design.frames``), so
these tests check what the sheet says: the shared frame and the Family's
line on every page, every Summary line and the code, one tile per kind of
line, and every timeline line's day, time, words and detail.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from parishkit.stewardship.reports import pdf_design, timeline_pdf
from parishkit.stewardship.reports.family_timeline import (
    Email,
    Engagement,
    Submitted,
    email_name,
    events,
)
from parishkit.stewardship.reports.timeline_documents import timeline_document
from parishkit.stewardship.web import dates

from .test_file_design import Recorder, assert_framed, assert_inside, frames
from .test_timeline_exports import PAYLOAD, REQUESTED

START = datetime(2054, 10, 1, 13, 0, tzinfo=UTC)
LINES = (
    ("Invitation", "Delivered"),
    ("Signed in", "or a mail scanner checked the link"),
    ("Opened the form", ""),
    ("Got past the first step", ""),
    ("Reminder 1", "Delivery unknown"),
    ("Reached the furthest step so far", "Census"),
    ("Submitted a response", "No receipt: no email address"),
    ("Reminder not sent", "in the ParishSoft No-reminders WorkGroup"),
)
PRIVACY = "Sensitive parish information. Share only with authorized recipients."


def document(count=8, *, step=timedelta(hours=7), code="ABCD-EFGH", **more):
    """A synthetic Family's timeline with ``count`` lines ``step`` apart."""
    lines = [
        {
            "at": (START + step * index).isoformat(),
            "what": LINES[index % len(LINES)][0],
            "detail": LINES[index % len(LINES)][1],
        }
        for index in range(count)
    ]
    return timeline_document(
        PAYLOAD | {"events": lines} | more,
        code=code,
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone="America/New_York",
    )


def drawn_pages(monkeypatch, report):
    """The text drawn on each page of ``report``'s PDF."""
    return frames(monkeypatch, lambda output: timeline_pdf.timeline_pdf(report, output))


def test_every_page_carries_the_frame_the_family_and_the_columns(monkeypatch):
    """A loose page still says whose timeline it is and what its columns are."""
    report = document(60)
    pages = drawn_pages(monkeypatch, report)
    assert len(pages) > 1
    assert_framed(pages, "Family timeline", PRIVACY)
    identity = "Example · Family DUID 42 · Envelope number 7 · Production"
    for drawn in pages:
        assert identity in drawn
        assert {"When", "What happened", "Details"} <= set(drawn)
        assert any(value.startswith("Requested ") for value in drawn)


def test_every_summary_line_the_code_and_every_timeline_line_are_drawn(
    monkeypatch,
):
    """Nothing in the report information or the table is left out."""
    with dates.using("us_long"):
        report = document()
        pages = drawn_pages(monkeypatch, report)
    drawn = [value for page in pages for value in page]
    text = "\n".join(drawn)
    # Each Summary line, wrapped into its card, rejoins to its value.
    for label in ("Counted at", "Submitted", "Last email", "Last seen"):
        value = pdf_design.safe(dict(report.metadata)[label])
        assert label in drawn
        assert "".join(timeline_pdf.card(label, value).values) == value
        for line in timeline_pdf.card(label, value).values:
            assert line in drawn
    assert "ABCD-EFGH" in drawn and "Family code" in drawn
    assert "Timeline · 8, oldest first" in drawn
    for at, what, detail in report.rows:
        with dates.using("us_long"):
            assert dates.format_date(at.date()) in text
            assert f"{dates.format_time(at)} {at.tzname()}" in drawn
        assert what in drawn
        assert not detail or detail in drawn
    # One tile per kind, with its words and count.
    for label, count in (
        ("Sign-ins and form opens", "2"),
        ("Emails", "2"),
        ("Emails not sent", "1"),
        ("Form progress", "2"),
        ("Submissions", "1"),
    ):
        index = drawn.index(label)
        assert drawn[index - 1] == count, label
    assert "Other lines" not in drawn


@pytest.mark.parametrize(
    ("code", "hidden", "words"),
    [(None, False, "Unavailable for this campaign"), (None, True, "Not shown")],
)
def test_no_code_reads_as_words_without_the_notice(monkeypatch, code, hidden, words):
    """Without a code the card says why, as the page does, and needs no notice."""
    report = timeline_document(
        PAYLOAD,
        code=code,
        code_hidden=hidden,
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone="UTC",
    )
    drawn = [value for page in drawn_pages(monkeypatch, report) for value in page]
    assert words in drawn
    assert not any(value.startswith("Sensitive: this code") for value in drawn)
    card = timeline_pdf.code_card("Family code", "ABCD-EFGH")
    assert card.face == "mono" and card.notice and card.code


def test_a_page_that_starts_mid_day_repeats_the_day_heading(monkeypatch):
    """Lines of one day split across pages keep their day in view."""
    with dates.using("us_long"):
        report = document(60, step=timedelta(minutes=1))
        pages = drawn_pages(monkeypatch, report)
        day = dates.format_date(report.rows[0][0].date())
    assert len(pages) > 1
    assert day in pages[0]
    for drawn in pages[1:]:
        assert f"{day} (continued)" in drawn


def test_an_empty_timeline_says_so(monkeypatch):
    """A Family with no lines still gets its summary and a plain statement."""
    report = document(0)
    pages = drawn_pages(monkeypatch, report)
    assert len(pages) == 1
    assert "No timeline lines." in pages[0] and "When" not in pages[0]


def test_escape_note_appears_only_when_a_value_needs_one(monkeypatch):
    """Characters the fonts cannot draw are escaped, and the note says how."""
    plain = [value for page in drawn_pages(monkeypatch, document()) for value in page]
    assert not any(value.startswith(pdf_design.FORMAT_LABEL) for value in plain)
    odd = document(family=PAYLOAD["family"] | {"name": "Example 中"})
    drawn = [value for page in drawn_pages(monkeypatch, odd) for value in page]
    assert any(value.startswith(pdf_design.FORMAT_LABEL) for value in drawn)
    assert any("Example \\u4e2d" in value for value in drawn)


class MarkerRecorder(Recorder):
    """The shared recorder, also recording markers by their size."""

    def marker(self, x, y, shape, color, size, **kwargs):
        """A marker spans half its size above and below its centre."""
        self.extents.append((y - size / 2, y + size / 2))


@pytest.mark.parametrize("count", [0, 3, 40, 200])
def test_the_body_stays_between_the_header_and_the_footer(count):
    """Cards, tiles, headings and lines never draw past body_bottom."""
    report = document(count, step=timedelta(hours=5))
    frame = timeline_pdf.timeline_frame(report)
    recorder = MarkerRecorder()
    blocks = timeline_pdf.timeline_blocks(report)
    for page in timeline_pdf.timeline_pages(blocks, frame):
        timeline_pdf.draw_page(recorder, page, frame)
    assert_inside(frame, recorder.extents)


def test_every_line_the_page_builds_has_a_known_marker():
    """Each kind of line ``family_timeline.events`` makes maps to a marker.

    A line this module did not know would get the neutral "other" marker;
    this keeps the page's words and the PDF's kinds in step.
    """
    minute = timedelta(minutes=1)
    emails = [
        Email(UUID(int=1), "initial", email_name("initial"), "delivered", START),
        Email(UUID(int=2), "reminder", email_name("reminder", 2), "sent", START),
        Email(UUID(int=3), "reminder", email_name("reminder"), "sent", START),
        Email(UUID(int=4), "receipt", email_name("receipt"), "delivered", START),
        Email(UUID(int=5), "test", email_name("test"), "delivered", START),
    ]
    lines = events(
        emails,
        [Submitted(UUID(int=9), START + minute, by_staff=True, no_receipt=True)],
        skips=[(START, UUID(int=20))],
        workgroup_skips=[(START, UUID(int=21))],
        sign_ins=[START, (START + minute, True)],
        forms=[START],
        engagement=Engagement(START, "census", START + minute, START + minute),
    )
    kinds = {str(line.what): timeline_pdf.kind_of(line.what) for line in lines}
    assert "other" not in kinds.values(), kinds
    assert set(kinds.values()) == set(timeline_pdf.KINDS) - {"other"}
    assert timeline_pdf.kind_of("Something new") == "other"


def test_an_unknown_line_gets_the_neutral_marker_and_its_tile(monkeypatch):
    """A line of words this module does not know still prints, marked "other"."""
    report = document()
    report = type(report)(
        report.metadata,
        (*report.rows, (report.rows[-1][0], "Something new", "")),
        report.item_count + 1,
        report.requested_at,
    )
    drawn = [value for page in drawn_pages(monkeypatch, report) for value in page]
    assert "Something new" in drawn and "Other lines" in drawn
    # Every kind has its own marker shape: never color or fill alone.
    shapes = [kind.shape for kind in timeline_pdf.KINDS.values()]
    assert len(set(shapes)) == len(shapes)


def test_the_export_renders_csv_and_xlsx_through_the_shared_renderer():
    """Only the PDF is the infographic; CSV and XLSX are the shared files."""
    import io

    output = io.BytesIO()
    timeline_pdf.render_timeline(document(), output, format="csv")
    assert output.getvalue().startswith(b"When,What happened,Details")
    output = io.BytesIO()
    timeline_pdf.render_timeline(document(), output, format="xlsx")
    assert output.getvalue().startswith(b"PK")
    with pytest.raises(ValueError):
        timeline_pdf.render_timeline(document(), io.BytesIO(), format="html")
