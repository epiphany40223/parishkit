"""Shared Phase 1A database-backed campaign scenarios, never operational permits."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import transition_campaign

from .test_campaign_postgresql import add_draft
from .test_policy_postgresql import initialized


def admit_test_work(*args):
    """Isolated storage fixture; deliberately cannot establish external readiness."""


def draft_campaign(tmp_path, row=None):
    """Create through the real installer and return the persisted current draft."""
    store, root, actor = initialized(tmp_path)
    result, row, _ = add_draft(store, root, actor, row)
    assert result.state == "applied"
    return store, Campaign.objects.get(pk=UUID(row["id"])), actor


@contextmanager
def campaign_clock(instant):
    """Replace only the domain test clock, never TaskRun's genuine lease clock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_campaign_now_v1()'::regprocedure)"
        )
        original = cursor.fetchone()[0]
        cursor.execute(
            "CREATE OR REPLACE FUNCTION stewardship_campaign_now_v1() "
            "RETURNS timestamptz "
            "LANGUAGE sql STABLE AS %s",
            ["SELECT '" + instant.isoformat() + "'::timestamptz"],
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(original)


def command(campaign, actor, action, **kwargs):
    """Fresh expected versions plus synthetic owning admission for storage tests."""
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    return transition_campaign(
        campaign_id=campaign.pk,
        action=action,
        request_id=uuid4(),
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **({"token_generation_id": uuid4()} if action is Action.ACTIVATE else {}),
        **kwargs,
    )
