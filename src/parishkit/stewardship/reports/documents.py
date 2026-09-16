"""One exact-generation document loader shared by interactive and export readers."""

from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .inputs import DAY_FIELDS
from .participation import ParticipationDay, ParticipationDocument


def participation_document(facts, *, parish_name, browser_timezone, requested_at):
    """Detach one complete series while the caller retains its pin or read guard.

    Read only required source metadata, not its validation/cursor payload. The
    selected generation's configuration, not today's configuration, describes
    its name, timezone, enabled financial series and date interval.
    """
    projection = CampaignConfiguration.objects.get(pk=facts.timezone_configuration_id)
    source_as_of = SourceSnapshot.objects.values_list("promoted_at", flat=True).get(
        pk=facts.source_id
    )
    return ParticipationDocument(
        campaign_id=facts.campaign_id,
        fact_set_id=facts.pk,
        parish_name=parish_name,
        campaign_name=projection.name,
        population_scope=facts.population_scope,
        campaign_timezone=projection.timezone,
        browser_timezone=browser_timezone,
        source_generation=facts.source_generation,
        source_as_of=source_as_of,
        submission_watermark=facts.submission_watermark,
        requested_at=requested_at,
        first_date=facts.first_date,
        last_date=facts.last_date,
        financial_enabled=projection.values.get("financial") is not None,
        days=tuple(
            ParticipationDay(**row)
            for row in facts.days.order_by("local_date").values(*sorted(DAY_FIELDS))
        ),
    )
