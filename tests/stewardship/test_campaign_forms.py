"""Campaign forms share canonical date/module validation without a database."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.admin_editing import direct_key, form_action
from parishkit.stewardship.accounts.campaign_forms import (
    DATE_PAIRS,
    FINANCIAL_REQUIRED,
    LEADER_ROLES_HELP,
    CampaignForm,
    initial_fields,
)
from parishkit.stewardship.accounts.campaign_preview import (
    describe_changes,
    display_value,
)
from parishkit.stewardship.campaigns.configuration import campaign_values

from .campaign_factory import campaign, financial


def posted(**changes):
    """A complete census-only draft with no hidden module-dependent data."""
    return initial_fields(campaign()["values"], digest="a" * 64) | changes


def form_for(values, *, previous=None):
    """The authoritative source controls choices; request values cannot add them."""
    return CampaignForm(
        values,
        previous=previous,
        ministries=[("1", "First ministry"), ("2", "Second ministry")],
        funds=[("1", "Current fund"), ("2", "Comparison fund")],
    )


def test_census_values_round_trip_and_default_form_are_safe():
    """Census does not require fund/Ministry inputs or invent optional slot data."""
    values = campaign()["values"]
    form = form_for(posted())
    assert form.is_valid(), form.errors
    assert form.values() == values
    assert campaign_values(form.values())
    empty = CampaignForm()
    assert not empty.is_bound and not empty.is_valid()


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 255},
        {"name": "bad\x00name"},
        {"timezone": "Invalid/Zone"},
        {"start_date": "not-date"},
        {"end_date": "2054-10-01"},
        {"end_date": "2054-09-30"},
        {"census": False},
        {"ministry_duids": ["1"]},
        {"fund_duids": ["1"]},
        {"comparison_fund_duids": ["2"]},
        {"financial_start": "2055-01-01"},
        {"financial_end": "2055-12-31"},
        {"comparison_start": "2026-01-01"},
        {"comparison_end": "2026-12-31"},
        {"financial_enabled": True},
        {"ministry": True, "ministry_duids": ["999"]},
        {"ministry": True, "ministry_duids": ["1", "1"]},
        {"base_digest": "bad"},
    ],
)
def test_invalid_or_disabled_module_data_is_not_accepted(changes):
    """Hidden data does not become canonical simply because a browser posted it."""
    assert not form_for(posted(**changes)).is_valid()


def test_catalog_selections_are_sorted_and_unknown_ids_are_rejected():
    """The same source selection always serializes into the same YAML values."""
    form = form_for(posted(ministry=True, ministry_duids=["2", "1"]))
    assert form.is_valid(), form.errors
    assert form.values()["ministry_duids"] == [1, 2]
    assert form.values()["modules"] == ["census", "ministry"]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"financial_start": "2054-11-01", "financial_end": "2055-10-31"},
        {"financial_start": "2056-02-29", "financial_end": "2057-02-27"},
    ],
)
def test_exact_financial_periods_and_explicit_fund_mappings(changes):
    """Leap years and the earliest commitment period start (the day after
    the campaign ends) use the existing canonical resolver."""
    values = campaign(modules=["financial"], financial=financial())["values"]
    form = form_for(initial_fields(values, digest="a" * 64) | changes)
    assert form.is_valid(), form.errors
    result = form.values()
    assert result["financial"]["fund_duids"] == [1]
    assert result["financial"]["comparison_fund_duids"] == [2]
    assert campaign_values(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"financial_end": "2055-12-30"},
        {"comparison_end": "2026-12-30"},
        {"financial_start": "2054-10-01", "financial_end": "2055-09-30"},
        {"fund_duids": []},
        {"comparison_fund_duids": []},
        {"fund_duids": ["999"]},
        {"comparison_fund_duids": ["2", "2"]},
        {"financial_start": ""},
        {"financial_end": ""},
        {"comparison_start": ""},
    ],
)
def test_financial_periods_require_complete_valid_fields(changes):
    """All mappings/dates must be explicitly supplied when financial is enabled."""
    values = campaign(modules=["financial"], financial=financial())["values"]
    form = form_for(initial_fields(values, digest="a" * 64) | changes)
    assert not form.is_valid()


def test_previous_share_and_content_versions_are_preserved_without_aliasing():
    """Structural editing does not accept replacement optional identities from POST."""
    options = [{"id": str(uuid4()), "label": "Other", "free_text": True}]
    slots = {name: str(uuid4()) for name in ("welcome", "financial", "additional")}
    values = campaign(
        modules=["financial"],
        financial=financial(),
        share_options=options,
        content_versions=slots,
    )["values"]
    form = form_for(initial_fields(values, digest="a" * 64), previous=values)
    assert form.is_valid(), form.errors
    assert form.values() == values
    values["share_options"][0]["label"] = "Caller mutation"
    assert form.values()["share_options"][0]["label"] == "Other"
    form.values()["share_options"][0]["label"] = "Result mutation"
    assert form.values()["share_options"][0]["label"] == "Other"
    reduced = form_for(
        posted(additional_information=False, year_label=""), previous=values
    )
    assert reduced.is_valid(), reduced.errors
    assert reduced.values()["share_options"] == []
    assert reduced.values()["content_versions"] == {"welcome": slots["welcome"]}
    assert reduced.values()["year_label"] is None


def test_closed_action_parser_allows_only_named_multiselects():
    """Multi-select support does not weaken scalar or confirmation replay checks."""
    data = QueryDict("action=preview&ministry_duids=1&ministry_duids=2")
    assert (
        form_action(
            data, preview_fields={"ministry_duids"}, multiple_fields={"ministry_duids"}
        )
        == "preview"
    )
    with pytest.raises(ValueError):
        form_action(data, preview_fields={"ministry_duids"})
    for raw in (
        "action=preview&name=a&name=b",
        "action=confirm&preview=a&preview=b",
        "action=preview&unexpected=value",
        "action=confirm&ministry_duids=1",
    ):
        with pytest.raises(ValueError):
            form_action(
                QueryDict(raw),
                preview_fields={"name", "ministry_duids"},
                multiple_fields={"ministry_duids"},
            )


def test_a_direct_save_is_admitted_only_where_the_editor_offers_it():
    """``save`` (#1404 PR 4) takes the preview's fields and a request key.

    An editor that does not save directly refuses it; one that does takes
    its multi-selects as a Review does, and the page's key must be a UUID.
    The change's key comes from the page's key and the posted values: the
    same save (even with a rotated CSRF token) has the same key, and a
    different change from the same page a key of its own (#1486 review).
    """
    key = uuid4()
    raw = f"action=save&request_key={key}&ministry_duids=1&ministry_duids=2"
    fields = {
        "preview_fields": {"ministry_duids"},
        "multiple_fields": {"ministry_duids"},
    }
    assert form_action(QueryDict(raw), **fields, direct=True) == "save"
    derived = direct_key(QueryDict(raw))
    assert derived != key
    assert direct_key(QueryDict(f"csrfmiddlewaretoken=t&{raw}")) == derived
    assert direct_key(QueryDict(raw.replace("=2", "=3"))) != derived
    assert direct_key(QueryDict(raw.replace(str(key), str(uuid4())))) != derived
    with pytest.raises(ValueError):
        form_action(QueryDict(raw), **fields)
    for bad in ("request_key=x", "request_key=", ""):
        with pytest.raises(ValueError):
            direct_key(QueryDict(f"action=save&{bad}"))
    # The key belongs to a save only; a Review or Apply never carries one.
    with pytest.raises(ValueError):
        form_action(
            QueryDict(f"action=preview&request_key={key}"), **fields, direct=True
        )


def test_confirmation_values_use_names_not_storage_repr():
    """Ministries and funds read by name, never DUID (#1123), and optional UUIDs
    never become labels."""
    assert (
        display_value("modules", ["census", "financial"])
        == "Census, Financial stewardship"
    )
    assert (
        display_value("ministry_duids", [1234], ministries=[("1234", "Choir")])
        == "Choir"
    )
    assert display_value("ministry_duids", [1234]) == "Unavailable"
    assert display_value("ministry_duids", []) == "None"
    assert display_value("content_versions", {"welcome": str(uuid4())}) == "Welcome"
    assert display_value("content_versions", {}) == "None"
    assert (
        display_value("share_options", [{"id": str(uuid4()), "label": "Bank check"}])
        == "Bank check"
    )
    assert display_value("share_options", []) == "None"
    assert display_value("additional_information", True) == "Yes"
    assert display_value("additional_information", False) == "No"
    assert display_value("financial", None) == "Not set"
    text = display_value(
        "financial", financial(), funds=[("1", "Offertory"), ("2", "Prior year")]
    )
    assert "Commitment period: 2055-01-01 through 2055-12-31" in text
    assert text.endswith("Comparison funds: Prior year") and "DUID" not in text
    # The retired overlap confirmation is not described (#2293).
    assert "overlap" not in text
    assert describe_changes({"name": "Before"}, {"name": "After"}) == [
        {"label": "Campaign name", "before": "Before", "after": "After"}
    ]


def test_form_groups_do_not_duplicate_hidden_or_module_controls():
    """The progressive template has one accessible control for each saved value."""
    form = CampaignForm()
    groups = form.general_fields + form.module_fields + form.financial_fields
    fields = [field.name for field in groups]
    assert len(fields) == len(set(fields))
    assert set(fields) == set(form.fields) - {
        "ministry_duids",
        "base_digest",
        "additional_information",
    }
    assert [field.name for field in form.module_fields] == [
        "census",
        "ministry",
        "financial_enabled",
    ]


def test_financial_fields_are_required_in_the_page_only_while_financial_is_on():
    """Every field clean() requires for Financial stewardship tells the page's
    complete gate the same (#563)."""
    form = CampaignForm()
    marked = {
        name
        for name, field in form.fields.items()
        if field.widget.attrs.get("data-required-when") == "financial_enabled=on"
    }
    assert marked == set(FINANCIAL_REQUIRED)
    # Not required in the markup: hidden, the module's fields are not sent.
    assert not any(form.fields[name].required for name in FINANCIAL_REQUIRED)
    # A prefixed form names its own module box.
    prefixed = CampaignForm(prefix="draft")
    assert (
        prefixed.fields["fund_duids"].widget.attrs["data-required-when"]
        == "draft-financial_enabled=on"
    )


def test_the_page_requires_what_clean_requires_for_financial_stewardship():
    """Leaving out any field the page marks is refused by the server too."""
    values = campaign(modules=["financial"], financial=financial())["values"]
    complete = initial_fields(values, digest="a" * 64)
    assert form_for(complete).is_valid()
    for name in FINANCIAL_REQUIRED:
        form = form_for(complete | {name: [] if name.endswith("duids") else ""})
        assert not form.is_valid()
        assert name in form.errors


def test_each_end_date_takes_its_start_as_its_minimum_in_the_page():
    """The page holds an end before its start (data-not-before, #563)."""
    form = CampaignForm(prefix="draft")
    for start, end in DATE_PAIRS:
        attrs = form.fields[end].widget.attrs
        assert attrs["data-not-before"] == f"id_draft-{start}"
        assert attrs["data-missing-hint"]
    # An empty period end keeps the financial fieldset's hint.
    assert "data-empty-hint" not in form.fields["end_date"].widget.attrs
    assert form.fields["financial_end"].widget.attrs["data-empty-hint"]
    # A live campaign's end date has its own live check instead.
    live = CampaignForm(whole=False)
    assert "data-not-before" not in live.fields["end_date"].widget.attrs


@pytest.mark.parametrize(
    ("changes", "field", "start"),
    [
        ({"end_date": "2054-10-01"}, "end_date", "The campaign end date must be"),
        ({"end_date": "2054-09-01"}, "end_date", "The campaign end date must be"),
        ({"financial_end": "2055-12-30"}, "financial_end", "A period spans"),
        ({"comparison_end": "2026-12-30"}, "comparison_end", "A period spans"),
    ],
)
def test_date_refusals_are_shown_at_the_end_field(changes, field, start):
    """An end before its start, or a period not one year long, is a field error.

    Never the generic message at the top of the page (#563).
    """
    values = campaign(modules=["financial"], financial=financial())["values"]
    complete = initial_fields(values, digest="a" * 64)
    assert complete["start_date"] == "2054-10-01"
    form = form_for(complete | changes)
    assert not form.is_valid()
    assert str(form.errors[field][0]).startswith(start)
    assert "__all__" not in form.errors


ROLE_CHOICES = [
    ("Chairperson", "Chairperson"),
    ("Member", "Member"),
    ("Staff", "Staff"),
]


def settings_form(values, *, previous=None, roles=("Chairperson", "Staff")):
    """Campaign settings' draft form, with the leader roles now in effect."""
    from parishkit.stewardship.accounts.campaign_forms import CampaignSettingsForm

    return CampaignSettingsForm(
        values,
        previous=previous,
        ministries=[("1", "First ministry"), ("2", "Second ministry")],
        funds=[("1", "Current fund"), ("2", "Comparison fund")],
        leader_choices=ROLE_CHOICES,
        leader_roles=roles,
    )


def test_leader_roles_are_written_only_when_they_change():
    """Keeping the roles in effect writes nothing; a change writes the list (#922)."""
    unchanged = settings_form(
        posted(ministry=True, ministry_leader_roles=["Staff", "Chairperson"])
    )
    assert unchanged.is_valid(), unchanged.errors
    assert "ministry_leader_roles" not in unchanged.values()
    changed = settings_form(
        posted(ministry=True, ministry_leader_roles=["Staff", "Member"])
    )
    assert changed.is_valid(), changed.errors
    assert changed.values()["ministry_leader_roles"] == ["Member", "Staff"]
    assert campaign_values(changed.values())


def test_leader_roles_need_one_role_and_only_offered_names():
    """Ministry stewardship needs a leader role; a posted name must be offered."""
    for roles in ([], ["Pastor"]):
        form = settings_form(posted(ministry=True, ministry_leader_roles=roles))
        assert not form.is_valid()
        assert "ministry_leader_roles" in form.errors
    # Without Ministry stewardship the roles are not asked about, and a saved
    # list is kept as it is.
    previous = campaign(ministry_leader_roles=["Member"])["values"]
    form = settings_form(posted(), previous=previous, roles=["Member"])
    assert form.is_valid(), form.errors
    assert form.values()["ministry_leader_roles"] == ["Member"]


def test_leader_roles_help_points_to_the_users_page():
    """Sign-in rules became users (#952); a leader signs in as a user."""
    assert "import leaders on the Users page" in str(LEADER_ROLES_HELP)
    assert "sign-in rule" not in str(LEADER_ROLES_HELP)


def test_leader_roles_review_lists_the_names():
    """The review shows the names, not a Python list."""
    assert (
        display_value("ministry_leader_roles", ["Chairperson", "Staff"])
        == "Chairperson, Staff"
    )
    assert describe_changes(
        {"ministry_leader_roles": ["Chairperson", "Staff"]},
        {"ministry_leader_roles": ["Chairperson"]},
    ) == [
        {
            "label": "Ministry leader roles",
            "before": "Chairperson, Staff",
            "after": "Chairperson",
        }
    ]
