"""Choosing the example Families of a refused large change (ADM-13 PR 5c, #530).

What each count counted (``counted_keys``, shared with the derived counts),
which Families a refused load loses for each failing count
(``choose_examples``), and that the refusal carries the refused load's keys
to the worker without ever showing them. Recording, showing and deleting
the examples under the real guard are covered by
``database/test_source_drop_examples_postgresql.py``.
"""

import pytest

from parishkit.stewardship.source.count_names import DERIVED_COUNTS
from parishkit.stewardship.source.drop_examples import (
    EXAMPLE_LIMIT,
    EXAMPLE_MEASURES,
    choose_examples,
    counted_keys,
    example_measures,
    record_drop_examples,
)
from parishkit.stewardship.source.loading import (
    CountCheck,
    DestructiveSourceChange,
    derived_counts,
)

from .test_source_drop_acceptance import refused_load


def family(*, portal=True, email=True, heads=("9",)):
    """One normalized Family row's eligibility facts."""
    return {
        "portal_eligible": portal,
        "email_eligible": email,
        "active_head_duids": list(heads),
    }


def corpus(families, members=None, contacts=None):
    """A corpus with only the rows the example counts read."""
    return {
        "family": families,
        "member": members or {},
        "contact": contacts or {},
    }


def contact(kind, key, *, valid=True):
    """One contact row owned by a Member or a Family."""
    return {
        "owner_kind": kind,
        "owner_key": key,
        "emails": [{"value": "x", "valid": valid}],
    }


BASELINE = corpus(
    {
        "7": family(),
        "3": family(portal=False),
        "12": family(email=False, heads=()),
        "5": family(),
    },
    members={"31": {"family_key": "3"}, "71": {"family_key": "7"}},
    contacts={
        "member:31": contact("member", "31"),
        "family:12": contact("family", "12"),
        "member:71": contact("member", "71", valid=False),
    },
)


def test_the_derived_counts_are_the_sizes_of_the_counted_sets():
    """One definition: a count and its examples cannot disagree."""
    counted = counted_keys(BASELINE)
    assert set(counted) == set(EXAMPLE_MEASURES)
    assert derived_counts(BASELINE) == {
        name: len(counted[name]) for name in DERIVED_COUNTS
    }
    assert counted["portal_eligible_families"] == {"7", "12", "5"}
    assert counted["email_eligible_families"] == {"7", "3", "5"}
    assert counted["active_head_families"] == {"7", "3", "5"}
    assert counted["valid_email_contacts"] == {"member:31", "family:12"}
    # A Family and contact read without Members counts none.
    assert counted_keys({"family": {}, "contact": {}})["member"] == frozenset()


def test_each_failing_count_names_the_families_it_lost_lowest_duid_first():
    """A lost Family, a lost Member's Family, a Family no longer eligible,
    and the Family whose (or whose Member's) valid email went."""
    refused = counted_keys(
        corpus(
            {"7": family(portal=False), "12": family(email=False, heads=())},
            members={"71": {"family_key": "7"}},
            contacts={"member:71": contact("member", "71", valid=False)},
        )
    )
    assert choose_examples(EXAMPLE_MEASURES, BASELINE, refused) == {
        "family": [3, 5],
        "member": [3],
        "portal_eligible_families": [5, 7],
        "email_eligible_families": [3, 5],
        "active_head_families": [3, 5],
        "valid_email_contacts": [3, 12],
    }
    # Only the measures asked for.
    assert choose_examples(("member",), BASELINE, refused) == {"member": [3]}


def test_at_most_five_per_count_and_a_family_once():
    """The lowest five DUIDs, each Family once however many records it lost."""
    families = {str(duid): family() for duid in range(20, 9, -1)}
    members = {
        f"{duid}0{n}": {"family_key": str(duid)} for duid in families for n in (1, 2)
    }
    baseline = corpus(families, members=members)
    chosen = choose_examples(("family", "member"), baseline, counted_keys(corpus({})))
    assert chosen == {"family": [10, 11, 12, 13, 14], "member": [10, 11, 12, 13, 14]}
    assert EXAMPLE_LIMIT == 5


def test_nothing_lost_or_a_record_without_a_family_gives_no_examples():
    """A count can fail against a trend high the current data no longer
    holds; then there is nothing to show for it."""
    baseline = corpus(
        {"4": family()},
        members={"40": {"family_key": "4"}},
        contacts={"member:99": contact("member", "99")},
    )
    refused = counted_keys(corpus({"4": family()}, members={"40": {"family_key": "4"}}))
    assert choose_examples(EXAMPLE_MEASURES, baseline, refused) == {}


def check(measure, after, failed):
    """One count a refusal was checked on."""
    return CountCheck(measure, 100, after, 25, failed)


def test_only_failing_family_member_and_contact_counts_get_examples():
    """Ministries, rosters and funds never; a passing count never; an empty
    load (no Families or no Members) never, since it cannot be accepted."""
    checks = (
        check("family", 60, True),
        check("member", 90, False),
        check("ministry", 10, True),
        check("valid_email_contacts", 10, True),
    )
    assert example_measures(checks) == ("family", "valid_email_contacts")
    assert example_measures((check("family", 0, True),)) == ()
    assert example_measures((check("family", 60, True), check("member", 0, True))) == ()


def test_recording_needs_the_refused_keys_and_a_failing_count(monkeypatch):
    """Without either nothing is read or written, so a refusal that carries
    no keys (a lapsed acceptance, say) records none."""
    from parishkit.stewardship.source import drop_examples

    def unreachable():
        """Recording must stop before reading the current data."""
        raise AssertionError("read")

    monkeypatch.setattr(drop_examples, "_baseline", unreachable)
    assert record_drop_examples(None, (check("family", 60, True),), {}) == 0
    assert record_drop_examples(None, (check("ministry", 1, True),), {"x": 1}) == 0


def test_the_refusal_carries_the_refused_keys_but_never_shows_them(tmp_path):
    """The loader attaches what the refused load counted; neither the
    error's text nor the classified failure's repr includes it."""
    from parishkit.stewardship.source.failures import classify_read_failure

    with pytest.raises(DestructiveSourceChange) as raised:
        refused_load(tmp_path, None)
    refusal = raised.value
    assert set(refusal.counted) == set(EXAMPLE_MEASURES)
    assert refusal.counted["family"]
    failure = classify_read_failure(refusal, has_source_claim=True)
    assert failure.counted is refusal.counted
    for shown in (repr(failure), str(refusal)):
        assert "counted" not in shown
        for key in refusal.counted["family"]:
            assert f"'{key}'" not in shown
