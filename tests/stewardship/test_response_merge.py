"""Family/source/Admin merge branches and private-value separation."""

from dataclasses import asdict

import pytest

from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS, ValueKind
from parishkit.stewardship.responses.merge import (
    KnownValue,
    MergeState,
    PriorChange,
    merge_value,
)


@pytest.mark.parametrize(
    "baseline,submitted,current,admin,state,display,changed,conflict",
    [
        ("old", "family", "old", None, "family_changed", "family", True, False),
        ("old", "family", "family", None, "upstream_caught_up", "family", False, False),
        ("old", "family", "other", None, "conflict", "family", True, True),
        ("old", "family", "edited", "edited", "admin_resolved", "edited", False, False),
        ("old", "family", "other", "edited", "conflict", "family", True, True),
        ("old", "family", "old", "edited", "family_changed", "family", True, False),
        (
            "old",
            "family",
            "family",
            "edited",
            "upstream_caught_up",
            "family",
            False,
            False,
        ),
        ("old", "old", "other", None, "source_changed", "other", False, False),
        ("old", "old", "old", None, "current", "old", False, False),
        (None, "family", None, None, "family_changed", "family", True, False),
        ("old", None, None, None, "upstream_caught_up", None, False, False),
        ("old", None, "other", None, "conflict", None, True, True),
    ],
)
def test_three_way_branch(
    baseline, submitted, current, admin, state, display, changed, conflict
):
    """A terminal Admin correction is never attributed to a Family submission."""
    prior = PriorChange(
        KnownValue(True, baseline), submitted, KnownValue(admin is not None, admin)
    )
    result = merge_value(ValueKind.TEXT, KnownValue(True, current), prior)
    assert result.state == state
    assert result.value == KnownValue(True, display)
    assert result.changed is changed
    assert result.conflict is conflict
    assert prior.submitted == submitted


@pytest.mark.parametrize(
    "current", [KnownValue(True, None), KnownValue(True, "source"), KnownValue(False)]
)
def test_no_prior_change_uses_current(current):
    result = merge_value(ValueKind.TEXT, current)
    assert result.value == current
    assert not result.changed and not result.conflict
    assert result.state == (
        MergeState.CURRENT if current.available else MergeState.UNAVAILABLE
    )


@pytest.mark.parametrize(
    "kind,old,submitted,equivalent",
    [
        (ValueKind.TEXT, "Before", " Café ", "Cafe\u0301"),
        (ValueKind.EMAIL, "old@example.org", "USER@Example.org", "user@example.org"),
        (ValueKind.PHONE, "5025550101", "(502) 555-0100", "+1 502 555 0100"),
        (ValueKind.DATE, "2000-01-01", "2001-01-01", "2001-01-01"),
        (ValueKind.BOOLEAN, False, True, True),
        (ValueKind.IDENTIFIER, 1, 2, "2"),
        (ValueKind.MONEY, "1.00", "12.00", 12),
        (ValueKind.ENUM, "single", "married", " married "),
        (
            ValueKind.ADDRESS,
            dict.fromkeys(ADDRESS_COMPONENTS, "old"),
            dict.fromkeys(ADDRESS_COMPONENTS, "New"),
            dict.fromkeys(ADDRESS_COMPONENTS, " new "),
        ),
    ],
)
def test_every_type_uses_shared_comparison(kind, old, submitted, equivalent):
    """Canonical equality clears the marker while retaining source display form."""
    prior = PriorChange(KnownValue(True, old), submitted)
    result = merge_value(kind, KnownValue(True, equivalent), prior, dialing_region="US")
    assert result.state is MergeState.UPSTREAM_CAUGHT_UP
    assert result.value.value == equivalent
    assert not result.changed
    # Baseline equality and resolved Admin equality use the same registry too.
    result = merge_value(
        kind,
        KnownValue(True, equivalent),
        PriorChange(KnownValue(True, submitted), old),
        dialing_region="US",
    )
    assert result.state is MergeState.FAMILY_CHANGED
    result = merge_value(
        kind,
        KnownValue(True, equivalent),
        PriorChange(KnownValue(True, None), old, KnownValue(True, submitted)),
        dialing_region="US",
    )
    assert result.state is MergeState.ADMIN_RESOLVED


def test_unknown_source_does_not_resolve_or_invent_conflict():
    result = merge_value(
        ValueKind.TEXT,
        KnownValue(False),
        PriorChange(KnownValue(True, "old"), "family"),
    )
    assert result.value == KnownValue(True, "family")
    assert result.state is MergeState.UNAVAILABLE
    assert result.changed and not result.conflict


def test_unknown_baseline_cannot_be_treated_as_null():
    result = merge_value(
        ValueKind.TEXT, KnownValue(True, None), PriorChange(KnownValue(False), "family")
    )
    assert result.state is MergeState.CONFLICT
    result = merge_value(
        ValueKind.TEXT,
        KnownValue(True, "family"),
        PriorChange(KnownValue(False), "family"),
    )
    assert result.state is MergeState.UPSTREAM_CAUGHT_UP


def test_resolved_admin_null_is_explicit():
    result = merge_value(
        ValueKind.TEXT,
        KnownValue(True, None),
        PriorChange(KnownValue(True, "old"), "family", KnownValue(True, None)),
    )
    assert result.state is MergeState.ADMIN_RESOLVED
    assert result.value == KnownValue(True, None)
    assert not result.changed


def test_conflict_result_does_not_contain_hidden_values():
    prior = PriorChange(
        KnownValue(True, "private-baseline"),
        "family",
        KnownValue(True, "private-admin"),
    )
    result = merge_value(ValueKind.TEXT, KnownValue(True, "private-source"), prior)
    assert "private" not in repr(asdict(result))


def test_repeated_and_reordered_refresh_never_mutates_submission():
    """Reconciliation is a projection, not an accumulating mutation of history."""
    prior = PriorChange(KnownValue(True, "old"), "family", KnownValue(True, "admin"))
    expected = {
        value: merge_value(ValueKind.TEXT, KnownValue(True, value), prior)
        for value in ("old", "family", "admin", "other")
    }
    for current in ("old", "other", "family", "old", "admin", "other", "family"):
        assert (
            merge_value(ValueKind.TEXT, KnownValue(True, current), prior)
            == expected[current]
        )
        assert prior.submitted == "family"


def test_mutating_display_address_cannot_change_input():
    original = dict.fromkeys(ADDRESS_COMPONENTS, "source")
    result = merge_value(ValueKind.ADDRESS, KnownValue(True, original))
    result.value.value["line1"] = "modified"
    assert original["line1"] == "source"


@pytest.mark.parametrize("available,value", [(1, None), (False, "private")])
def test_invalid_availability_is_safe(available, value):
    with pytest.raises(ValueError, match="^Invalid source availability metadata.$"):
        KnownValue(available, value)
