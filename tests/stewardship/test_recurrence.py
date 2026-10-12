"""The server's repeat-rule expansion matches the page script's (#469)."""

import json
from pathlib import Path

import pytest

from parishkit.stewardship.campaigns.recurrence import canonical_rule, expand

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "recurrence_cases.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_shared_fixture(case):
    """Each rule gives exactly the fixture's dates, gaps or refusal."""
    assert expand(case["rule"]) == case["expected"]


def test_canonical_rules_keep_only_what_their_frequency_uses():
    """Stored rules compare equal when they mean the same thing."""
    base = {"start": "2054-10-01", "end": "2054-10-31"}
    assert canonical_rule(
        base | {"frequency": "weekly", "weekdays": [3, 0, 3], "monthly": {"day": 2}}
    ) == base | {"frequency": "weekly", "weekdays": [0, 3]}
    assert canonical_rule(
        base | {"frequency": "monthly", "monthly": {"day": 5}, "weekdays": [[0]]}
    ) == base | {"frequency": "monthly", "monthly": {"day": 5}}
    assert canonical_rule(base | {"frequency": "daily", "weekdays": [1]}) == base | {
        "frequency": "daily"
    }
    for bad in (
        None,
        base | {"frequency": "weekly", "weekdays": [True]},
        base | {"frequency": "weekly", "weekdays": [7]},
        base | {"frequency": "monthly", "monthly": {"day": True}},
        # Ambiguous: a day and a weekday of the month together.
        base | {"frequency": "monthly", "monthly": {"day": 5, "ordinal": 2}},
        # Never hashed, never raised: refused.
        base | {"frequency": "weekly", "weekdays": [[0]]},
        base | {"frequency": "weekly", "weekdays": [{}]},
        base | {"frequency": "weekly", "weekdays": "0"},
        base | {"frequency": "monthly", "monthly": "5"},
        base | {"frequency": "hourly"},
    ):
        assert canonical_rule(bad) is None, bad
