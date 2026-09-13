"""The shared source converter's exact output survives normalized SQL storage."""

import pytest
from test_parishsoft_source import initialized, page

from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.giving import load_giving
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    reconstruct_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from ..test_source_corpus import TODAY, source
from ..test_source_giving import contribution, pledge, window
from .source_builders import running_source_task

pytestmark = pytest.mark.django_db(transaction=True)


def permit(*args):
    """This storage integration has no runtime admission or provider credentials."""
    return True


def promote(data, *, giving=None):
    """Stage all converter collections and require SQL completeness/relationships."""
    corpus = normalize_core(data, as_of=TODAY)
    if giving is not None:
        corpus.update(pledge=giving.pledges, contribution=giving.contributions)
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=data.organization_id, admit=permit)
    for kind, entities in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=entities, admit=permit)
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={},
        admit=permit,
    )
    promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
    release_source(claim)
    return snapshot.pk, corpus


def test_core_round_trip_and_contact_only_change_reuses_other_payload_versions():
    """Independent contacts and unknown field coverage remain coherent after reload."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    data = source()
    first, original = promote(data)
    assert reconstruct_snapshot() == original
    counts = {
        kind: payload.objects.count() for kind, (payload, _) in ENTITY_MODELS.items()
    }
    data.members[3]["emailAddress"] = "updated@example.org"
    second, changed = promote(data)
    assert second != first and reconstruct_snapshot() == changed
    assert reconstruct_snapshot(first) == original
    for kind, (payload, _) in ENTITY_MODELS.items():
        assert payload.objects.count() == counts[kind] + (kind == "contact")


def test_selected_exact_giving_round_trips_with_all_core_relationships(tmp_path):
    """The financial adapter's real output satisfies normalized source SQL checks."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    data = source()
    client = initialized(tmp_path, [page([pledge()]), page([contribution()])])
    giving = load_giving(
        client, corpus=normalize_core(data, as_of=TODAY), window=window(), as_of=TODAY
    )
    snapshot, corpus = promote(data, giving=giving)
    assert reconstruct_snapshot(snapshot) == corpus
    assert corpus["pledge"]["1"]["amount"] == "1200.00"
    assert corpus["contribution"]["1"]["amount"] == "100.25"
