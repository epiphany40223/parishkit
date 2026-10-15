"""Ministry requests has no summary export or data-load number (#1126).

The campaign-wide summary (now By Ministry, #1133) is counts only, already
on the page, so its
export (with a sensitivity warning and a time zone that meant nothing for
it) was removed, as was the "Technical details" disclosure that held only
the ParishSoft data load number. One Ministry's joining and leaving lists,
and their export, were retired too (#1157): the follow-up queue's Export
these requests replaced them.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.reports.export_models import MinistryExportSnapshot
from parishkit.stewardship.reports.ministry_exports import create_ministry_export

from .test_ministry_table_columns import ministry_page

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
    """Ministry requests keeps the Follow-up packet and the queue's Export
    these requests (#1133), never the summary's removed counts export or
    its data-load number."""
    page = ministry_page([], summaries=[SUMMARY])
    assert "Follow-up packet" in page and "Queue follow-up packet" in page
    assert "Export these requests" in page
    for removed in (
        "Export complete results",
        "Queue complete export",
        "Technical details",
        "ParishSoft data load",
    ):
        assert removed not in page, removed
    assert "ParishSoft data as of" in page


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
