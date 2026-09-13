"""Public campaign status without credential lookup or household information."""

from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.lifecycle import portal_admitted
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.web.presentation import parish_date


def unavailable_message(service):
    """Return only campaign-wide display text; route admission remains independent.

    Exact UTC interval checks use the same domain clock as login and Submit.
    Dates in the message are parish civil dates, not midnight converted into a
    different browser date. Setup/restore maintenance remains the outer gate.
    """
    configuration = coherent_configuration(service.store)
    campaign = configuration.current_campaign
    if campaign is None:
        return "There is no campaign open for responses at this time."
    definition = campaign.active_configuration
    now = _now()
    if (
        campaign.state in {"closed", "archived", "purging", "purged"}
        or now >= definition.ends_at
    ):
        return "This parish's census / stewardship campaign has ended."
    if now < definition.starts_at:
        return (
            "This parish's census / stewardship campaign starts on "
            + parish_date(definition.start_date)
            + " ("
            + definition.timezone
            + ")."
        )
    if not portal_admitted(campaign_facts(campaign, configuration), now):
        return "The campaign is not open for responses at this time."
    scope = CampaignCredentialState.objects.filter(campaign=campaign).first()
    if scope is None or scope.go_live_gate or scope.population_dirty:
        return "The campaign is being prepared. Please try again later."
    if configuration.mode == "testing" and (
        scope.rehearsal_epoch_id is None or scope.rehearsal_epoch.state != "active"
    ):
        return "The Testing campaign is being prepared. Please try again later."
    return None
