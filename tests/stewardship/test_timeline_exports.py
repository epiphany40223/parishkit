"""The Family timeline export's file contents (ADM-11 PR 8g).

Pure tests: the document built from a retained capture, the code shown only
when given, instants in the display zone, and the shared renderer's CSV. The
capture, the SQL checks and the worker are in
database/test_timeline_exports_postgresql.py.
"""

import csv
import io
from datetime import UTC, datetime

import pytest

from parishkit.stewardship.reports.information_rendering import render_information
from parishkit.stewardship.reports.timeline_documents import (
    HEADINGS,
    timeline_document,
)

FAMILY = "00000000-0000-4000-8000-000000000061"
PAYLOAD = {
    "family": {
        "id": FAMILY,
        "duid": 42,
        "name": "Example",
        "envelope": 7,
        "reach": "Yes",
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
    assert metadata["Family"] == "Example" and metadata["ParishSoft DUID"] == "42"
    assert metadata["Family code"] == "ABCD-EFGH"
    assert metadata["Mode"] == "Production"
    assert metadata["Timeline lines"] == "2, oldest first"
    # The page's Summary panel, in the display zone.
    assert (
        metadata["Submitted"].startswith("Yes, ") and "2 times" in metadata["Submitted"]
    )
    assert metadata["Last email"].startswith("Submission receipt, ")
    assert metadata["Last email"].endswith(": Delivered")
    assert metadata["Furthest step reached"].startswith("Census, ")
    assert metadata["Last seen"] != "Never"
    assert built.item_count == 2 and built.headings == HEADINGS
    when, what, detail = built.rows[1]
    assert what == "Submitted a response" and detail == "No receipt: no email address"
    # Instants are shown in the display zone (13:05 UTC is 09:05 in New York).
    assert when.hour == 9 and when.utcoffset() is not None


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
    """The CSV has the metadata, the headings and each line."""
    output = io.BytesIO()
    render_information(document(), output, format="csv")
    text = output.getvalue().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    # The shared renderer repeats the metadata as columns beside each row.
    assert rows[0][:3] == ["When", "What happened", "Details"]
    assert any("Submitted a response" in row for row in rows)
    assert any("ABCD-EFGH" in row for row in rows)
