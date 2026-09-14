"""Financial display exposes only scoped aggregates and the Family's own intent."""

from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.web.content import render_template
from parishkit.stewardship.web.presentation import parish_date

from .financial import (
    FREQUENCIES,
    MAX_PLEDGE_CENTS,
    SHARE_TEXT_LIMIT,
    household_pronoun,
)
from .financial_inputs import financial_definition


def option_labels(option, definition, *, parish_name):
    """Pre-render inert wording variants; the browser never evaluates a template."""
    start, end = definition.upcoming.start, definition.upcoming.end
    substitutions = {
        "parish_name": parish_name,
        "campaign_year": definition.campaign_year,
        "financial_start": parish_date(start),
        "financial_end": parish_date(end),
        "financial_period": f"{parish_date(start)} – {parish_date(end)}",
    }
    return {
        key: render_template(
            option.label, substitutions | {"pronoun": household_pronoun(count)}
        )
        for key, count in (("none", 0), ("one", 1), ("many", 2))
    }


def financial_presentation(inputs, prior, *, parish_name):
    """Keep unavailable source data distinct from blank new or prior pledge answers."""
    if inputs is None:
        return None
    definition = inputs.definition
    previous = prior.answers.get("financial") if prior else None
    answers = {
        "annual_pledge": previous["annual_pledge"] if previous else "",
        "frequency": previous["frequency"] if previous else "",
        "shares": dict(previous["shares"]) if previous else {},
    }
    options = [
        {
            "id": option.id,
            "free_text": option.free_text,
            "labels": option_labels(option, definition, parish_name=parish_name),
        }
        for option in definition.options
    ]
    removed = set(answers["shares"]) - {option.id for option in definition.options}
    unavailable_options = []
    if removed:
        # Structural edits are draft-only, but a repeated rehearsal can still
        # encounter removed options. Retain its own old choice until explicit
        # removal rather than silently erasing an immutable prior response.
        configuration = CampaignConfiguration.objects.get(
            configuration_id=prior.configuration_id, record_id=prior.campaign_id
        )
        old_definition = financial_definition(
            configuration.values, campaign_id=prior.campaign_id
        )
        unavailable_options = [
            {
                "id": option.id,
                "free_text": option.free_text,
                "labels": option_labels(
                    option, old_definition, parish_name=parish_name
                ),
            }
            for option in old_definition.options
            if option.id in removed
        ]
    return {
        "upcoming": {
            "start": definition.upcoming.start.isoformat(),
            "end": definition.upcoming.end.isoformat(),
            "label": (
                f"{parish_date(definition.upcoming.start)} – "
                f"{parish_date(definition.upcoming.end)}"
            ),
        },
        "comparison": {
            "start": definition.comparison.start.isoformat(),
            "end": definition.comparison.end.isoformat(),
            "label": (
                f"{parish_date(definition.comparison.start)} – "
                f"{parish_date(definition.comparison.end)}"
            ),
        },
        "year_label": definition.year_label,
        "pledge": inputs.pledge.document() | {"display": inputs.pledge.display},
        "contributions": inputs.contributions.document()
        | {"display": inputs.contributions.display},
        "observed_at": inputs.observation.observed_at.isoformat()
        if inputs.observation
        else None,
        "through_date": inputs.observation.through_date.isoformat()
        if inputs.observation
        else None,
        "options": options,
        "unavailable_options": unavailable_options,
        "answers": answers,
        "previously_submitted": previous is not None,
        "max_pledge_cents": MAX_PLEDGE_CENTS,
        "share_text_limit": SHARE_TEXT_LIMIT,
        "frequencies": dict(FREQUENCIES),
    }
