"""Bounded source staging retains per-row liveness without dynamic query planning."""

from time import monotonic

import pytest
from django.db import connection

from parishkit.stewardship.source.snapshots import stage_entities
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .test_source_payloads_postgresql import staging

pytestmark = pytest.mark.django_db(transaction=True)


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

    The broad 30-second local staging ceiling leaves the normal two-to-three-
    minute provider load as the dominant cost. Every 500-row batch retains its
    own transaction/fence checks rather than monopolizing the source write lock.
    This synthetic storage benchmark is not a full application load demonstration.
    """
    snapshot, claim = staging()
    started, longest = monotonic(), 0.0
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
            batch_started = monotonic()
            stage_entities(
                snapshot.pk,
                claim,
                kind=kind,
                entities=entities,
                admit=lambda action, row: True,
            )
            longest = max(longest, monotonic() - batch_started)
        assert membership.objects.filter(snapshot=snapshot).count() == 2000
    elapsed = monotonic() - started
    record_property("staging_records", 18000)
    record_property("staging_seconds", elapsed)
    record_property("longest_batch_seconds", longest)
    assert elapsed < 30, f"Reference source staging took {elapsed:.2f}s"
    assert longest < 10, f"One bounded staging batch took {longest:.2f}s"
