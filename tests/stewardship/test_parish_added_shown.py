"""The reviewed form's rules for a census change the parish added (#2008).

``responses.reviewed`` draws a standing parish-added census change in its
field from a ``parish`` shown item and treats an answer equal to the added
value as left as shown (review pipeline spec, "What the reviewed form
shows"). These pure rules need no database: which entry a field draws,
whether an answer leaves the added value as shown, and whether ParishSoft
still has the value it had when the change was added.
"""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.pipeline import Shown
from parishkit.stewardship.responses.comparison import ValueKind
from parishkit.stewardship.responses.inputs import FieldInput
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.responses.reviewed import (
    ABSENT,
    PARISH_FIELDS,
    _unchanged_since,
    census_draw,
    census_rows,
    drawn_entries,
    parish_left_as_shown,
)


def entry(shown, item, field="nickname", entity="member", key="3"):
    """One census shown item."""
    return {
        "stream": "census",
        "entity": entity,
        "key": key,
        "field": field,
        "item": item,
        "version": None if shown == Shown.PARISH else 1,
        "decision": None,
        "shown": shown.value,
    }


def nickname(source):
    """Member 3's nickname input with ParishSoft's ``source`` value."""
    return FieldInput("member", 3, "nickname", ValueKind.TEXT, source)


def test_the_parish_entry_wins_its_field_unless_withdrawn():
    """A field's ``parish`` entry is drawn over the Ignored change beside it,
    in either order; a withdrawn addition (absent from the additions read)
    leaves the change entry drawn."""
    ignored = entry(Shown.BASELINE, "change")
    added = entry(Shown.PARISH, "addition")
    other = entry(Shown.FAMILY, "other", field="suffix")
    key = ("member", "3", "nickname")
    for entries in ([ignored, added, other], [added, ignored, other]):
        drawn = drawn_entries(entries, {"addition": object()})
        assert drawn[key] is added
        assert drawn["member", "3", "suffix"] is other
        assert drawn_entries(entries, {})[key] is ignored


@pytest.mark.parametrize(
    "answer,expected", [("Dave", True), ("Davey", False), (ABSENT, False)]
)
def test_an_answer_equal_to_the_added_value_is_left_as_shown(answer, expected):
    """By the field's canonical comparison, as every shown value is."""
    addition = SimpleNamespace(value="Dave")
    assert parish_left_as_shown(entry(Shown.PARISH, "a"), addition, answer) is expected


def test_a_withdrawn_addition_is_never_left_as_shown():
    """``census_additions`` leaves a withdrawn addition out (None here)."""
    assert parish_left_as_shown(entry(Shown.PARISH, "a"), None, "Dave") is False


@pytest.mark.parametrize(
    "recorded,source,expected",
    [
        ({"available": True, "value": "Bob"}, KnownValue(True, "Bob"), True),
        ({"available": True, "value": "Bob"}, KnownValue(True, "Dave"), False),
        ({"available": False, "value": None}, KnownValue(False), True),
        ({"available": False, "value": None}, KnownValue(True, "Dave"), False),
        (None, KnownValue(False), True),
        ({"available": True, "value": "Bob"}, KnownValue(False), False),
    ],
)
def test_an_entered_addition_stands_while_parishsoft_is_unchanged(
    recorded, source, expected
):
    """ParishSoft's value now against the one recorded when it was added."""
    assert _unchanged_since(nickname(source), recorded) is expected


def test_an_added_value_is_drawn_and_marked_where_it_differs():
    """Drawn as the field's value, marked changed unless ParishSoft has it."""
    addition = SimpleNamespace(value="Dave")
    parish = entry(Shown.PARISH, "a")
    drawn = census_draw(nickname(KnownValue(True, "Bob")), parish, addition, None)
    assert (drawn.value.value, drawn.changed, drawn.conflict) == ("Dave", True, False)
    same = census_draw(nickname(KnownValue(True, "Dave")), parish, addition, None)
    assert (same.value.value, same.changed) == ("Dave", False)


def test_the_parish_fields_are_census_adds_but_a_death_date():
    """Every Member ordinary field and both addresses; no request field."""
    assert ("member", "nickname") in PARISH_FIELDS
    assert ("family", "home_address") in PARISH_FIELDS
    assert ("family", "mailing_address") in PARISH_FIELDS
    assert ("member", "death_date") not in PARISH_FIELDS
    assert ("family", "email_opt_out") not in PARISH_FIELDS


def test_census_rows_reads_no_parish_entry(monkeypatch):
    """A ``parish`` entry names an addition, never a change: not looked up."""
    from parishkit.stewardship.responses import reviewed

    asked = []

    class Objects:
        """Records the ids a lookup asked for."""

        @staticmethod
        def in_bulk(ids):
            asked.append(list(ids))
            return {}

    monkeypatch.setattr(reviewed.ProposedChange, "objects", Objects)
    monkeypatch.setattr(reviewed.ProposalReview, "objects", Objects)
    census_rows([entry(Shown.PARISH, "addition"), entry(Shown.FAMILY, "change")])
    assert asked[0] == ["change"]
