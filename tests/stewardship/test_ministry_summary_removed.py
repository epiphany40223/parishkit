"""Ministry requests has no summary export or data-load number (#1126).

The campaign-wide summary (now By Ministry, #1133) is counts only, already
on the page, so its
export (with a sensitivity warning and a time zone that meant nothing for
it) was removed, as was the "Technical details" disclosure that held only
the ParishSoft data load number. One Ministry's joining and leaving lists,
and their export, were retired too (#1157): the follow-up queue's Export
these requests replaced them. The multi-Ministry Follow-up packet was
removed for every role (Administrator decision, #2394), with its address
and the command line's ``export ministry-packet``.
"""

import pytest
from django.urls import NoReverseMatch, reverse

from parishkit.stewardship.admin_cli import COMMANDS

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
    """Ministry requests keeps the queue's Export these requests (#1133),
    never the summary's removed counts export or its data-load number."""
    page = ministry_page([], summaries=[SUMMARY])
    assert "Export these requests" in page
    for removed in (
        "Export complete results",
        "Queue complete export",
        "Technical details",
        "ParishSoft data load",
    ):
        assert removed not in page, removed
    assert "ParishSoft data as of" in page


@pytest.mark.parametrize(
    "roles", [{"administrator"}, {"staff"}, {"ministry_leader"}], ids=sorted
)
def test_no_role_gets_the_follow_up_packet(roles):
    """The Follow-up packet is gone for every role (#2394): no section, no
    Ministry boxes and no packet export, and its address and command line
    command are gone too, with no redirect."""
    page = ministry_page([], summaries=[SUMMARY], roles=frozenset(roles))
    for removed in (
        "Follow-up packet",
        "follow-up packet",
        "ministry-packet",
        'name="ministries"',
        "/admin/reports/ministries/packets/",
    ):
        assert removed not in page, removed
    with pytest.raises(NoReverseMatch):
        reverse("admin:ministry_packet")
    assert "export ministry-packet" not in {spec.name for spec in COMMANDS}
