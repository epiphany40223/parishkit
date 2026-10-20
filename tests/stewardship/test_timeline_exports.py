"""The Family timeline export's file contents (ADM-11 PR 8g).

Pure tests: the document built from a retained capture, the code shown only
when given, instants in the display zone, and the shared renderer's CSV. The
capture, the SQL checks and the worker are in
database/test_timeline_exports_postgresql.py.
"""

import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports import export_services
from parishkit.stewardship.reports.timeline_documents import (
    HEADINGS,
    timeline_document,
)
from parishkit.stewardship.reports.timeline_pdf import render_timeline
from parishkit.stewardship.web import dates

FAMILY = "00000000-0000-4000-8000-000000000061"
PAYLOAD = {
    "family": {
        "id": FAMILY,
        "duid": 42,
        "name": "Example",
        "envelope": 7,
        "reach": "Yes",
        "reminder_workgroup": None,
    },
    "mode": "production",
    "as_of": "2054-10-05T14:00:00+00:00",
    "summary": {
        "submissions": 2,
        "first_submitted_at": "2054-10-01T13:05:00+00:00",
        "last_submitted_at": "2054-10-02T13:05:00+00:00",
        "last_email": {
            "name": "Submission receipt",
            "at": "2054-10-02T13:06:00+00:00",
            "outcome": "Delivered",
        },
        "furthest_step": "Census",
        "furthest_at": "2054-10-01T13:01:00+00:00",
        "last_seen_at": "2054-10-02T13:05:00+00:00",
    },
    "events": [
        {"at": "2054-10-01T13:00:00+00:00", "what": "Signed in", "detail": ""},
        {
            "at": "2054-10-01T13:05:00+00:00",
            "what": "Submitted a response",
            "detail": "No receipt: no email address",
        },
    ],
}
REQUESTED = datetime(2054, 10, 5, 15, 0, tzinfo=UTC)


def document(code="ABCD-EFGH", timezone="America/New_York"):
    """The file's document for the sample capture."""
    return timeline_document(
        PAYLOAD,
        code=code,
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone=timezone,
    )


def test_the_document_holds_the_family_and_every_line():
    """Metadata names the Family and the code; one row per timeline line."""
    built = document()
    metadata = dict(built.metadata)
    assert metadata["Family"] == "Example" and metadata["Family DUID"] == "42"
    assert metadata["Family code"] == "ABCD-EFGH"
    assert metadata["Mode"] == "Production"
    assert metadata["Timeline lines"] == "2, oldest first"
    # The page's Summary panel, in the display zone.
    assert metadata["Submitted"] == (
        "Yes, October 1, 2054 at 9:05 AM EDT · 2 times; most recently "
        "October 2, 2054 at 9:05 AM EDT"
    )
    assert metadata["Last email"] == (
        "Submission receipt, October 2, 2054 at 9:06 AM EDT: Delivered"
    )
    assert metadata["Furthest step reached"] == (
        "Census, October 1, 2054 at 9:01 AM EDT"
    )
    assert metadata["Last seen"] != "Never"
    assert built.item_count == 2 and built.headings == HEADINGS
    when, what, detail = built.rows[1]
    assert what == "Submitted a response" and detail == "No receipt: no email address"
    # Instants are shown in the display zone (13:05 UTC is 09:05 in New York).
    assert when.hour == 9 and when.utcoffset() is not None


def test_a_family_the_data_no_longer_has_gets_its_status_detail():
    """No name, and "Not in ParishSoft" as a ParishSoft status detail right
    after Family; a current Family has no such detail (#1216)."""
    labels = [label for label, _ in document().metadata]
    assert "ParishSoft status" not in labels
    built = timeline_document(
        PAYLOAD | {"family": PAYLOAD["family"] | {"name": None}},
        code=None,
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone="America/New_York",
    )
    labels = [label for label, _ in built.metadata]
    metadata = dict(built.metadata)
    assert metadata["Family"] == ""
    assert metadata["ParishSoft status"] == "Not in ParishSoft"
    assert labels.index("ParishSoft status") == labels.index("Family") + 1


def test_the_code_is_left_out_unless_given():
    """A requester who may not see codes gets none, and the file says so;
    a Family with no code is described as the page describes it."""
    hidden = timeline_document(
        PAYLOAD,
        code=None,
        code_hidden=True,
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone="UTC",
    )
    assert dict(hidden.metadata)["Family code"] == "Not shown"
    assert dict(document(code=None).metadata)["Family code"] == (
        "Unavailable for this campaign"
    )


def test_an_unzoned_instant_is_refused():
    """A stored instant must carry its offset."""
    payload = PAYLOAD | {"as_of": "2054-10-05T14:00:00"}
    with pytest.raises(ValueError):
        timeline_document(
            payload,
            code=None,
            parish_name="Sample Parish",
            requested_at=REQUESTED,
            timezone="UTC",
        )


def test_the_shared_renderer_writes_its_csv():
    """The CSV is exactly the timeline table; the details are in XLSX and PDF.

    Every format carries the same columns (#966), so the shared renderer
    writes the report information (the Family, its code and the summary)
    to the XLSX "Report information" sheet and the PDF's summary, never
    as CSV columns or rows.
    """
    output = io.BytesIO()
    render_timeline(document(), output, format="csv")
    text = output.getvalue().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["When", "What happened", "Details"]
    assert len(rows) == 3 and rows[2][1] == "Submitted a response"
    assert "ABCD-EFGH" not in text
    book = io.BytesIO()
    render_timeline(document(), book, format="xlsx")
    sheets = load_workbook(io.BytesIO(book.getvalue())).worksheets
    values = {cell for sheet in sheets for row in sheet.values for cell in row}
    assert "ABCD-EFGH" in values


# The page and its Timeline tab's panel (#1678), which holds the Summary and
# the table; read in that order, the page's own.
TEMPLATES = Path(__file__).resolve().parents[2] / (
    "src/parishkit/stewardship/accounts/templates/stewardship"
)


def test_the_file_follows_its_page():
    """The table's columns and the details' lines are the page's, in its order.

    UX conventions, "Downloaded files": the same columns, names and order as
    the page. The page's Timeline table heads its columns When, What happened
    and Details; its identity line and Summary panel become the report
    information, in the page's order, beside lines only a file needs.
    """
    text = "".join(
        (TEMPLATES / name).read_text(encoding="utf-8")
        for name in ("timeline.html", "timeline-panel.html")
    )
    head = text[text.index('id="events-heading"') :]
    head = head[head.index("<thead>") : head.index("</thead>")]
    assert tuple(re.findall(r'translate "([^"]+)"', head)) == HEADINGS
    page_lines = re.findall(r'<dt>{% translate "([^"]+)" %}</dt>', text)
    built = timeline_document(
        PAYLOAD
        | {"family": PAYLOAD["family"] | {"reminder_workgroup": "No reminders"}},
        code="ABCD-EFGH",
        parish_name="Sample Parish",
        requested_at=REQUESTED,
        timezone="UTC",
    )
    labels = [label for label, _ in built.metadata]
    identity = ["Family", "Family DUID", "Envelope number"]
    assert [label for label in labels if label in page_lines] == page_lines
    assert labels[2:5] == identity
    # The identity line, then the mode and Counted at, then the Summary.
    assert labels.index("Counted at") < labels.index(page_lines[0])
    assert dict(built.metadata)["Reminders"].endswith("“No reminders”")
    assert "Reminders" not in dict(document().metadata)


def test_summary_times_follow_the_pinned_parish_format():
    """Times inside the Summary's lines use the style the export pinned.

    The worker loads the document inside ``dates.using`` (the requesting
    configuration's parish format), so a combined line never shows Python's
    ISO text with microseconds and offsets.
    """
    with dates.using("iso"):
        metadata = dict(document().metadata)
    assert metadata["Submitted"] == (
        "Yes, 2054-10-01 09:05 EDT · 2 times; most recently 2054-10-02 09:05 EDT"
    )
    assert (
        metadata["Last email"] == "Submission receipt, 2054-10-02 09:06 EDT: Delivered"
    )
    assert metadata["Furthest step reached"] == "Census, 2054-10-01 09:01 EDT"
    single = PAYLOAD | {"summary": PAYLOAD["summary"] | {"submissions": 1}}
    with dates.using("iso"):
        built = timeline_document(
            single,
            code=None,
            parish_name="Sample Parish",
            requested_at=REQUESTED,
            timezone="America/New_York",
        )
    assert dict(built.metadata)["Submitted"] == "Yes, 2054-10-01 09:05 EDT"


def test_the_pdf_holds_the_family_code(monkeypatch):
    """The PDF carries the Family's details and code (#926 slice 2).

    The CSV is the table alone; the PDF's summary cards are the report
    information, so the code an Administrator may see is in the PDF too.
    The sheet itself is tested in ``test_timeline_pdf``.
    """
    from .test_file_design import frames

    pages = frames(
        monkeypatch, lambda output: render_timeline(document(), output, format="pdf")
    )
    assert "ABCD-EFGH" in pages[0]
    assert any("Family DUID 42" in value for value in pages[0])


REQUESTER = "00000000-0000-4000-8000-000000000071"
OTHER = "00000000-0000-4000-8000-000000000072"


@pytest.mark.parametrize(
    ("roles", "requester", "allowed"),
    [
        (frozenset({"administrator", "staff", "ministry_leader"}), REQUESTER, True),
        (frozenset({"staff", "ministry_leader"}), REQUESTER, True),
        (frozenset({"ministry_leader"}), REQUESTER, False),
        # Another requester's export: an Administrator's only, as for every
        # shared export.
        (frozenset({"administrator", "staff", "ministry_leader"}), OTHER, True),
        (frozenset({"staff", "ministry_leader"}), OTHER, False),
    ],
)
def test_administrators_and_staff_may_have_a_timeline_export(
    monkeypatch, roles, requester, allowed
):
    """The service's check mirrors migration 0144's SQL (#1799): Staff may
    have their own timeline export; a Ministry leader never may."""
    from uuid import UUID

    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports.export_models import ExportRequest

    led = frozenset({9}) if "ministry_leader" in roles else frozenset()
    monkeypatch.setattr(
        export_services,
        "current_principal",
        lambda store, user_id: Principal(user_id, roles, led),
    )
    export = ExportRequest(report="family_timeline", requester_id=UUID(requester))
    if allowed:
        assert export_services.authorize(None, UUID(REQUESTER), request=export)
    else:
        with pytest.raises(PermissionError):
            export_services.authorize(None, UUID(REQUESTER), request=export)
