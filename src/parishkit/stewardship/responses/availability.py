"""Public campaign status without credential lookup or household information."""

from dataclasses import dataclass

from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.lifecycle import portal_admitted
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.web.presentation import parish_date

from .page_content import public_substitutions, render_pages


@dataclass(frozen=True)
class UnavailablePage:
    """Fallback text and optional sanitized, campaign-wide parish instructions."""

    message: str
    content: str = ""


def unavailable_page(service):
    """Return only campaign-wide display text; route admission remains independent.

    Exact UTC interval checks use the same domain clock as login and Submit.
    Dates in the message are parish civil dates, not midnight converted into a
    different browser date. Setup/restore maintenance remains the outer gate.
    """
    configuration = coherent_configuration(service.store)
    campaign = configuration.current_campaign
    if campaign is None:
        return UnavailablePage("There is no campaign open for responses at this time.")
    definition = campaign.active_configuration
    now = _now()
    if (
        campaign.state in {"closed", "archived", "purging", "purged"}
        or now >= definition.ends_at
    ):
        return _page(
            configuration,
            definition,
            "post_end",
            "This parish's census / stewardship campaign has ended.",
        )
    if now < definition.starts_at:
        return _page(
            configuration,
            definition,
            "pre_start",
            "This parish's census / stewardship campaign starts on "
            + parish_date(definition.start_date)
            + " ("
            + definition.timezone
            + ").",
        )
    if not portal_admitted(campaign_facts(campaign, configuration), now):
        return UnavailablePage("The campaign is not open for responses at this time.")
    scope = CampaignCredentialState.objects.filter(campaign=campaign).first()
    if scope is None or scope.go_live_gate or scope.population_dirty:
        return UnavailablePage(
            "The campaign is being prepared. Please try again later."
        )
    if configuration.mode == "testing" and (
        scope.rehearsal_epoch_id is None or scope.rehearsal_epoch.state != "active"
    ):
        return UnavailablePage(
            "The Testing campaign is being prepared. Please try again later."
        )
    return None


def _page(configuration, definition, slot, fallback):
    """Keep date admission text even when an optional parish block is empty."""
    blocks = render_pages(
        definition.configuration_id,
        definition,
        {slot},
        public_substitutions(configuration.active_configuration.parish, definition),
    )
    return UnavailablePage(fallback, blocks.get(slot, ""))
