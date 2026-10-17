"""Ministry requests has no summary export or data-load number (#1126).

The campaign-wide summary (now By Ministry, #1133) is counts only, already
on the page, so its
export (with a sensitivity warning and a time zone that meant nothing for
it) was removed, as was the "Technical details" disclosure that held only
the ParishSoft data load number. One Ministry's joining and leaving lists,
and their export, were retired too (#1157): the follow-up queue's Export
these requests replaced them.
"""

from .test_ministry_table_columns import ministry_page

SUMMARY = {
    "duid": 9,
    "name": "Choir",
    "active": True,
    "joining": 2,
    "leaving": 1,
    "unresolved": 1,
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
