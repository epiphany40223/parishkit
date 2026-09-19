"""Classify report-read closure without changing export mutation admission."""

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

from .export_services import admit_campaign


def admit_report_read(campaign_id):
    """Keep unknown/global denials distinct from retained-campaign unavailability.

    Callers authenticate separately and repeat this check inside their response
    guard. Only failure classification reads extra lifecycle metadata; no report
    labels or values are loaded before the guard. A refusal always stays closed,
    even if admission reopens before these diagnostic queries finish.
    """
    try:
        admit_campaign(campaign_id, mutating=False)
    except PermissionError:
        system_open = SystemConfiguration.objects.filter(
            active_configuration_id__isnull=False, restore_review_required=False
        ).exists()
        if not system_open or not Campaign.objects.filter(pk=campaign_id).exists():
            raise PermissionError("Campaign reporting is unavailable.") from None
        raise ReadUnavailable("Campaign information is unavailable.") from None
