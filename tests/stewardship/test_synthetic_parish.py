"""The synthetic parish is deterministic, scales by the table and keeps its rules."""

from datetime import date, datetime

import pytest

from parishkit.parishsoft import HEAD_MEMBER_TYPES
from parishkit.stewardship.local import LOCAL_ORGANIZATION_ID
from parishkit.stewardship.local.synthetic_parish import (
    DEFAULT_FAMILIES,
    FAMILY_GROUPS,
    SyntheticParish,
    age_at,
    digest,
    generate,
    is_child,
    scaling,
)

ANCHOR = date(2026, 9, 16)
SIZES = (100, 1100)
# The digests of seed 1 at the default size and at the real parish's size.
# Any generator change, however small, changes these; update them on purpose.
DIGESTS = {
    100: "c76fa5629bc939391b69b465e5bb870dc92de25bf4f7caf1eef4fdf798a2e577",
    1100: "0fe8b7236506212c6de1ce2fb4d45b6302d2c19eb54217f1f6772c0bf801f15b",
}
_cache = {}


def parish(families=DEFAULT_FAMILIES, seed=1):
    """Generate once per size; the generator is pure, so sharing is safe."""
    if (seed, families) not in _cache:
        _cache[(seed, families)] = generate(seed, families, ANCHOR)
    return _cache[(seed, families)]


def regular(value):
    """Families and Members other than the held-back late-added Family."""
    families = [f for f in value.families if f["familyDUID"] != value.late_family_id]
    members = [m for m in value.members if m["familyDUID"] != value.late_family_id]
    return families, members


def roster_rows(value):
    return [row for rows in value.ministry_rosters.values() for row in rows]


def test_same_inputs_produce_the_same_digest_and_other_inputs_do_not():
    """Byte-identical responses require byte-identical generated data."""
    first, second = generate(7, 30, ANCHOR), generate(7, 30, ANCHOR)
    assert isinstance(first, SyntheticParish)
    assert digest(first) == digest(second)
    assert first == second
    assert digest(generate(8, 30, ANCHOR)) != digest(first)
    assert digest(generate(7, 31, ANCHOR)) != digest(first)
    assert digest(generate(7, 30, date(2026, 9, 17))) != digest(first)


@pytest.mark.parametrize("families", SIZES)
def test_fixed_seed_digest_is_pinned(families):
    """The fake's data for a seed is a fixed fact, not something that drifts."""
    assert digest(parish(families)) == DIGESTS[families]


def test_scaling_table_values():
    """The specification's table at the default size and at 1,100 Families."""
    assert scaling(100) == {
        "families": 100,
        "inactive_families": 8,
        "no_email_families": 10,
        "family_groups": 6,
        "family_workgroups": 4,
        "member_workgroups": 5,
        "ministries": 25,
        "funds": 4,
        "pledges": 64,
        "contributions": 1360,
    }
    assert scaling(1100) == {
        "families": 1100,
        "inactive_families": 88,
        "no_email_families": 110,
        "family_groups": 6,
        "family_workgroups": 8,
        "member_workgroups": 10,
        "ministries": 40,
        "funds": 6,
        "pledges": 704,
        "contributions": 14960,
    }
    with pytest.raises(ValueError):
        scaling(0)


@pytest.mark.parametrize("families", SIZES)
def test_collections_scale_with_the_family_count(families):
    """Each generated collection has the table's size, plus one late Family."""
    value = parish(families)
    counts = scaling(families)
    regular_families, members = regular(value)
    assert len(regular_families) == families
    assert len(value.families) == families + 1
    assert len(value.family_groups) == counts["family_groups"] == len(FAMILY_GROUPS)
    assert len(value.family_workgroups) == counts["family_workgroups"]
    assert len(value.member_workgroups) == counts["member_workgroups"]
    assert len(value.ministries) == counts["ministries"]
    assert len(value.funds) == counts["funds"]
    assert len(value.pledges) == counts["pledges"]
    assert len(value.contributions) == counts["contributions"]
    # 8% inactive or non-parishioner, half each; 10% with no head email.
    inactive = [f for f in regular_families if f["famGroupID"] == 2]
    elsewhere = [
        f
        for f in regular_families
        if f["registeredOrganizationID"] != LOCAL_ORGANIZATION_ID
    ]
    assert len(inactive) + len(elsewhere) == counts["inactive_families"]
    assert abs(len(inactive) - len(elsewhere)) <= 1
    assert not {f["familyDUID"] for f in inactive} & {
        f["familyDUID"] for f in elsewhere
    }
    assert (
        sum(1 for f in regular_families if f["eMailAddress"] is None)
        == counts["no_email_families"]
    )
    # Mean Family size 2.7, about 30% children, 2% deceased.
    assert abs(len(members) / families - 2.7) < 0.1
    children = [m for m in members if is_child(m, ANCHOR)]
    assert 0.25 <= len(children) / len(members) <= 0.35
    deceased = [m for m in members if m["memberStatus"] == "Deceased"]
    assert len(deceased) == round(0.02 * len(members))
    assert all(m["dateOfDeath"] for m in deceased)
    # Fifteen months of contributions ending on the anchor date.
    days = sorted(
        date.fromisoformat(c["contributionDate"][:10]) for c in value.contributions
    )
    assert date(2025, 6, 16) <= days[0] and days[-1] <= ANCHOR
    assert days[-1] > date(2026, 9, 1) and days[0] < date(2025, 7, 1)


@pytest.mark.parametrize("families", SIZES)
def test_member_types_follow_the_household_rules(families):
    """Heads are Head or Husband/Wife; children are Child and under 18; others adult."""
    value = parish(families)
    by_family = {}
    for member in value.members:
        by_family.setdefault(member["familyDUID"], []).append(member)
    for family in value.families:
        members = by_family[family["familyDUID"]]
        assert 1 <= len(members) <= 5 and family["hasMembers"] == len(members)
        heads = [m for m in members if m["memberType"] in HEAD_MEMBER_TYPES]
        assert 1 <= len(heads) <= 2
        if len(heads) == 2:
            assert {m["memberType"] for m in heads} == {"Husband", "Wife"}
        else:
            assert heads[0]["memberType"] == "Head"
        if len(members) == 1:
            assert not is_child(members[0], ANCHOR)
    for member in value.members:
        born = date.fromisoformat(member["birthdate"][:10])
        if member["memberType"] == "Child":
            assert age_at(born, ANCHOR) < 18 and is_child(member, ANCHOR)
            assert member["emailAddress"] is None
        else:
            assert member["memberType"] in HEAD_MEMBER_TYPES | {"Other"}
            assert age_at(born, ANCHOR) >= 18 and not is_child(member, ANCHOR)
        assert member["memberStatus"] in {"Active", "Inactive", "Deceased"}
    # Members of inactive Families are inactive themselves.
    for family in value.families:
        if family["famGroupID"] == 2:
            assert all(
                m["memberStatus"] != "Active" for m in by_family[family["familyDUID"]]
            )


@pytest.mark.parametrize("families", SIZES)
def test_ministry_rosters_hold_only_active_adults_in_the_stated_shares(families):
    """No child on any roster; 40% volunteer, 30% of them 2+, 10% 3+, 5% ended."""
    value = parish(families)
    _, members = regular(value)
    by_id = {m["memberDUID"]: m for m in members}
    rows = roster_rows(value)
    eligible = [
        m for m in members if m["memberStatus"] == "Active" and not is_child(m, ANCHOR)
    ]
    ministries_of = {}
    for row in rows:
        member = by_id[row["memberId"]]
        assert not is_child(member, ANCHOR)
        assert member["memberStatus"] == "Active"
        assert row["familyId"] == member["familyDUID"]
        assert row["ministryTypeId"] in value.ministry_rosters
        ministries_of.setdefault(row["memberId"], set()).add(row["ministryTypeId"])
    volunteers = len(ministries_of)
    assert abs(volunteers / len(eligible) - 0.40) < 0.03
    two_plus = sum(1 for v in ministries_of.values() if len(v) >= 2)
    three_plus = sum(1 for v in ministries_of.values() if len(v) >= 3)
    assert abs(two_plus / volunteers - 0.30) < 0.03
    assert abs(three_plus / volunteers - 0.10) < 0.03
    ended = [row for row in rows if row["endDate"]]
    assert abs(len(ended) / len(rows) - 0.05) < 0.02
    for row in ended:
        assert row["startDate"] < row["endDate"] < ANCHOR.isoformat()
    for identifier, roster in value.ministry_rosters.items():
        active = [row for row in roster if row["endDate"] is None]
        assert active, f"Ministry {identifier} has no active Member"
        assert [row["memberId"] for row in roster] == sorted(
            row["memberId"] for row in roster
        )
        assert len({row["memberId"] for row in roster}) == len(roster)


def test_small_parish_still_gives_every_ministry_an_active_member():
    """With few volunteers the shares bend, but the floor rule holds."""
    value = generate(3, 20, ANCHOR)
    assert len(value.ministries) == 25
    for roster in value.ministry_rosters.values():
        assert any(row["endDate"] is None for row in roster)
    assert not any(
        is_child(m, ANCHOR) for m in value.members if m["memberType"] != "Child"
    )


def test_emails_and_data_quality_cases():
    """Every address is at example.test; the specified odd records are present."""
    value = parish()
    emails = [
        fragment.strip()
        for row in (*value.members, *value.families, *value.contacts)
        for field in ("emailAddress", "eMailAddress")
        if row.get(field)
        for fragment in row[field].split(";")
    ]
    assert emails and all(email.endswith("@example.test") for email in emails)
    heads = [m for m in value.members if m["memberType"] in HEAD_MEMBER_TYPES]
    assert any(";" in (m["emailAddress"] or "") for m in heads)
    assert any(f["mailingName"] == "" for f in value.families)
    assert any(f["envelopeNumber"] == 0 for f in value.families)
    assert all(f["primaryAddress1"] and f["primaryCity"] for f in value.families)
    assert value.organization == {
        "organizationID": LOCAL_ORGANIZATION_ID,
        "organizationReportName": "Synthetic Parish",
    }


def test_late_added_family_is_extra_and_has_no_history():
    """Exactly one held-back Family at every size, new to every other collection."""
    for families in (1, 7, *SIZES):
        value = parish(families) if families in SIZES else generate(1, families, ANCHOR)
        late = value.late_family_id
        assert late == value.families[-1]["familyDUID"]
        members = [m["memberDUID"] for m in value.members if m["familyDUID"] == late]
        assert len(members) == 3
        assert not any(p["familyID"] == late for p in value.pledges)
        assert not any(c["familyId"] == late for c in value.contributions)
        assert not any(r["memberId"] in members for r in roster_rows(value))
        assert not any(
            r["memberId"] in members
            for rows in value.member_workgroup_rosters.values()
            for r in rows
        )
        assert not any(
            r["familyId"] == late
            for rows in value.family_workgroup_rosters.values()
            for r in rows
        )


def test_one_family_parish_still_generates_rosters():
    """Even a single Family yields a volunteer, so no seed can fail generation."""
    for seed in range(40):
        value = generate(seed, 1, ANCHOR)
        assert len(value.families) == 2 and len(value.ministries) == 25
        for roster in value.ministry_rosters.values():
            assert any(row["endDate"] is None for row in roster)
    with pytest.raises(ValueError):
        generate(1, 0, ANCHOR)


def test_giving_references_resolve_and_amounts_are_exact():
    """Pledges and contributions point at known Families and funds, or are anonymous."""
    value = parish()
    families = {f["familyDUID"] for f in value.families}
    funds = {f["fundId"] for f in value.funds}
    pledge_funds = {f["fundId"] for f in value.funds if f["requiresPledges"]}
    for pledge in value.pledges:
        assert pledge["familyID"] in families and pledge["fundID"] in pledge_funds
        assert pledge["organizationID"] == LOCAL_ORGANIZATION_ID
        assert type(pledge["currentPledgeAmount"]) is int
        assert pledge["pledgeStartDate"][5:10] == "01-01"
    anonymous = [c for c in value.contributions if c["familyId"] == 0]
    assert 0 < len(anonymous) < len(value.contributions) * 0.03
    for contribution in value.contributions:
        assert contribution["fundId"] in funds
        assert contribution["familyId"] == 0 or contribution["familyId"] in families
        assert round(contribution["contributionAmount"] * 100) == (
            contribution["contributionAmount"] * 100
        )
    assert [c["contributionID"] for c in value.contributions] == sorted(
        c["contributionID"] for c in value.contributions
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("1", 10, ANCHOR),
        (True, 10, ANCHOR),
        (-1, 10, ANCHOR),
        (1, 0, ANCHOR),
        (1, 10.0, ANCHOR),
        (1, 10, "2026-09-16"),
        (1, 10, datetime(2026, 9, 16)),
    ],
)
def test_generate_validates_its_inputs(arguments):
    """Only an integer seed, a bounded Family count and a civil date are accepted."""
    with pytest.raises(ValueError):
        generate(*arguments)
    with pytest.raises(ValueError):
        generate(1, 10, ANCHOR, organization_id=0)
