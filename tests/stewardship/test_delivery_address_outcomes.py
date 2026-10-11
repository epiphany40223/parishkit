"""How the Mail message page joins addresses with their outcomes (#806).

``email_recipients`` reads the addresses (0058) and each routed address's
latest outcome (0068) through two definer reads; these tests replace both
reads with fixed rows, so they need no database.
"""

import pytest

from parishkit.stewardship.jobs import delivery_reads

ADDRESSES = ["first@example.org", "second@example.org"]


def recipients(monkeypatch, rows, outcomes):
    """``email_recipients`` over fixed recipient and outcome rows (None = refused)."""
    answers = iter([rows, outcomes])
    monkeypatch.setattr(delivery_reads, "_definer_rows", lambda *args: next(answers))
    return delivery_reads.email_recipients("message", "actor")


def listed(intended, routed):
    """The recipients read's (kind, address) rows."""
    return [("intended", value) for value in intended] + [
        ("routed", value) for value in routed
    ]


def labels(rows):
    """Each row's label as plain text, or None."""
    return [(address, label and str(label)) for address, label in rows]


def test_production_outcomes_follow_the_to_addresses(monkeypatch):
    """In Production the routed list is the To: list, so it carries them,
    and a partly accepted attempt says how many it reached."""
    result = recipients(
        monkeypatch,
        listed(ADDRESSES, ADDRESSES),
        [(ADDRESSES[0], "delivered"), (ADDRESSES[1], "refused_for_now")],
    )
    assert labels(result["intended_rows"]) == [
        (ADDRESSES[0], "delivered"),
        (ADDRESSES[1], "refused for now"),
    ]
    assert result["reached"] == (1, 2) and result["refused_for_now"]


@pytest.mark.parametrize(
    ("outcomes", "reached"),
    [
        (["delivered", "delivered"], None),
        (["not_delivered", "not_delivered"], None),
        (["refused_permanently", "delivered"], (1, 2)),
        (["refused_for_now", "unknown"], None),
    ],
)
def test_reached_only_when_some_but_not_all_were_delivered(
    monkeypatch, outcomes, reached
):
    """The count line is for an accepted attempt that missed some addresses."""
    result = recipients(
        monkeypatch,
        listed(ADDRESSES, ADDRESSES),
        list(zip(ADDRESSES, outcomes, strict=True)),
    )
    assert result["reached"] == reached
    assert result["refused_for_now"] == ("refused_for_now" in outcomes)


def test_testing_outcomes_follow_the_testing_recipient(monkeypatch):
    """In Testing mode only the routed (Testing) line carries outcomes."""
    result = recipients(
        monkeypatch,
        listed(ADDRESSES, ["testing@example.org"]),
        [("testing@example.org", "delivered")],
    )
    assert labels(result["intended_rows"]) == [(a, None) for a in ADDRESSES]
    assert labels(result["routed_rows"]) == [("testing@example.org", "delivered")]


def test_outcomes_for_another_list_are_not_shown(monkeypatch):
    """A rendering made between the two reads must not mislabel an address,
    and no outcome (before the first attempt) shows none."""
    for outcomes in ([("other@example.org", "delivered")], []):
        result = recipients(monkeypatch, listed(ADDRESSES, ADDRESSES), outcomes)
        assert labels(result["intended_rows"]) == [(a, None) for a in ADDRESSES]
        assert result["reached"] is None and not result["refused_for_now"]


def test_a_refused_read_returns_none(monkeypatch):
    """Either read refused leaves the page to its final authority recheck."""
    assert recipients(monkeypatch, None, None) is None
    assert recipients(monkeypatch, listed(ADDRESSES, ADDRESSES), None) is None
