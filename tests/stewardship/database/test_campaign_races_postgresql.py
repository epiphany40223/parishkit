"""Independent connections cannot commit competing lifecycle confirmations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections

from parishkit.stewardship.accounts.installation_lock import ConfigurationBusy
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import CampaignTransition
from parishkit.stewardship.campaigns.runtime import (
    return_to_testing,
    transition_campaign,
)
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import admit_test_work, campaign_clock, command, draft_campaign
from .test_controls_resolutions_postgresql import close_campaign
from .test_exceptional_end_postgresql import complete_empty_catchup

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "action",
    [
        Action.ACTIVATE,
        Action.WITHDRAW,
        Action.ARCHIVE,
        Action.UNARCHIVE,
        Action.RETURN_TESTING,
    ],
)
def test_competing_confirmations_have_one_winner(tmp_path, action):
    """Global/session serialization and optimistic versions agree across connections."""
    _, campaign, actor = draft_campaign(tmp_path)
    instant = campaign.active_configuration.starts_at - timedelta(days=1)
    with campaign_clock(instant):
        if action is not Action.ACTIVATE:
            command(campaign, actor, Action.ACTIVATE)
        if action is Action.WITHDRAW:
            complete_empty_catchup(campaign, actor)
        elif action in {Action.ARCHIVE, Action.UNARCHIVE, Action.RETURN_TESTING}:
            close_campaign(campaign, actor)
            if action in {Action.UNARCHIVE, Action.RETURN_TESTING}:
                command(campaign, actor, Action.ARCHIVE)
        campaign.refresh_from_db()
        runtime = SystemConfiguration.objects.get()
        count = CampaignTransition.objects.count()
        barrier = Barrier(2)

        def confirm(_):
            """Two browsers submit one displayed version under different request IDs."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                shared = dict(
                    campaign_id=campaign.pk,
                    request_id=uuid4(),
                    expected_runtime_version=runtime.version,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                )
                if action is Action.RETURN_TESTING:
                    return_to_testing(**shared)
                else:
                    transition_campaign(
                        **shared,
                        expected_version=campaign.version,
                        action=action,
                        token_generation_id=uuid4()
                        if action is Action.ACTIVATE
                        else None,
                        reason="reviewed" if action is Action.WITHDRAW else "",
                    )
                return "committed"
            except (ConfigurationBusy, StaleRecordError):
                return "retry"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, range(2)))
    assert sorted(results) == ["committed", "retry"]
    assert SystemConfiguration.objects.get().version == runtime.version + 1
    assert CampaignTransition.objects.count() == count + (
        action is not Action.RETURN_TESTING
    )
