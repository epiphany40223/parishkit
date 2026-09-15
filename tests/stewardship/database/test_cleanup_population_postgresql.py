"""A population larger than the production batch budget exercises real cleanup."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCheckpoint,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.rehearsals import prepare_rehearsals

from .campaign_builders import campaign_clock
from .credential_builders import family_campaign
from .test_cleanup_tasks_postgresql import queued, run

pytestmark = pytest.mark.django_db(transaction=True)


def populated_cleanup(tmp_path, count):
    """Create synthetic credentials through the real bounded rehearsal owner."""
    _, campaign, _, rings = family_campaign(tmp_path, count=count)
    families = list(FamilyCampaign.objects.values_list("pk", flat=True))
    with campaign_clock(campaign.active_configuration.starts_at):
        for offset in range(0, len(families), 500):
            prepare_rehearsals(
                campaign_id=campaign.pk,
                family_ids=families[offset : offset + 500],
                general=rings.general,
                mac=rings.mac,
                public=rings.public,
                purpose=CampaignWorkKind.REHEARSAL,
                admit=lambda *args: True,
            )
        status = queued(SimpleNamespace(campaign=campaign))
    return status


def test_default_batch_cleans_more_than_one_population_window(tmp_path):
    """Real default-size batches make bounded progress across a bulk manifest."""
    status = populated_cleanup(tmp_path, 501)
    assert status.inventory_total == 1_002
    assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    checkpoints = list(
        ProductionCleanupCheckpoint.objects.filter(request=request)
        .order_by("sequence")
        .values_list("deleted_count", flat=True)
    )
    assert len(checkpoints) >= 3 and max(checkpoints) == 500
    assert sum(checkpoints) == 1_002
    assert request.state == "cleanup_complete"
