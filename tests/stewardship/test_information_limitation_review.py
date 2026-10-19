"""Pure rules of the Additional information and Limitations decisions (#1685)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.information import flag_decision, staff_ignored
from parishkit.stewardship.responses.limitation_review import (
    Answer,
    answer_given,
    answer_id,
    parse_answer,
)


def revision(decision):
    """A latest revision carrying only its review decision."""
    return SimpleNamespace(review_decision=decision)


@pytest.mark.parametrize(
    ("latest", "ignored"),
    [
        (None, False),
        (revision(None), True),
        (revision("ignored"), True),
        (revision("none"), False),
        (revision("accepted"), False),
    ],
)
def test_staff_ignored_reads_old_revisions_by_their_flags(latest, ignored):
    """No revision is To review; a pre-0134 one left not needed is Ignored."""
    assert staff_ignored(latest) is ignored


def item(needed=False, done=False):
    """A request with its follow-up flags."""
    return SimpleNamespace(
        follow_up_needed=needed, followed_up_at=object() if done else None
    )


@pytest.mark.parametrize(
    ("before", "latest", "flags", "decision"),
    [
        # Needed or completed is Accepted, whatever the request was.
        (item(), None, (True, False), "accepted"),
        (item(), revision("ignored"), (False, True), "accepted"),
        # A notes-only Save keeps To review or Ignored as it was.
        (item(), None, (False, False), "none"),
        (item(), revision("none"), (False, False), "none"),
        (item(), revision("ignored"), (False, False), "ignored"),
        (item(), revision(None), (False, False), "ignored"),
        # Clearing To handle or Handled sends it back to review.
        (item(needed=True), revision("accepted"), (False, False), "none"),
        (item(True, True), revision("accepted"), (False, False), "none"),
    ],
)
def test_flag_decision_never_ignores_on_its_own(before, latest, flags, decision):
    """pk-admin's follow-up flags map onto the review decision."""
    needed, done = flags
    assert (
        flag_decision(before, latest, follow_up_needed=needed, followed_up=done)
        == decision
    )


def test_answer_ids_round_trip_and_fit_the_worklist():
    """Each kind's item id parses back to its answer and stays short."""
    proposed = str(uuid4())
    for answer in (
        Answer(123456789012345678, "member", "987654321098765432"),
        Answer(42, "proposed_member", proposed),
        Answer(42, "family"),
    ):
        text = answer.id
        assert parse_answer(text) == answer and len(text) <= 64
    assert answer_id(42, "family") == "f42"
    assert answer_id(42, "member", "3") == "m42-3"


@pytest.mark.parametrize(
    "text",
    ["", "f0", "f42-3", "m42", "m42-03", "p42-3", "x42", "f42 ", "m42-3-4", None],
)
def test_malformed_answer_ids_are_refused(text):
    """Only the three id shapes the page draws are accepted."""
    with pytest.raises(ValueError):
        parse_answer(text)


def test_answer_given_reads_each_kind():
    """The Family's and each Member's answer, as SQL's answer check reads them."""
    proposed = str(uuid4())
    answers = {
        "cannot_attend": True,
        "service": {
            "members": {"3": {"cannot_serve": True}, "4": {"cannot_serve": False}},
            "proposed_members": {proposed: {"cannot_serve": True}},
        },
    }
    assert answer_given(answers, "family", "")
    assert answer_given(answers, "member", "3")
    assert not answer_given(answers, "member", "4")
    assert not answer_given(answers, "member", "5")
    assert answer_given(answers, "proposed_member", proposed)
    assert not answer_given({}, "family", "")
    assert not answer_given({"service": None}, "member", "3")
