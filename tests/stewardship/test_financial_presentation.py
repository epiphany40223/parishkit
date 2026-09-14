"""Financial presentation contains only exact aggregates and scoped Family intent."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import campaign_values
from parishkit.stewardship.responses.financial_inputs import (
    financial_definition,
    financial_inputs,
    giving_observation,
)
from parishkit.stewardship.responses.financial_presentation import (
    financial_presentation,
    option_labels,
)
from parishkit.stewardship.responses.inputs import definition_digest
from parishkit.stewardship.web.content import SHARE_PLACEHOLDERS, validate_share_label

from .financial_factory import CAMPAIGN, configuration, cursor, record
from .test_financial_answers import CHECK, OPTIONS, OTHER


def definition():
    """Use genuine campaign validation and stable synthetic options."""
    values = configuration() | {"share_options": [asdict(row) for row in OPTIONS]}
    return financial_definition(values, campaign_id=CAMPAIGN)


@pytest.mark.parametrize("available", [False, True])
def test_first_and_repeat_financial_presentation_does_not_prefill_comparison(available):
    """A past pledge is read-only context, not an automatically renewed new pledge."""
    config = definition()
    inputs = financial_inputs(
        config,
        giving_observation(cursor(config), config) if available else None,
        family_duid=1,
        pledges=[record("1200.00")],
        contributions=[record("9.99")],
    )
    form = financial_presentation(inputs, None, parish_name="Sample Parish")
    assert form["answers"] == {"annual_pledge": "", "frequency": "", "shares": {}}
    assert form["pledge"]["display"] == ("$1,200.00" if available else "Unavailable")
    assert form["observed_at"] == ("2026-10-15T04:00:00+00:00" if available else None)
    previous = {"annual_pledge": "0.00", "frequency": "", "shares": {OTHER: "Gift"}}
    form = financial_presentation(
        inputs,
        SimpleNamespace(answers={"financial": previous}),
        parish_name="Sample Parish",
    )
    assert form["answers"] == previous and form["previously_submitted"]
    form["answers"]["shares"].clear()
    assert previous["shares"] == {OTHER: "Gift"}
    assert financial_presentation(None, None, parish_name="Sample Parish") is None


def test_removed_prior_choice_is_explained_from_its_own_configuration(monkeypatch):
    """A removed option retains only this Family's prior label and note."""
    from parishkit.stewardship.responses import financial_presentation as owner

    old = configuration() | {"share_options": [asdict(row) for row in OPTIONS]}
    new = configuration() | {"share_options": [asdict(OPTIONS[0])]}
    config = financial_definition(new, campaign_id=CAMPAIGN)
    inputs = financial_inputs(config, None, family_duid=1, pledges=[], contributions=[])
    lookups = []

    def get(**query):
        """Record the exact retained prior response configuration reference."""
        lookups.append(query)
        return SimpleNamespace(values=old)

    monkeypatch.setattr(owner.CampaignConfiguration.objects, "get", get)
    prior = SimpleNamespace(
        configuration_id="own-configuration",
        campaign_id=CAMPAIGN,
        answers={
            "financial": {
                "annual_pledge": "0.00",
                "frequency": "",
                "shares": {OTHER: "Gift"},
            }
        },
    )
    form = financial_presentation(inputs, prior, parish_name="Sample Parish")
    assert lookups == [{"configuration_id": "own-configuration", "record_id": CAMPAIGN}]
    assert [row["id"] for row in form["options"]] == [CHECK]
    assert [row["id"] for row in form["unavailable_options"]] == [OTHER]
    assert form["answers"]["shares"] == {OTHER: "Gift"}


@pytest.mark.parametrize("placeholder", sorted(SHARE_PLACEHOLDERS))
def test_share_labels_admit_documented_financial_substitutions(placeholder):
    """Both YAML and the inert presenter support the same finite placeholder set."""
    label = "{{ " + placeholder + " }}"
    assert validate_share_label(label) == label
    values = configuration() | {
        "share_options": [{"id": CHECK, "label": label, "free_text": False}]
    }
    campaign_values(values)
    config = financial_definition(values, campaign_id=CAMPAIGN)
    labels = option_labels(config.options[0], config, parish_name="Sample Parish")
    assert all(labels.values()) and all("{{" not in value for value in labels.values())
    if placeholder == "pronoun":
        assert labels == {"none": "This household", "one": "I", "many": "We"}


@pytest.mark.parametrize(
    "label",
    ["{{ family_code }}", "{{ family_url }}", "{{ unknown }}", "{% include x %}"],
)
def test_yaml_share_labels_cannot_bypass_editor_template_validation(label):
    """Configuration publication cannot enable a private or executable placeholder."""
    values = configuration() | {
        "share_options": [{"id": CHECK, "label": label, "free_text": False}]
    }
    with pytest.raises(ConfigError):
        campaign_values(values)


@pytest.mark.parametrize(
    "change", ["year", "period", "option", "content", "limit", "frequency"]
)
def test_financial_definition_changes_invalidate_the_form(change, monkeypatch):
    """Displayed instructions and validation rules participate in stale admission."""
    from parishkit.stewardship.responses import inputs

    values = configuration()
    before = definition_digest(values)
    if change == "year":
        values["year_label"] = "Fiscal giving year"
    elif change == "period":
        values["financial"]["fund_duids"] = [5]
    elif change == "option":
        values["share_options"] = [asdict(OPTIONS[0])]
    elif change == "content":
        values["content_versions"]["financial"] = CHECK
    elif change == "limit":
        monkeypatch.setattr(inputs, "SHARE_TEXT_LIMIT", 1900)
    else:
        monkeypatch.setattr(inputs, "FREQUENCIES", {"annual": 1})
    assert definition_digest(values) != before


def test_unrelated_financial_definition_settings_do_not_invalidate():
    """Mail scheduling and disabled census content do not change a financial form."""
    values = configuration()
    before = definition_digest(values)
    values["email_schedule"] = "later"
    values["content_versions"]["census"] = CHECK
    assert definition_digest(values) == before


@pytest.mark.parametrize("label", [None, "Custom campaign year"])
def test_campaign_year_matches_admin_preview_pages_and_share_labels(monkeypatch, label):
    """The campaign year is not silently replaced by the upcoming pledge year."""
    from parishkit.stewardship.accounts.content_forms import sample_render
    from parishkit.stewardship.responses import page_content, presentation

    text = "{{ campaign_year }}: {{ financial_period }}"
    values = configuration() | {
        "year_label": label,
        "share_options": [{"id": CHECK, "label": text, "free_text": False}],
        "content_versions": {"financial": OTHER},
    }
    config = financial_definition(values, campaign_id=CAMPAIGN)
    money = financial_inputs(config, None, family_duid=1, pledges=[], contributions=[])
    parish = {
        "name": "Sample Parish",
        "website": "https://example.org/",
        "phone": "+12025550100",
    }
    html = "<p>" + text + "</p>"
    preview = sample_render(
        {"html": html, "text": text, "subject": None}, parish=parish, campaign=values
    )
    monkeypatch.setattr(
        page_content.ContentVersion.objects,
        "filter",
        lambda *args, **kwargs: [SimpleNamespace(slot="financial", html=html)],
    )
    page = presentation._page_content(
        SimpleNamespace(
            configuration=SimpleNamespace(parish=SimpleNamespace(**parish)),
            configuration_id=CHECK,
        ),
        SimpleNamespace(values=values, timezone=values["timezone"], record_id=CAMPAIGN),
        {},
        [],
        0,
        money,
    )
    assert page["financial"] == preview["html"]
    labels = option_labels(config.options[0], config, parish_name=parish["name"])
    assert set(labels.values()) == {preview["text"]}
    assert preview["text"].startswith((label or "2026") + ": January 1, 2027")
