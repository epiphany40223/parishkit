"""A load's drop check compares with the recent trend of full refreshes (#387)."""

from types import SimpleNamespace

from parishkit.stewardship.source.drop_acceptance import ACCEPTANCE_KEY
from parishkit.stewardship.source.loading import DEFAULT_MAXIMUM_DROP_PERCENT
from parishkit.stewardship.source.refreshing import (
    _accepted_measures,
    _derived_baseline,
    _largest_counts,
    _resets_trend,
)


def full(counts, limit=None):
    """A stand-in promoted full snapshot with its counts and load limit."""
    cursor = {} if limit is None else {"load": {"maximum_drop_percent": limit}}
    return SimpleNamespace(counts=counts, cursor=cursor)


def test_each_count_takes_its_largest_recent_value():
    """Drops just under the limit from one full to the next cannot compound."""
    trend = [
        full({"family": 800, "member": 2000}),
        full({"family": 880, "member": 1900}),
        full({"family": 1000, "member": 1950}),
    ]
    assert _largest_counts(trend) == {"family": 1000, "member": 2000}


def test_only_the_newest_kinds_are_compared_and_bad_old_values_are_skipped():
    """An older snapshot's missing or malformed count never becomes a baseline."""
    trend = [
        full({"family": 800, "fund": 3}),
        full({"family": "900"}),
        full({"family": 850, "pledge": 99}),
    ]
    assert _largest_counts(trend) == {"family": 850, "fund": 3}


def test_a_malformed_newest_count_is_left_for_the_count_check_to_refuse():
    """Nothing is repaired here; the existing validation still sees it."""
    assert _largest_counts([full({"family": None}), full({"family": 900})]) == {
        "family": None
    }
    assert _largest_counts([full(None)]) is None


def test_only_a_raised_limit_resets_the_trend():
    """An operator accepting a large change starts the trend again."""
    assert _resets_trend(full({}, DEFAULT_MAXIMUM_DROP_PERCENT + 1))
    assert not _resets_trend(full({}, DEFAULT_MAXIMUM_DROP_PERCENT))
    assert not _resets_trend(full({}, DEFAULT_MAXIMUM_DROP_PERCENT - 5))
    assert not _resets_trend(full({}))
    assert not _resets_trend(SimpleNamespace(counts={}, cursor=None))


def counts(family):
    """A full load's complete record counts, with the Family count varied."""
    from parishkit.stewardship.source.corpus import KINDS

    return {kind: 100 for kind in KINDS} | {"family": family}


def derived(eligible):
    """A full load's derived counts, with one count varied."""
    return {
        "portal_eligible_families": eligible,
        "email_eligible_families": 900,
        "active_head_families": 1000,
        "valid_email_contacts": 2500,
    }


def check(new_counts, new_derived, history, *, current_derived=None):
    """Run the real drop check against the trend of promoted fulls."""
    from parishkit.stewardship.source.loading import check_source_counts

    trend = list(reversed(history))
    check_source_counts(
        new_counts,
        new_derived,
        previous_full_counts=_largest_counts(trend),
        previous_derived_counts=_derived_baseline(trend, current_derived),
        maximum_drop_percent=DEFAULT_MAXIMUM_DROP_PERCENT,
    )


def promoted(record, eligible, *, accepted=None, member=100):
    """A promoted full refresh's manifest, as the trend reads it.

    ``accepted``, when given, is the counts an Administrator's one-time
    acceptance let fall in it (#530), recorded as the worker records them.
    """
    load = {"derived_counts": derived(eligible)}
    if accepted is not None:
        load[ACCEPTANCE_KEY] = {"id": "0b8f", "actor_id": "4c1d", "failed": accepted}
    return SimpleNamespace(
        counts=counts(record) | {"member": member}, cursor={"load": load}
    )


def test_compounding_record_drops_are_refused_on_the_third_full():
    """Each full falls about 20% from the one before: the second passes (20%
    below the week's largest), the third is refused (36% below it), though
    it is only 20% below the last full."""
    import pytest

    from parishkit.stewardship.source.loading import DestructiveSourceChange

    history = [promoted(1000, 1000)]
    check(counts(800), derived(1000), history)
    history.append(promoted(800, 1000))
    with pytest.raises(DestructiveSourceChange) as refused:
        check(counts(640), derived(1000), history)
    assert refused.value.loss[0] == "family"
    # Against the last full alone, as before, the third would have passed.
    check(counts(640), derived(1000), history[-1:])


def test_compounding_eligibility_drops_are_refused_on_the_second_full():
    """Derived counts use the same trend: 1000, then 790 (-21%), then 600
    (-24% from the last, -40% from the week's largest) is refused."""
    import pytest

    from parishkit.stewardship.source.loading import DestructiveSourceChange

    history = [promoted(1000, 1000), promoted(1000, 790)]
    with pytest.raises(DestructiveSourceChange) as refused:
        check(counts(1000), derived(600), history)
    assert refused.value.loss[0] == "portal_eligible_families"
    check(counts(1000), derived(600), history[-1:])


def test_an_acceptance_starts_only_the_failed_counts_trends_again():
    """#530: the counts an acceptance let fall start again from that refresh;
    every other count keeps its history, and malformed evidence resets
    nothing. An acceptance never resets the whole trend."""
    accepted = promoted(500, 1000, accepted=["family"], member=760)
    trend = [accepted, promoted(1000, 1000, member=1000)]
    assert _accepted_measures(accepted) == {"family"}
    assert not _resets_trend(accepted)
    largest = _largest_counts(trend)
    assert (largest["family"], largest["member"]) == (500, 1000)
    for value in (None, {}, {"failed": "family"}, {"failed": [7]}):
        broken = promoted(500, 1000)
        broken.cursor["load"][ACCEPTANCE_KEY] = value
        assert _accepted_measures(broken) == frozenset()
        assert _largest_counts([broken, trend[1]])["family"] == 1000


def test_after_an_acceptance_a_passing_count_still_compounds():
    """The accepted Family drop is not refused again, but Members, which
    passed 24% below their high in the accepted refresh, are refused when
    they fall a further 24% (42% below the week's high)."""
    import pytest

    from parishkit.stewardship.source.loading import DestructiveSourceChange

    history = [
        promoted(1000, 1000, member=1000),
        promoted(500, 1000, accepted=["family"], member=760),
    ]
    check(counts(500) | {"member": 760}, derived(1000), history)
    with pytest.raises(DestructiveSourceChange) as refused:
        check(counts(500) | {"member": 578}, derived(1000), history)
    assert refused.value.loss[0] == "member"


def test_an_accepted_eligibility_count_starts_again_and_others_keep_history():
    """The same per-count rule for derived counts, with the current data."""
    import pytest

    from parishkit.stewardship.source.loading import DestructiveSourceChange

    history = [
        promoted(1000, 1000),
        promoted(1000, 600, accepted=["portal_eligible_families"]),
    ]
    trend = list(reversed(history))
    assert _derived_baseline(trend, None)["portal_eligible_families"] == 600
    assert _derived_baseline(trend, derived(650))["portal_eligible_families"] == 650
    assert _derived_baseline(trend, None)["email_eligible_families"] == 900
    check(counts(1000), derived(600), history)
    older = history[0].cursor["load"]["derived_counts"]
    older["valid_email_contacts"] = 4000
    with pytest.raises(DestructiveSourceChange) as refused:
        check(counts(1000), derived(600), history)
    assert refused.value.loss[0] == "valid_email_contacts"
