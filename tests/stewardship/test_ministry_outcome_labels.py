"""Ministry request outcome labels by request kind (#1132)."""

import re
from pathlib import Path

import pytest

from parishkit.stewardship.reports import ministries, ministry_followup
from parishkit.stewardship.reports.ministries import outcome_choices, outcome_label
from parishkit.stewardship.workflows.followup import outcomes_for
from parishkit.stewardship.workflows.models import RESOLVED_OUTCOMES

SOURCE = Path(__file__).resolve().parents[2] / "src" / "parishkit" / "stewardship"
# Every stored outcome code, resolved or closed.
CODES = (*RESOLVED_OUTCOMES, "no_response")
EXPECTED = {
    "join": {
        "joined": "Joined the Ministry",
        "leave_confirmed": "Left the Ministry",
        "declined": "Declined / no longer interested",
        "duplicate": "Duplicate request",
        "other": "Other",
        "no_response": "No response",
    },
    "leave": {
        "joined": "Joined the Ministry",
        "leave_confirmed": "Left the Ministry",
        "declined": "Stayed in the Ministry",
        "duplicate": "Duplicate request",
        "other": "Other",
        "no_response": "No response",
    },
    "any": {
        "joined": "Joined the Ministry",
        "leave_confirmed": "Left the Ministry",
        "declined": "Declined / stayed",
        "duplicate": "Duplicate request",
        "other": "Other",
        "no_response": "No response",
    },
}


@pytest.mark.parametrize("action", sorted(EXPECTED))
@pytest.mark.parametrize("outcome", CODES)
def test_every_outcome_has_its_kinds_label(outcome, action):
    """Every (outcome, kind) pair has exactly the specified label."""
    assert outcome_label(outcome, action) == EXPECTED[action][outcome]


def test_unrecorded_outcome_uses_the_default():
    """No outcome (an open request) reads blank, or the caller's default."""
    assert outcome_label(None, "leave") == ""
    assert outcome_label(None, "join", "Not yet recorded") == "Not yet recorded"


@pytest.mark.parametrize("action", ["join", "leave", "any"])
def test_filter_choices_cover_every_code_in_order(action):
    """The Outcome filter offers every stored code, labelled for the kind."""
    assert outcome_choices(action) == {
        code: EXPECTED[action][code]
        for code in ("joined", "leave_confirmed", "declined")
        + ("no_response", "duplicate", "other")
    }


def test_each_kind_offers_its_own_pair():
    """A join offers Joined/Declined; a leave offers Left/Stayed, never mixed."""
    join = {outcome_label(code, "join") for code in outcomes_for("join")}
    leave = {outcome_label(code, "leave") for code in outcomes_for("leave")}
    assert {"Joined the Ministry", "Declined / no longer interested"} <= join
    assert not {"Left the Ministry", "Stayed in the Ministry"} & join
    assert {"Left the Ministry", "Stayed in the Ministry"} <= leave
    assert not {"Joined the Ministry", "Declined / no longer interested"} & leave


def test_no_direct_outcome_label_lookups():
    """Labels come only from outcome_label/outcome_choices: neither Ministry
    report module exposes an outcome dict to index, and no Ministry module
    indexes the private label tables, so no screen can drop the request kind."""
    assert not hasattr(ministries, "OUTCOMES")
    assert not hasattr(ministry_followup, "OUTCOMES")
    offenders = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in SOURCE.rglob("*.py")
        if path.name != "ministries.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\b_(JOIN|ANY)?_?LABELS\b", line) and "ministries" in line
    ]
    assert offenders == []
