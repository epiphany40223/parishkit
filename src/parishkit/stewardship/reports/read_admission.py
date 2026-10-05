"""Classify report-read closure without changing export mutation admission."""

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.campaigns.single_campaign import not_current_refused

from .export_services import admit_campaign


def admit_report_read(campaign_id):
    """Keep unknown/global denials distinct from retained-campaign unavailability.

    Callers authenticate separately and repeat this check inside their response
    guard. Only failure classification reads extra lifecycle metadata; no report
    labels or values are loaded before the guard. A refusal always stays closed,
    even if admission reopens before these diagnostic queries finish.

    Until the single-campaign change (#145), reports show the current campaign
    only (navigation rule 10): any other campaign is refused with a
    ``UserFacingGone``, which every report view's ``denial()`` answers with
    the 410 page. Because the guards repeat this check, a report that loses
    its current campaign mid-request is refused too.
    """
    current = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    if current is None or current != campaign_id:
        raise not_current_refused()
    try:
        admit_campaign(campaign_id, mutating=False)
    except PermissionError:
        system_open = SystemConfiguration.objects.filter(
            active_configuration_id__isnull=False, restore_review_required=False
        ).exists()
        if not system_open or not Campaign.objects.filter(pk=campaign_id).exists():
            raise PermissionError("Campaign reporting is unavailable.") from None
        raise ReadUnavailable("Campaign information is unavailable.") from None
