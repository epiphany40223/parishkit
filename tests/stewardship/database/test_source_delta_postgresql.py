"""A staged delta cursor advances only with a whole coherent promotion."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_parishsoft_source import source as client_factory

from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.delta import load_delta_source
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.rejection import reject_snapshot
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    reconstruct_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.windows import RefreshWindow

from ..test_source_corpus import source
from ..test_source_delta import household
from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def stage(snapshot, claim, corpus, cursor):
    """Use real complete counts, membership maps and SQL referential validation."""
    for kind, rows in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=rows, admit=permit)
    return finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor=cursor,
        admit=permit,
    )


@pytest.mark.parametrize("publish", [False, True])
def test_delta_cursor_and_contacts_follow_only_atomic_current_pointer(
    tmp_path, publish
):
    """Rejected later cursors cannot cause the scheduler to skip change indications."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    selected = RefreshWindow(uuid4(), ())
    old = acquire_source(**running_source_task(), phase="full")
    first = begin_snapshot(old, organization_id=5, admit=permit)
    day = first.started_at.date()
    original = normalize_core(source(), as_of=day)
    first_cursor = refresh_cursor(
        snapshot_id=first.pk,
        kind="full",
        started_at=first.started_at,
        window_digest=selected.digest,
        evidence={
            "giving_as_of_date": day.isoformat(),
            "anonymous_pledges": 0,
            "anonymous_contributions": 0,
        },
    )
    first = stage(first, old, original, first_cursor)
    promote_snapshot(first.pk, old, admit=permit, reconcile=permit)
    release_source(old)
    claim = acquire_source(**running_source_task(), phase="delta")
    second = begin_snapshot(claim, organization_id=5, admit=permit)
    client = client_factory(
        tmp_path,
        [
            [{"organizationID": 5}],
            [
                {
                    "family_DUID": 1,
                    "currentParishID": 5,
                    "previousParishID": 5,
                    "logDate": datetime.now(UTC).isoformat(),
                }
            ],
            *household(member_change={"emailAddress": "changed@example.org"}),
            [{"famGroupID": 7, "famGroup": "Active"}],
        ],
    )
    loaded = load_delta_source(
        client,
        base=reconstruct_snapshot(),
        base_cursor=first_cursor,
        window=selected,
        started_at=second.started_at,
        as_of=day,
        previous_full_counts=first.counts,
    )
    second_cursor = refresh_cursor(
        snapshot_id=second.pk,
        kind="delta",
        started_at=second.started_at,
        window_digest=selected.digest,
        evidence=loaded.evidence,
        base_cursor=first_cursor,
    )
    stage(second, claim, loaded.corpus, second_cursor)
    assert (
        SourceCurrent.objects.select_related("snapshot").get().snapshot.cursor
        == first_cursor
    )
    assert reconstruct_snapshot() == original
    if publish:
        promote_snapshot(second.pk, claim, admit=permit, reconcile=permit)
        assert reconstruct_snapshot() == loaded.corpus
        assert (
            SourceCurrent.objects.select_related("snapshot").get().snapshot.cursor
            == second_cursor
        )
        assert second_cursor["full_snapshot_id"] == str(first.pk)
    else:
        reject_snapshot(second.pk, claim, admit=permit)
        assert reconstruct_snapshot() == original
        assert (
            SourceCurrent.objects.select_related("snapshot").get().snapshot.cursor
            == first_cursor
        )
