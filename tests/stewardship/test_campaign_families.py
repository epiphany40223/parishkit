"""A campaign's Families rule decides its Families; legacy stays today's (#1393)."""

import hashlib
import json
from datetime import date
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import (
    family_is_active,
    family_is_parishioner,
    family_matches_rule,
    member_is_active,
    member_matches_rule,
)
from parishkit.stewardship.campaigns import campaign_families
from parishkit.stewardship.campaigns.configuration import campaign_values
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.families import (
    family_eligibility,
    family_statuses,
)
from parishkit.stewardship.source.windows import RefreshWindow, refresh_window

from . import campaign_factory
from .campaign_families_fixture import GROUPS, ORGANIZATION, parish

TODAY = date(2026, 9, 11)
# The fixture's whole corpus as origin/main built it before rules existed
# (sha256 of json.dumps(corpus, sort_keys=True)), computed with that code:
# a campaign with no rule must keep exactly these rows.
LEGACY_CORPUS_DIGEST = (
    "74dc7cd3ce8fd578ce29c6e30e0bc13d993e6e61cb41b4952981ba0957069c77"
)
ACTIVE = {"rule": "active_parishioners"}
STAFF = {"rule": "active_parishioners_and_staff"}


def advanced(groups, statuses=("Active",), *, registered=True):
    """An Advanced rule choosing ``groups`` (ids; 0 is no group)."""
    return {
        "rule": "advanced",
        "registered": registered,
        "family_groups": [
            campaign_families.NO_GROUP
            if identifier == 0
            else {"id": identifier, "name": GROUPS[identifier]}
            for identifier in sorted(groups)
        ],
        "member_statuses": list(statuses),
    }


def selected(rule):
    """The Family keys ``rule`` makes Portal- and email-eligible."""
    families = normalize_core(parish(), as_of=TODAY, families=rule)["family"]
    return (
        {key for key, row in families.items() if row["portal_eligible"]},
        {key for key, row in families.items() if row["email_eligible"]},
    )


def test_a_campaign_without_a_rule_builds_exactly_todays_rows():
    """The legacy rule is byte for byte what main built (the hard rule)."""
    corpus = normalize_core(parish(), as_of=TODAY)
    digest = hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()
    assert digest == LEGACY_CORPUS_DIGEST
    assert normalize_core(parish(), as_of=TODAY, families=None) == corpus
    assert not any("campaign_families" in row for row in corpus["family"].values())


def test_the_legacy_predicate_is_todays_functions():
    """family_matches_rule(None) is family_is_active and family_is_parishioner."""
    data = parish()
    for family in data.families.values():
        family = {
            **family,
            "py members": [
                member
                for member in data.members.values()
                if member["familyDUID"] == family["familyDUID"]
            ],
            "py family group": GROUPS.get(family.get("famGroupID")),
        }
        assert family_matches_rule(family, None, ORGANIZATION) is (
            family_is_active(family) and family_is_parishioner(family, ORGANIZATION)
        )
        for member in family["py members"]:
            assert member_matches_rule(member, None) is member_is_active(member)


@pytest.mark.parametrize(
    ("rule", "portal", "email"),
    [
        # Registered, in the Active group, with an Active Member.
        (ACTIVE, {"101", "109", "114"}, {"101"}),
        (STAFF, {"101", "102", "109", "114"}, {"101", "102"}),
        # Every group but the Inactive one, statuses as the presets.
        (advanced([1, 7, 8, 10]), {"101", "102", "109", "114"}, {"101", "102"}),
        # The Inactive group alone, and the no-group choice alone.
        (advanced([9]), {"103"}, {"103"}),
        (advanced([0]), {"104", "105"}, {"104", "105"}),
        # Registration not required.
        (
            advanced([7], registered=False),
            {"101", "106", "107", "109", "114"},
            {"101", "106", "107"},
        ),
        # Other statuses: ActOther heads are emailed, an empty one never.
        (
            advanced([1, 7, 10], ("Active", "ActOther", "Unknown")),
            {"101", "108", "109", "112", "114"},
            {"101", "108", "109", "112"},
        ),
    ],
)
def test_each_rule_selects_and_emails_its_families(rule, portal, email):
    """Email goes only through heads whose status the rule accepts."""
    assert selected(rule) == (portal, email)


def test_legacy_selects_what_it_always_did():
    """The fixture's legacy selection, for comparison with the rules above."""
    assert selected(None) == (
        {"101", "102", "104", "105", "108", "109", "112", "113", "114"},
        {"101", "102", "104", "105", "108", "109", "112", "113"},
    )


def test_a_lookup_group_named_no_family_group_joins_the_no_group_choice():
    """Should ParishSoft ever list a group with that name, (No Family Group)
    matches it too, so it never needs a second choice."""
    data = parish()
    data.family_groups[20] = "No Family Group"
    data.families[101]["famGroupID"] = 20
    rows = normalize_core(data, as_of=TODAY, families=advanced([0]))["family"]
    assert {key for key, row in rows.items() if row["portal_eligible"]} == {
        "101",
        "104",
        "105",
    }


def test_rule_rows_carry_the_rule_and_its_heads():
    """A rule row names its rule's digest; its heads are the rule's heads."""
    rows = normalize_core(parish(), as_of=TODAY, families=ACTIVE)["family"]
    assert {row["campaign_families"] for row in rows.values()} == {
        campaign_families.digest(ACTIVE)
    }
    assert rows["109"]["active_head_duids"] == []
    assert rows["108"]["active"] is True and rows["108"]["portal_eligible"] is False


def test_the_families_check_evaluates_the_recorded_rule():
    """Promotion's check recomputes each row under the snapshot's rule."""
    rule = advanced([9])
    corpus = normalize_core(parish(), as_of=TODAY, families=rule)
    assert {row.key for row in family_eligibility(corpus, rule) if row.eligible} == {
        "103"
    }
    # Another rule, the legacy one, or a forged row are refused.
    for other in (ACTIVE, None):
        with pytest.raises(InvalidSourcePayload):
            family_eligibility(corpus, other)
    corpus["family"]["101"]["portal_eligible"] = True
    with pytest.raises(InvalidSourcePayload):
        family_eligibility(corpus, rule)
    legacy = normalize_core(parish(), as_of=TODAY)
    with pytest.raises(InvalidSourcePayload):
        family_eligibility(legacy, rule)
    assert family_eligibility(legacy, None) == family_eligibility(legacy)


def test_an_inactive_group_campaign_family_is_active_in_its_campaign():
    """Delivery reads the campaign row's active flag, so it is set (#1393)."""
    corpus = normalize_core(parish(), as_of=TODAY, families=advanced([9]))
    (status,) = [
        row
        for row in family_statuses(corpus, suppressed_addresses=frozenset())
        if row.duid == 103
    ]
    assert (status.active, status.portal_eligible, status.email_eligible) == (
        True,
        True,
        True,
    )
    assert status.status_reason == "eligible"
    assert corpus["family"]["103"]["active"] is False


def test_legacy_statuses_keep_todays_active_flag():
    """Under the legacy rule a campaign row's active is ParishSoft's."""
    corpus = normalize_core(parish(), as_of=TODAY)
    statuses = {
        row.duid: row
        for row in family_statuses(corpus, suppressed_addresses=frozenset())
    }
    for key, family in corpus["family"].items():
        assert statuses[int(key)].active is family["active"]


GOOD = [
    ACTIVE,
    STAFF,
    advanced([0, 1, 9]),
    advanced([7], ("Active", "Unknown"), registered=False),
]
BAD = [
    None,
    [],
    {},
    {"rule": "legacy"},
    {"rule": "active_parishioners", "registered": True},
    {**advanced([7]), "extra": 1},
    {**advanced([7]), "registered": 1},
    {**advanced([7]), "family_groups": []},
    {**advanced([7]), "member_statuses": []},
    {**advanced([7]), "member_statuses": ["Inactive"]},
    {**advanced([7]), "member_statuses": ["Unknown", "Active"]},
    {**advanced([7]), "member_statuses": ["Active", "Active"]},
    {**advanced([7]), "family_groups": [{"id": 7, "name": ""}]},
    {**advanced([7]), "family_groups": [{"id": 7}]},
    {**advanced([7]), "family_groups": [{"id": "7", "name": "Active"}]},
    {**advanced([7]), "family_groups": [{"id": True, "name": "Active"}]},
    {**advanced([7]), "family_groups": [{"id": -1, "name": "Active"}]},
    {**advanced([7]), "family_groups": [{"id": 2**31, "name": "Active"}]},
    {**advanced([7]), "family_groups": [{"id": 0, "name": "No Family Group"}]},
    {**advanced([7, 8]), "family_groups": advanced([7, 8])["family_groups"][::-1]},
    {
        **advanced([7]),
        "family_groups": [{"id": 7, "name": "A"}, {"id": 7, "name": "A"}],
    },
]


@pytest.mark.parametrize("value", GOOD)
def test_canonical_rules_are_stored(value):
    """Each shape validates, alone and inside a campaign's values."""
    campaign_families.validate(value)
    campaign_values(campaign_factory.campaign(campaign_families=value)["values"])


@pytest.mark.parametrize("value", BAD)
def test_other_values_are_refused(value):
    """Python refuses what SQL's configuration guard refuses (0175)."""
    with pytest.raises(ConfigError):
        campaign_families.validate(value)
    with pytest.raises(ConfigError):
        campaign_values(campaign_factory.campaign(campaign_families=value)["values"])


def test_a_legacy_window_keeps_its_document_and_digest():
    """A campaign without a rule binds its refresh exactly as before."""
    values = campaign_factory.campaign()["values"]
    identifier = uuid4()
    window = refresh_window(campaign_id=identifier, state="active", values=values)
    assert window.families is None
    assert window.document() == {"campaign_id": str(identifier), "periods": []}
    assert window == RefreshWindow(identifier, ())
    ruled = refresh_window(
        campaign_id=identifier,
        state="active",
        values={**values, "campaign_families": ACTIVE},
    )
    assert ruled.document()["campaign_families"] == ACTIVE
    assert ruled.digest != window.digest


def test_load_evidence_names_the_rule_and_missing_groups():
    """A chosen group the lookup lacks is recorded; legacy records none."""
    assert campaign_families.evidence(None, GROUPS) is None
    missing = campaign_families.evidence(advanced([0, 7, 9]), {7: "Active"})
    assert missing["missing_groups"] == [{"id": 9, "name": "Inactive"}]
    assert missing["digest"] == campaign_families.digest(advanced([0, 7, 9]))
    assert campaign_families.missing_groups(STAFF, {7: "Active"}) == [
        {"id": None, "name": "Staff"}
    ]
    assert campaign_families.group_lookup_evidence({8: "Staff", 7: "Active"}) == [
        {"id": 7, "name": "Active"},
        {"id": 8, "name": "Staff"},
    ]
    cursor = {"load": {"campaign_families": missing}}
    assert campaign_families.recorded_rule(cursor) == advanced([0, 7, 9])
    assert campaign_families.recorded_digest(cursor) == missing["digest"]
    assert campaign_families.recorded_digest({"load": {}}) is None


class Recorded:
    """A promoted snapshot as the drop check reads it: cursor and counts."""

    def __init__(self, rule, portal):
        load = {
            "derived_counts": {
                "portal_eligible_families": portal,
                "email_eligible_families": portal,
                "active_head_families": portal,
                "valid_email_contacts": 50,
            }
        }
        if rule is not None:
            load["campaign_families"] = campaign_families.evidence(rule, GROUPS)
        self.cursor, self.counts = {"load": load}, {}


def test_the_drop_check_compares_rule_counts_only_under_the_same_rule():
    """Legacy snapshots keep today's baseline; a new rule's first load starts
    its eligibility trend again; later loads compare with that rule's."""
    from parishkit.stewardship.source.refreshing import (
        _derived_baseline,
        _rule_baseline,
    )

    def baseline(trend, rule):
        """The baseline a load under ``rule`` is checked against."""
        counted = trend[0].cursor["load"]["derived_counts"]
        return _rule_baseline(
            _derived_baseline(trend, counted),
            trend,
            counted,
            rule=campaign_families.digest(rule),
            counted_rule=campaign_families.recorded_digest(trend[0].cursor),
        )

    legacy = [Recorded(None, 900), Recorded(None, 1000)]
    assert baseline(legacy, None) == _derived_baseline(
        legacy, legacy[0].cursor["load"]["derived_counts"]
    )
    assert baseline(legacy, None)["portal_eligible_families"] == 1000
    first = baseline(legacy, ACTIVE)
    assert first["portal_eligible_families"] == 0
    assert first["valid_email_contacts"] == 50
    later = baseline([Recorded(ACTIVE, 400), *legacy], ACTIVE)
    assert later["portal_eligible_families"] == 400
