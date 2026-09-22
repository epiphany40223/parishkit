"""Family activity racing a source refresh on an independent connection."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic, sleep

import pytest
from django.db import connection, connections, transaction
from django.db.models import F

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns import family_identity
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyEligibilityChange,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus

from .credential_builders import family_campaign, populate
from .lock_observer import backend_pid

pytestmark = pytest.mark.django_db(transaction=True)


def record_activity(identifier, pids):
    """Mirror authenticated_family's FamilyCampaign bump outside the work order."""
    try:
        with transaction.atomic():
            pids.put(backend_pid())
            FamilyCampaign.objects.filter(pk=identifier).update(
                last_activity_at=database_now(), version=F("version") + 1
            )
    finally:
        connections.close_all()


def blocked_or_done(pid, future, *, timeout=5):
    """Return once activity has either committed or become a real lock waiter.

    The unlocked read lets the activity commit at once; the locked read makes
    it wait for the refresh. Either outcome is observed, never assumed.
    """
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if future.done():
            return "committed"
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT wait_event_type='Lock' "
                "AND cardinality(pg_blocking_pids(pid))>0 "
                "FROM pg_stat_activity WHERE pid=%s",
                [pid],
            )
            if cursor.fetchone() == (True,):
                return "waiting"
        sleep(0.01)
    raise AssertionError("Family activity neither committed nor waited.")


def test_family_activity_between_refresh_read_and_write_keeps_versions(
    tmp_path, monkeypatch
):
    """Activity committing after reconcile's read cannot abort source promotion.

    Reconcile computes each FamilyCampaign version in memory. Without a row
    lock, an activity bump committed in between made bulk_update write the
    same version again and the SQL guard (23514) rolled back the refresh.
    """
    _, campaign, _, ring = family_campaign(tmp_path)
    family = FamilyCampaign.objects.get()
    before = family.version
    outcomes, pids = [], Queue()
    original = family_identity._now

    with ThreadPoolExecutor(max_workers=1) as executor:

        def interleave():
            """Run activity on another backend just after reconcile's read."""
            future = executor.submit(record_activity, family.pk, pids)
            outcomes.append(blocked_or_done(pids.get(timeout=5), future))
            outcomes.append(future)
            return original()

        monkeypatch.setattr(family_identity, "_now", interleave)
        populate(
            campaign,
            ring,
            [
                FamilyStatus(
                    1,
                    True,
                    True,
                    True,
                    False,
                    deliverability_reason="provider_suppressed",
                )
            ],
            generation=2,
        )
        outcomes[1].result(timeout=5)

    # The activity waited for the refresh commit, then advanced its version.
    assert outcomes[0] == "waiting"
    family.refresh_from_db()
    assert family.version == before + 2
    assert family.source_generation == 2 and not family.email_deliverable
    assert family.last_activity_at is not None
    # Eligibility history records the refresh's own version, not a reused one.
    assert (
        FamilyEligibilityChange.objects.filter(family=family)
        .order_by("family_version")
        .values_list("family_version", "source_generation")
        .last()
    ) == (before + 1, 2)
