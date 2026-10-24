"""Create the campaign's Families section: rule, counts and words (#1393)."""

from parishkit.stewardship.accounts import families_choice as choice
from parishkit.stewardship.campaigns import campaign_families

GROUPS = (
    choice.Group(1, "(blank)", 1),
    choice.Group(7, "Active", 3),
    choice.Group(8, "Staff", 1),
    choice.Group(9, "Inactive", 1),
    choice.Group(0, "(No Family Group)", 1),
)
# (registered, famGroupID, group, Member statuses, emailed head statuses)
FACTS = (
    (True, 7, "Active", frozenset({"Active"}), frozenset({"Active"})),
    (True, 7, "Active", frozenset({"Active", "ActOther"}), frozenset({"ActOther"})),
    (False, 7, "Active", frozenset({"Active"}), frozenset({"Active"})),
    (True, 8, "Staff", frozenset({"Active"}), frozenset({"Active"})),
    (True, 9, "Inactive", frozenset({"Unknown"}), frozenset({"Unknown"})),
    (True, None, None, frozenset({"Active"}), frozenset()),
    (True, 1, "(blank)", frozenset({"Inactive"}), frozenset({"Inactive"})),
)
SOURCE = choice.Source(None, None, GROUPS, FACTS)


def posted(**data):
    """``posted_rule`` over cleaned data."""
    return choice.posted_rule(data, GROUPS)


def test_counts_are_matching_and_emailable_families():
    """Only a head whose status the rule accepts makes a Family emailable."""
    assert choice.counts(SOURCE, {"rule": "active_parishioners"}) == (2, 1)
    assert choice.counts(SOURCE, {"rule": "active_parishioners_and_staff"}) == (3, 2)
    everything = {
        "rule": "advanced",
        "registered": False,
        "family_groups": [{"id": group.id, "name": group.name} for group in GROUPS],
        "member_statuses": ["Active", "ActOther", "Unknown"],
    }
    assert choice.counts(SOURCE, everything) == (6, 5)


def test_basic_stores_the_preset_and_advanced_its_conditions():
    """Basic stores the preset; Advanced canonical lists; gaps are named."""
    assert posted(families_mode="basic", families_preset="active_parishioners") == (
        {"rule": "active_parishioners"},
        None,
        None,
    )
    assert posted(families_mode="basic")[1:] == (choice.PRESET, choice.NO_PRESET_CHOSEN)
    rule, _, _ = posted(
        families_mode="advanced",
        families_registered="not_required",
        families_groups=[9, 0, 7],
        families_statuses=["Unknown", "Active"],
    )
    campaign_families.validate(rule)
    assert rule == {
        "rule": "advanced",
        "registered": False,
        "family_groups": [
            campaign_families.NO_GROUP,
            {"id": 7, "name": "Active"},
            {"id": 9, "name": "Inactive"},
        ],
        "member_statuses": ["Active", "Unknown"],
    }
    assert posted(families_mode="advanced", families_statuses=["Active"])[1] == (
        choice.GROUPS
    )
    assert posted(families_mode="advanced", families_groups=[7])[1] == choice.STATUSES


def test_a_preset_maps_to_the_advanced_rule_it_equals():
    """Basic → Advanced is lossless: the preset's groups by name."""
    staff = choice.preset_rule("active_parishioners_and_staff", GROUPS)
    assert [group["id"] for group in staff["family_groups"]] == [7, 8]
    assert choice.counts(SOURCE, staff) == choice.counts(
        SOURCE, {"rule": "active_parishioners_and_staff"}
    )
    # A preset whose group the data lacks maps to no group.
    assert choice.preset_rule("active_parishioners", GROUPS[:1])["family_groups"] == []


def test_the_rule_in_words():
    """Settings and the confirmation describe the rule; legacy has its own."""
    assert choice.describe(None) == str(choice.LEGACY_WORDS)
    assert choice.describe({"rule": "active_parishioners"}).startswith(
        "Active parishioners: Families registered to this parish"
    )
    words = choice.describe(
        {
            "rule": "advanced",
            "registered": True,
            "family_groups": [campaign_families.NO_GROUP, {"id": 99, "name": "Gone"}],
            "member_statuses": ["Active", "ActOther"],
        },
        GROUPS,
    )
    assert "(No Family Group), Gone (no longer in ParishSoft)" in words
    assert "Active or ActOther" in words


def test_the_preview_line_counts_or_says_none_match():
    """Two counts, or the gate's reason; no names."""
    line = choice.preview_line(SOURCE, {"rule": "active_parishioners"})
    assert line == {
        "count": "2 Families match in the ParishSoft data as of",
        "emailable": "; 1 of them can be emailed.",
        "matches": 2,
    }
    none = {
        "rule": "advanced",
        "registered": True,
        "family_groups": [{"id": 8, "name": "Staff"}],
        "member_statuses": ["Unknown"],
    }
    assert choice.preview_line(SOURCE, none)["count"] == str(choice.NO_MATCH)


def test_the_command_line_posts_a_rule_as_the_page_does():
    """A stored rule becomes the section's fields and reads back the same."""
    for rule in (
        {"rule": "active_parishioners_and_staff"},
        {
            "rule": "advanced",
            "registered": False,
            "family_groups": [
                campaign_families.NO_GROUP,
                {"id": 9, "name": "Inactive"},
            ],
            "member_statuses": ["ActOther"],
        },
    ):
        data = choice.families_post(rule)
        if "families_groups" in data:
            data["families_groups"] = [int(item) for item in data["families_groups"]]
        assert posted(**data)[0] == rule


def test_each_preset_radio_is_described_by_its_help_line():
    """A screen reader reads a preset's definition with its radio."""
    radios = choice.DescribedRadioSelect(choices=list(choice.PRESET_LABELS.items()))
    html = radios.render(choice.PRESET, "active_parishioners", {"id": "id_preset"})
    assert 'aria-describedby="id_preset_0-help"' in html
    assert 'aria-describedby="id_preset_1-help"' in html
