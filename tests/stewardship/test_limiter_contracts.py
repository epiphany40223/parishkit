"""No caller can use a nominal counter field to retain raw private values."""

import pytest

from parishkit.stewardship.accounts.limiting import Counter


@pytest.mark.parametrize(
    "name",
    [
        "admin_start",
        "admin_callback",
        "admin_identity_1",
        "admin_identity_99",
        "family_ip",
        "family_pair",
    ],
)
def test_counter_closed_names_include_policy_epoch(name):
    assert Counter(name, "f" * 64, 5, 900).name == name


@pytest.mark.parametrize(
    "values",
    [
        ("private@example.org", "f" * 64, 5, 900),
        ("admin_identity_private", "f" * 64, 5, 900),
        ("admin_identity_0", "f" * 64, 5, 900),
        ("family_pair", "SECRET", 5, 900),
        ("family_pair", "f" * 64, True, 900),
        ("family_pair", "f" * 64, 0, 900),
        ("family_pair", "f" * 64, 10001, 900),
        ("family_pair", "f" * 64, 5, 86401),
    ],
)
def test_counter_bad_inputs_rejected_without_echoing(values):
    with pytest.raises(ValueError, match="counter policy"):
        Counter(*values)
