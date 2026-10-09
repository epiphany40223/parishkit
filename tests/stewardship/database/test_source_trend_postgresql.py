"""The full refreshes a load's drop check compares with (#387)."""

import json
from datetime import timedelta

import pytest
from django.db import connection, transaction

from parishkit.stewardship.source.leases import _now
from parishkit.stewardship.source.loading import DEFAULT_MAXIMUM_DROP_PERCENT
from parishkit.stewardship.source.refreshing import TREND_DAYS, _trend

from .test_source_compaction_postgresql import promote_history

pytestmark = pytest.mark.django_db(transaction=True)


def history(monkeypatch, *days_ago):
    """Promote one full refresh per age in days, oldest first."""
    with transaction.atomic():
        now = _now()
    plan = [(now - timedelta(days=days), index) for index, days in enumerate(days_ago)]
    return now, promote_history(monkeypatch, plan)


def raise_limit(snapshot):
    """Record that this refresh's load ran with a raised drop limit.

    The manifest is immutable to the application, so the schema owner writes
    the one field this check reads, with triggers off.
    """
    cursor = {"load": {"maximum_drop_percent": DEFAULT_MAXIMUM_DROP_PERCENT + 25}}
    with transaction.atomic(), connection.cursor() as db:
        db.execute("SET LOCAL session_replication_role = replica")
        db.execute(
            "UPDATE stewardship_source_snapshot SET cursor=%s::jsonb WHERE id=%s",
            [json.dumps(cursor), snapshot.pk],
        )
    snapshot.refresh_from_db()


def test_the_trend_is_the_newest_full_and_those_of_the_last_week(monkeypatch):
    """Older full refreshes fall out; the newest always counts."""
    now, (old, week, latest) = history(monkeypatch, TREND_DAYS + 3, 3, 1)
    trend = _trend(latest, now - timedelta(days=TREND_DAYS))
    assert [each.pk for each in trend] == [latest.pk, week.pk]
    # Even with no recent refresh, the newest full is the baseline.
    assert [each.pk for each in _trend(latest, now)] == [latest.pk]


def test_a_refresh_accepted_past_the_limit_starts_the_trend_again(monkeypatch):
    """Refreshes before an accepted large change are no longer compared with."""
    now, (before, accepted, latest) = history(monkeypatch, 5, 3, 1)
    raise_limit(accepted)
    since = now - timedelta(days=TREND_DAYS)
    assert [each.pk for each in _trend(latest, since)] == [latest.pk, accepted.pk]
    raise_limit(latest)
    assert [each.pk for each in _trend(latest, since)] == [latest.pk]
