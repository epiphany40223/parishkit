"""Roster changes to enter: eligibility and the download (#528, step 4)."""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.reports.ministry_roster_views import HEADINGS, roster_csv
from parishkit.stewardship.workflows.roster import eligible


def test_only_person_resolved_roster_changes_take_the_tick():
    """Resolved joins and leaves with no resolution source; nothing else."""
    base = {"state": "resolved", "outcome": "joined", "source_resolved": False}
    assert eligible(base)
    assert eligible(base | {"outcome": "leave_confirmed"})
    assert not eligible(base | {"source_resolved": True})
    assert not eligible(base | {"outcome": "declined"})
    assert not eligible(base | {"state": "superseded"})


def test_download_times_are_in_the_chosen_zone_and_formulas_are_neutral():
    """Instants carry the zone's offset; a formula-like name stays text."""
    when = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)
    row = {
        "member_name": "=HYPERLINK(1)",
        "member_duid": "3",
        "family_duid": 1,
        "ministry_name": "Food pantry",
        "ministry_duid": 9,
        "action": "join",
        "resolved_at": when,
        "resolved_by": "admin@example.org",
        "roster_entered": True,
        "roster_by": "staff@example.org",
        "roster_at": when,
    }
    text = roster_csv([row], ZoneInfo("America/Chicago")).decode()
    table = list(csv.reader(io.StringIO(text)))
    assert tuple(table[0]) == HEADINGS
    line = dict(zip(HEADINGS, table[1], strict=True))
    assert not line["Member"].startswith("=")
    assert line["Resolved"] == "2054-10-05T09:30:00-05:00"
    assert line["Resolved by"] == "admin@example.org"
    assert line["Entered at"] == "2054-10-05T09:30:00-05:00"
