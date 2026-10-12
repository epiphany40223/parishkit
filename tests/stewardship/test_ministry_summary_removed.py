"""Ministry requests has no summary export or data-load number (#1126).

The campaign-wide summary is counts only, already on the page, so its
export (with a sensitivity warning and a time zone that meant nothing for
it) was removed, as was the "Technical details" disclosure that held only
the ParishSoft data load number. One Ministry's joining and leaving lists
keep their export: their rows name Members.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.reports import ministry_export_views
from parishkit.stewardship.reports.export_models import MinistryExportSnapshot
from parishkit.stewardship.reports.ministries import MinistryQuery
from parishkit.stewardship.reports.ministry_exports import create_ministry_export

from .test_ministry_table_columns import ministry_page, request_row

MOMENT = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)
SUMMARY = {
    "duid": 9,
    "name": "Choir",
    "active": True,
    "joining": 2,
    "leaving": 1,
    "unresolved": 1,
    "progress": "2 out of 3 (67%)",
}


def test_summary_has_no_export_and_no_technical_details():
    """The summary page keeps its table and the Follow-up packet only."""
    page = ministry_page([], summaries=[SUMMARY])
    assert "Follow-up packet" in page and "Queue follow-up packet" in page
    for removed in (
        "Export complete results",
        "Queue complete export",
        "Export timezone",
        'id="table-export"',
        "Technical details",
        "ParishSoft data load",
    ):
        assert removed not in page, removed
    assert "ParishSoft data as of" in page


def test_a_ministry_list_keeps_its_export():
    """One Ministry's list (joining here) still offers its complete export."""
    page = ministry_page([request_row()], ministry_id=9, summaries=[SUMMARY])
    assert '<input type="hidden" name="action" value="join">' in page
    assert "Export complete results" in page and "Queue complete export" in page
    assert "Sensitive parish information" in page and "Export timezone" in page
    assert '<input type="hidden" name="ministry" value="9">' in page
    assert "Technical details" not in page and "ParishSoft data load" not in page
    assert "Follow-up packet" not in page


@pytest.mark.parametrize(
    "selection",
    [{"action": "summary", "ministry": ""}, {"action": "summary", "ministry": "9"}],
)
def test_a_summary_export_request_is_refused(monkeypatch, selection):
    """A stale form or crafted POST for the summary is an invalid selection."""
    monkeypatch.setattr(ministry_export_views, "runtime", Mock())
    monkeypatch.setattr(
        ministry_export_views,
        "_principal",
        lambda request, store: SimpleNamespace(identity=uuid4()),
    )
    values = MinistryQuery().form_values() | selection
    values |= {"format": "csv", "browser_timezone": "UTC"}
    values["request_key"] = str(uuid4())
    response = ministry_export_views.create(RequestFactory().post("/", values), uuid4())
    assert response.status_code == 400
    assert response.content == b"Invalid Ministry export selection.\n"


def test_an_old_summary_capture_is_not_regenerated():
    """An expired summary export made before the removal is not made again."""
    campaign = uuid4()
    snapshot = MinistryExportSnapshot(
        campaign_id=campaign,
        parameters={"action": "summary", "ministry": None, "filters": {}},
    )
    with pytest.raises(ValueError, match="summary export was removed"):
        create_ministry_export(
            Mock(),
            uuid4(),
            campaign_id=campaign,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
            snapshot=snapshot,
        )


@pytest.mark.parametrize("removed", [True, False])
def test_status_page_says_why_a_summary_cannot_be_regenerated(removed):
    """An expired summary's Regenerate is unavailable, with the reason."""
    page = render_to_string(
        "stewardship/report-export.html",
        {
            "job": SimpleNamespace(
                pk=uuid4(),
                report="ministry",
                format="csv",
                requester_id=uuid4(),
                parameters={"action": "summary"},
                ministry_snapshot_id=uuid4(),
                ministry_snapshot=SimpleNamespace(row_count=2, created_at=MOMENT),
                ministry_source_generation=1,
            ),
            "status": {"state": "expired"},
            "mutable": True,
            "retry_key": uuid4(),
            "rehearsal_ended": False,
            "summary_removed": removed,
        },
    )
    assert ("data-regenerate-unavailable" in page) is removed
    assert ("Ministry summary export was removed" in page) is removed
