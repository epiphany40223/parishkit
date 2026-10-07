"""Bounded source staging retains per-row liveness without dynamic query planning."""

from time import monotonic

import pytest
from django.db import connection

from parishkit.stewardship.source.snapshots import stage_entities
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .plan_work import analyze_all, rows_read_by
from .test_source_payloads_postgresql import staging

pytestmark = pytest.mark.django_db(transaction=True)
# Rows (and index entries) one 500-record staging batch may read, per record.
# With current statistics a batch reads about 3,500 plus twice the rows of
# its kind already staged (at most 1,500 here), at most about 13 a record; a
# per-record scan of the staged rows would read hundreds of thousands.
BATCH_READS_PER_RECORD = 30


def test_membership_guard_uses_static_typed_payload_queries():
    """Each branch may cache its query plan; liveness still uses the wall clock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_source_membership_guard()'::regprocedure)"
        )
        body = cursor.fetchone()[0]
    assert "EXECUTE format" not in body
    assert "clock_timestamp()" in body
    for kind in ENTITY_MODELS:
        assert f"FROM public.stewardship_source_{kind} WHERE id=NEW.payload_id" in body


def test_all_entity_kinds_stage_in_bounded_batches_at_reference_scale(record_property):
    """Measure 18,000 memberships and payloads without provider or promotion claims.

    Every 500-row batch retains its own transaction/fence checks rather than
    monopolizing the source write lock, and its work is bounded by the batch,
    not by what is already staged: the server-counted rows each batch reads
    stay within a fixed budget per staged record (#690). Wall-clock time is
    only recorded and guarded against catastrophe, because CI packs three
    PostgreSQL partitions onto each runner. This synthetic storage benchmark
    is not a full application load demonstration.
    """
    snapshot, claim = staging()
    started, longest, reads = monotonic(), 0.0, {}
    for kind, (model, membership) in ENTITY_MODELS.items():
        fields = {
            field.name
            for field in model._meta.fields
            if field.name.endswith("_key")
            and field.name != "source_key"
            or field.name == "owner_kind"
        }
        for first in range(1, 2001, 500):
            entities = {
                str(index): {
                    "name": "Synthetic reference-scale record",
                    **{
                        name: "family" if name == "owner_kind" else str(index)
                        for name in fields
                    },
                }
                for index in range(first, first + 500)
            }
            # Current planner statistics before each batch, so its row count
            # does not depend on when autovacuum last analyzed the growing
            # tables. One reading only: staging the same batch again is a
            # different measurement (every row is then already present).
            analyze_all()
            batch_started = monotonic()
            reads.setdefault(kind, []).append(
                rows_read_by(
                    lambda entities=entities, kind=kind: stage_entities(
                        snapshot.pk,
                        claim,
                        kind=kind,
                        entities=entities,
                        admit=lambda action, row: True,
                    )
                )
            )
            longest = max(longest, monotonic() - batch_started)
        assert membership.objects.filter(snapshot=snapshot).count() == 2000
    elapsed = monotonic() - started
    record_property("staging_records", 18000)
    record_property("staging_seconds", elapsed)
    record_property("longest_batch_seconds", longest)
    record_property("batch_rows_read", str(reads))
    # The fourth batch of a kind, staged beside 1,500 rows already there,
    # reads within the same per-record budget as the first: a lookup that
    # scanned the staged rows would read hundreds of thousands.
    budget = 500 * BATCH_READS_PER_RECORD
    assert all(max(counts) < budget for counts in reads.values()), reads
    assert elapsed < 120, f"Reference source staging took {elapsed:.2f}s"
