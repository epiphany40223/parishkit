"""Pure rules for changing a live campaign's Ministries (#342)."""

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.campaigns.live_ministries import live_change_admitted
from parishkit.stewardship.reports.ministry_documents import activity


@pytest.mark.parametrize(
    ("state", "locked", "admitted"),
    [
        ("scheduled", True, True),
        ("active", True, True),
        # An unlocked draft edits everything on Campaign settings instead.
        ("draft", False, False),
        ("closed", True, False),
        ("archived", True, False),
    ],
)
def test_only_open_locked_campaigns_take_the_exemption(state, locked, admitted):
    """Removal and addition both stop once the campaign closes."""
    assert live_change_admitted(state, locked=locked) is admitted


def test_audit_context_accepts_the_change_lists():
    """Each list is sorted, distinct positive DUIDs, like ministry_duids."""
    context = {
        "previous_ministry_duids": [4, 9],
        "ministry_duids": [4],
        "added_ministry_duids": [],
        "removed_ministry_duids": [9],
    }
    assert sanitize(ContextKind.ACTION, context) == context
    for bad in ([9, 4], [4, 4], [0], ["4"]):
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, {"removed_ministry_duids": bad})


@pytest.mark.parametrize(
    ("item", "label"),
    [
        ({"active": True}, "Active"),
        ({"active": True, "in_campaign": True}, "Active"),
        (
            {"active": False, "in_campaign": False},
            "Inactive; no longer in this campaign",
        ),
        (
            {"active": None, "in_campaign": False},
            "Unavailable; no longer in this campaign",
        ),
    ],
)
def test_summary_export_marks_removed_ministries(item, label):
    """Captures from before the flag existed have no in_campaign key."""
    assert activity(item) == label
