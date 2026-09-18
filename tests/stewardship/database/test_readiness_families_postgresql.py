"""Readiness reads whole real campaign groups without creating delivery work."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.readiness_families import family_impact
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.storage import StorageInvariantError

from .credential_builders import family_campaign, populate
from .test_background_grants_postgresql import task_login
from .test_family_schedule_planning_postgresql import add_reminders

pytestmark = pytest.mark.django_db(transaction=True)


def test_real_web_reader_counts_every_page_and_binds_changed_source(tmp_path):
    """201 Families cross a page; no per-Family query or outbox allocation occurs."""
    store, campaign, actor, ring = family_campaign(tmp_path, count=201)
    add_reminders(store, campaign, actor)
    campaign.refresh_from_db()
    cutoff = campaign.active_configuration.starts_at + timedelta(days=5)
    with task_login(ServiceRole.WEB), work_transaction():
        with CaptureQueriesContext(connection) as queries:
            first = family_impact(campaign, cutoff=cutoff)
        assert first.counts.active == first.counts.messages == 201
        assert first.counts.coalesced_slots == 402
        # Inspect only this reader's relevant query shapes, not all queries in
        # authorization/locking helpers whose implementation can change freely.
        family_reads = [
            row["sql"]
            for row in queries
            if 'FROM "stewardship_family_campaign"' in row["sql"]
        ]
        assert len(family_reads) <= 3
        assert all("LIMIT 200" in sql for sql in family_reads)
        assert not any("code_ciphertext" in row["sql"] for row in queries)
        second = family_impact(campaign, cutoff=cutoff)
        assert second == first
    assert not ScheduleOccurrence.objects.exists()
    assert not OutboxMessage.objects.exists()

    populate(
        campaign,
        ring,
        [FamilyStatus(n, True, True, True, True) for n in range(1, 202)],
        generation=2,
    )
    with task_login(ServiceRole.WEB), work_transaction():
        newer = family_impact(campaign, cutoff=cutoff)
    assert newer.counts == first.counts
    assert newer.digest != first.digest


def test_due_boundary_changes_fingerprint_without_writing_any_work(tmp_path):
    """A preview just before a reminder cannot hide the newly due semantic slot."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    campaign.refresh_from_db()
    cutoff = campaign.active_configuration.starts_at + timedelta(days=2)
    with work_transaction():
        before = family_impact(campaign, cutoff=cutoff)
        after = family_impact(campaign, cutoff=cutoff + timedelta(days=1))
        closed = family_impact(campaign, cutoff=campaign.active_configuration.ends_at)
    assert before.counts.messages == after.counts.messages == 1
    assert before.counts.coalesced_slots == 0
    assert after.counts.coalesced_slots == 1
    assert before.digest != after.digest
    assert closed.counts.messages == 0 and closed.counts.skipped_slots == 4
    assert not ScheduleOccurrence.objects.exists()


def test_requires_owning_work_order_even_with_empty_population(tmp_path):
    _, campaign, _, _ = family_campaign(tmp_path, count=0)
    assert not FamilyCampaign.objects.exists()
    with pytest.raises(StorageInvariantError, match="ordered transaction"):
        family_impact(campaign, cutoff=campaign.active_configuration.starts_at)
