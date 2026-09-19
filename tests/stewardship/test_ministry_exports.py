"""Fast exact-output and current-scope tests without database bootstrap."""

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.information_rendering import (
    information_lines,
    render_information,
)
from parishkit.stewardship.reports.ministry_documents import ministry_document
from parishkit.stewardship.reports.ministry_exports import scope_authorized


def document(*, action="join", count=52):
    """Closed payload mirrors SQL capture, with hostile values and hidden contacts."""
    moment = datetime(2026, 9, 19, 15, tzinfo=UTC)
    row = dict(
        member_name="=Example Member",
        member_duid=12345,
        proposed_id=None,
        ministry_duid=9,
        submitted_at=moment.isoformat(),
        state="new",
        outcome=None,
        current_role="Volunteer",
        gender="Unspecified",
        age=66,
        email_visibility="not_published",
        phone_visibility="not_published",
        emails=[{"value": "hidden@example.org"}],
        phones={"home": "hidden-phone"},
        address={"primaryAddress1": "1 Example Street"},
    )
    summary = dict(
        name="Example Ministry",
        duid=9,
        active=False,
        joining=3,
        leaving=1,
        unresolved=1,
        completed=3,
        requests=4,
    )
    payload = dict(
        total=count,
        rows=[row] * count,
        summaries=[summary] * (count if action == "summary" else 1),
        metadata=dict(
            name="Campaign",
            id="campaign",
            source_id="source",
            source_generation=1234,
            source_as_of=moment.isoformat(),
            observed_at=moment.isoformat(),
            report_date="2026-09-19",
            timezone="America/Detroit",
        ),
    )
    return ministry_document(
        payload,
        {"action": action, "filters": {}},
        parish_name="Sample Parish",
        requested_at=moment,
        timezone="America/Detroit",
    )


@pytest.mark.parametrize("action", ["summary", "join", "leave"])
def test_complete_columns_and_csv_privacy(action):
    """Every row survives export; hidden contacts and birth dates never become cells."""
    report = document(action=action)
    assert report.item_count == len(report.rows) == 52
    output = io.BytesIO()
    render_information(report, output, format="csv")
    rows = list(csv.reader(io.StringIO(output.getvalue().decode())))
    assert len(rows) == 54
    assert "hidden" not in output.getvalue().decode()
    assert "Birth date" not in report.headings
    assert dict(report.metadata)["Requested at"] == "2026-09-19T11:00:00-04:00"
    if action == "join":
        assert report.sheet_name == "Ministry requests"
        assert rows[-1][0] == "'=Example Member"
        assert "Not published" in report.rows[0]
    elif action == "leave":
        assert report.sheet_name == "Ministry requests"
        assert "Email" not in report.headings and "Phones" not in report.headings
        assert report.rows[0][-1] == "Volunteer"
    else:
        assert report.rows[0][-1] == "3 out of 4 (75%)"
        assert report.sheet_name == "Ministry summary"


@pytest.mark.parametrize("format", ["xlsx", "pdf"])
def test_rendered_ministry_formats_use_same_private_document(format):
    """Spreadsheet literals and complete PDF text preserve the narrowed projection."""
    report = document(count=1)
    output = io.BytesIO()
    render_information(report, output, format=format)
    if format == "xlsx":
        book = load_workbook(output)
        sheet = book[report.sheet_name]
        assert sheet["A2"].value == "=Example Member" and sheet["A2"].data_type == "s"
        assert "hidden" not in str(list(sheet.values))
        book.close()
    else:
        assert output.getvalue().startswith(b"%PDF")
        text = "\n".join(information_lines(report))
        assert "Not published" in text and "hidden" not in text


@pytest.mark.parametrize("format", ["csv", "xlsx", "pdf"])
def test_empty_capture_still_has_provenance(format):
    """No-result searches still generate a labeled, private report."""
    report = document(count=0)
    output = io.BytesIO()
    render_information(report, output, format=format)
    assert output.getvalue() and dict(report.metadata)["Matching results"] == "0"


@pytest.mark.parametrize(
    "roles,assigned,operational,scope,expected",
    [
        (("staff",), (), True, (4, 9), True),
        (("ministry_leader",), (9,), False, (9,), True),
        (("ministry_leader",), (9,), False, (4, 9), False),
        (("ministry_leader",), (9,), True, (9,), False),
        (("ministry_leader",), (9,), False, (), False),
        ((), (9,), False, (9,), False),
    ],
)
def test_retained_scope_requires_all_current_assignments(
    roles, assigned, operational, scope, expected
):
    """Losing captured scope denies the file, without partial-download fallback."""
    actor = Principal(uuid4(), frozenset(roles), frozenset(assigned))
    assert (
        scope_authorized(actor, {"operational": operational, "ministries": list(scope)})
        is expected
    )
