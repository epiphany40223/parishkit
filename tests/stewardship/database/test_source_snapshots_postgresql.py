"""Real staged-corpus validation, deduplication and all-or-nothing source truth."""

import json
from dataclasses import replace
from itertools import batched

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.source import snapshots
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.leases import _now, acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import (
    SourceCurrent,
    SourceSnapshot,
    SourceSnapshotPin,
)
from parishkit.stewardship.source.snapshots import (
    PROMOTION_STATEMENT_SECONDS,
    begin_snapshot,
    complete_snapshot,
    finish_snapshot,
    promote_snapshot,
    reconstruct_snapshot,
    stage_entities,
    validate_snapshot,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS, SourceFamily
from parishkit.stewardship.storage import StorageInvariantError

from .source_builders import running_source_task, source_corpus

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only idle/empty migration seeds after disposable test flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def permit(*args):
    """Synthetic owning admission; runtime consumers supply concrete policy checks."""
    return True


def prepared(corpus=None, claim=None):
    """Stage complete synthetic provider evidence under a real source fence."""
    corpus = source_corpus() if corpus is None else corpus
    claim = claim or acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    for kind, entities in corpus.items():
        # Staging accepts bounded batches, as the real refresh sends them.
        for batch in batched(entities.items(), 500) if entities else [()]:
            stage_entities(
                snapshot.pk, claim, kind=kind, entities=dict(batch), admit=permit
            )
    snapshot = finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={"watermark": "synthetic-1"},
        admit=permit,
    )
    return snapshot, claim


def publish(snapshot, claim, reconcile=permit):
    """Keep synthetic reconciliation explicit instead of a production bypass default."""
    return promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=reconcile)


def test_staging_is_invisible_until_the_complete_corpus_promotes():
    """Staged rows never masquerade as truth; every collection switches together."""
    snapshot, claim = prepared()
    assert SourceCurrent.objects.get().snapshot_id is None
    with pytest.raises(InvalidSourcePayload, match="unavailable"):
        reconstruct_snapshot()
    promoted = publish(snapshot, claim)
    assert promoted.generation == 1
    assert reconstruct_snapshot() == source_corpus()
    assert SourceCurrent.objects.get().snapshot_id == snapshot.pk
    evidence = AuditContext.objects.get(
        event__event_type="source_promoted", event__subject_id=claim.task_id
    ).context
    assert evidence == {
        "before_version": 0,
        "after_version": 1,
        "count": 9,
        "source_fingerprint": promoted.content_digest,
        "outcome": "succeeded",
    }


def test_repeated_identical_refresh_reuses_all_payload_versions():
    """New manifests/maps do not cause duplicate unchanged entity payload rows."""
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    second, claim = prepared()
    publish(second, claim)
    for payload, membership in ENTITY_MODELS.values():
        assert payload.objects.count() == 1
        assert membership.objects.count() == 2
    assert reconstruct_snapshot(first.pk) == reconstruct_snapshot(second.pk)
    assert SourceSnapshot.objects.get(pk=second.pk).generation == 2
    # A first load counts every record as changed; an identical one, none (#242).
    assert first.cursor["changes"] == dict.fromkeys(ENTITY_MODELS, 1)
    assert second.cursor == {
        "watermark": "synthetic-1",
        "changes": dict.fromkeys(ENTITY_MODELS, 0),
    }


def test_changed_family_does_not_copy_unchanged_members_or_giving():
    """Version reuse follows entity content, not the parent snapshot's generation."""
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    second, claim = prepared(source_corpus(name="Changed"))
    publish(second, claim)
    assert SourceFamily.objects.count() == 2
    for kind, (payload, _) in ENTITY_MODELS.items():
        assert payload.objects.count() == (2 if kind == "family" else 1)
    assert reconstruct_snapshot(first.pk)["family"]["1"]["name"] == "Synthetic"
    assert reconstruct_snapshot()["family"]["1"]["name"] == "Changed"
    assert second.cursor["changes"] == dict.fromkeys(ENTITY_MODELS, 0) | {"family": 1}


def test_a_failed_change_count_never_fails_the_refresh(monkeypatch, caplog):
    """The display-only changed counts are omitted, with a WARNING, on failure.

    The failure is a real database error, so this also proves the savepoint
    keeps the refresh's own transaction usable for the ready transition.
    """
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    manifest = snapshots.snapshot_manifest

    def broken(snapshot_id):
        """Fail only the base comparison, with a genuine SQL error."""
        if snapshot_id == first.pk:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")
        return manifest(snapshot_id)

    monkeypatch.setattr(snapshots, "snapshot_manifest", broken)
    with caplog.at_level("WARNING", logger="parishkit.stewardship"):
        second, claim = prepared(source_corpus(name="Changed"))
    assert "changes" not in second.cursor
    assert publish(second, claim).state == "promoted"
    (warning,) = [
        r for r in caplog.records if r.getMessage() == "report_shaping_failed"
    ]
    # A classified category only; no exception text reaches the log line.
    assert warning.levelname == "WARNING" and not warning.exc_info


def test_failed_reconciliation_rolls_back_pointer_manifest_and_derived_records():
    """No Family/policy effect can commit without its coherent source promotion."""
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    second, claim = prepared(source_corpus(name="Changed"))

    def fail(snapshot):
        """A retained input pin stands in for a real derived-domain write."""
        SourceSnapshotPin.objects.create(
            snapshot=snapshot, parent_kind="operator", parent_id=claim.worker_id
        )
        return False

    with pytest.raises(StorageInvariantError, match="reconciliation"):
        publish(second, claim, fail)
    assert SourceCurrent.objects.get().snapshot_id == first.pk
    assert SourceSnapshot.objects.get(pk=second.pk).state == "ready"
    assert not SourceSnapshotPin.objects.exists()
    assert publish(second, claim).generation == 2


def test_a_second_prepared_snapshot_cannot_overwrite_its_now_stale_base():
    """Even the same live source worker must rebase a competing staging corpus."""
    first, claim = prepared()
    second, _ = prepared(source_corpus(name="Changed"), claim)
    publish(first, claim)
    with pytest.raises(InvalidSourcePayload, match="current-base"):
        publish(second, claim)
    assert SourceCurrent.objects.get().snapshot_id == first.pk


@pytest.mark.parametrize(
    "kind", ["member", "contact", "address", "roster", "pledge", "contribution"]
)
def test_missing_relationships_never_validate(kind):
    """Relationships are validated against this snapshot, including optional records."""
    corpus = source_corpus()
    field = {
        "member": "family_key",
        "contact": "owner_key",
        "address": "owner_key",
        "roster": "member_key",
        "pledge": "fund_key",
        "contribution": "family_key",
    }[kind]
    next(iter(corpus[kind].values()))[field] = "missing"
    with pytest.raises(InvalidSourcePayload, match="unresolved"):
        prepared(corpus)
    assert SourceCurrent.objects.get().snapshot_id is None
    assert SourceSnapshot.objects.get().state == "staging"


def test_staging_batch_retry_is_idempotent_but_changed_retry_is_rejected():
    """A resumed chunk cannot silently replace a previously observed entity."""
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    args = dict(kind="family", entities=source_corpus()["family"], admit=permit)
    assert stage_entities(snapshot.pk, claim, **args) == 1
    assert stage_entities(snapshot.pk, claim, **args) == 0
    args["entities"] = source_corpus(name="Changed")["family"]
    with pytest.raises(InvalidSourcePayload, match="inconsistent"):
        stage_entities(snapshot.pk, claim, **args)
    assert SourceFamily.objects.count() == 1


def test_completeness_counts_and_admission_fail_closed():
    """Source metadata is neither best-effort nor implicitly authorized."""
    claim = acquire_source(**running_source_task(), phase="full")
    with pytest.raises(PermissionError):
        begin_snapshot(claim, organization_id=100, admit=lambda *args: None)
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    with pytest.raises(InvalidSourcePayload, match="counts differ"):
        finish_snapshot(
            snapshot.pk,
            claim,
            expected_counts={kind: 1 for kind in ENTITY_MODELS},
            cursor={},
            admit=permit,
        )
    assert SourceSnapshot.objects.get().state == "staging"


def stage_only(corpus, claim):
    """Stage ``corpus`` under ``claim`` without finishing it; return the row."""
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    for kind, entities in corpus.items():
        if entities:
            stage_entities(
                snapshot.pk, claim, kind=kind, entities=entities, admit=permit
            )
    return snapshot


def test_validation_evidence_that_went_stale_is_never_frozen():
    """A refresh validates outside the work order and completes under it (#895).

    Validation only reads; completion writes what it derived. Membership that
    changed in between, or evidence altered on the way, is refused by the
    snapshot guard, which re-derives the counts and digest from the rows.
    """
    corpus = source_corpus()
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = stage_only(corpus, claim)
    options = dict(
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={"watermark": "synthetic-1"},
        admit=permit,
    )
    evidence = validate_snapshot(snapshot.pk, claim, **options)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "staging"
    with pytest.raises(TypeError, match="validated snapshot evidence"):
        complete_snapshot(snapshot.pk, claim, evidence.counts, admit=permit)
    tampered = replace(evidence, content_digest="0" * 64)
    with pytest.raises(IntegrityError, match="differs from its complete corpus"):
        complete_snapshot(snapshot.pk, claim, tampered, admit=permit)
    # A Family staged after validation: the old evidence no longer matches.
    stage_entities(
        snapshot.pk,
        claim,
        kind="family",
        entities={"9": {"name": "Late"}},
        admit=permit,
    )
    with pytest.raises(IntegrityError, match="differs from its complete corpus"):
        complete_snapshot(snapshot.pk, claim, evidence, admit=permit)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "staging"
    # Fresh evidence for the rows as they are now completes, as finishing does.
    options["expected_counts"]["family"] = 2
    ready = complete_snapshot(
        snapshot.pk,
        claim,
        validate_snapshot(snapshot.pk, claim, **options),
        admit=permit,
    )
    assert ready.state == "ready" and ready.counts["family"] == 2
    assert ready.cursor == {
        "watermark": "synthetic-1",
        "changes": dict.fromkeys(ENTITY_MODELS, 1) | {"family": 2},
    }


def test_validation_counts_changes_against_a_base_off_the_work_order():
    """Change counts against a promoted base are computed during validation,
    while another session holds the work-order lock exclusively (#895), and
    completion freezes them unchanged."""
    from .test_source_step_lock_postgresql import separate_session

    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    corpus = source_corpus(name="Changed")
    corpus["fund"]["7"] = {"name": "Second Fund"}
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = stage_only(corpus, claim)
    with separate_session() as session, session.transaction():
        session.execute("SELECT pg_advisory_xact_lock(736220, 1)")
        evidence = validate_snapshot(
            snapshot.pk,
            claim,
            expected_counts={kind: len(rows) for kind, rows in corpus.items()},
            cursor={"watermark": "synthetic-2"},
            admit=permit,
        )
    changes = dict.fromkeys(ENTITY_MODELS, 0) | {"family": 1, "fund": 1}
    assert json.loads(evidence.cursor)["changes"] == changes
    ready = complete_snapshot(snapshot.pk, claim, evidence, admit=permit)
    assert ready.cursor == {"watermark": "synthetic-2", "changes": changes}


def test_sql_cannot_fabricate_validated_completeness_evidence():
    """Database validation recomputes digests/counts rather than trusting a flag."""
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    with pytest.raises(IntegrityError, match="evidence"), transaction.atomic():
        SourceSnapshot.objects.filter(pk=snapshot.pk).update(
            version=F("version") + 1,
            state="ready",
            completed_at=_now(),
            counts={kind: 1 for kind in ENTITY_MODELS},
            content_digest="0" * 64,
            validation={"schema": "source-corpus-v1", "complete": True},
        )


def test_sql_cannot_promote_a_manifest_without_updating_the_pointer():
    """The deferred commit check enforces both halves of atomic promotion."""
    snapshot, _ = prepared()
    with pytest.raises(IntegrityError, match="commit together"), transaction.atomic():
        SourceSnapshot.objects.filter(pk=snapshot.pk).update(
            version=F("version") + 1,
            state="promoted",
            generation=1,
            promoted_at=_now(),
        )
    assert SourceSnapshot.objects.get().state == "ready"
    assert SourceCurrent.objects.get().snapshot_id is None


def test_current_and_protected_corpora_cannot_be_marked_compacted():
    """Compaction requires explicit ownership and cannot destroy current inputs."""
    snapshot, claim = prepared()
    publish(snapshot, claim)
    with pytest.raises(IntegrityError, match="protected"), transaction.atomic():
        SourceSnapshot.objects.filter(pk=snapshot.pk).update(
            version=F("version") + 1, compacted_at=_now()
        )
    assert reconstruct_snapshot() == source_corpus()


def estimated_rows(table, snapshot_id):
    """The planner's row estimate for one snapshot's membership scan."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"EXPLAIN (FORMAT JSON) SELECT * FROM {table} WHERE snapshot_id=%s",
            [snapshot_id],
        )
        return cursor.fetchone()[0][0]["Plan"]["Plan Rows"]


def test_ready_snapshot_is_planned_from_its_own_statistics():
    """Promotion must not plan a new snapshot from statistics that predate it.

    On a live host the statistics usually describe only earlier snapshots
    until autovacuum catches up. The planner then estimates one row for the
    new snapshot and chooses nested loops that are quadratic at a real
    parish's size, all while promotion holds the global work-order lock.
    """
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    tables = [membership._meta.db_table for _, membership in ENTITY_MODELS.values()]
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE " + ", ".join(tables))
    corpus = source_corpus()
    corpus["member"].update(
        {
            str(100 + index): {"name": "Member", "family_key": "1"}
            for index in range(2000)
        }
    )
    second, claim = prepared(corpus)
    member_table = ENTITY_MODELS["member"][1]._meta.db_table
    assert estimated_rows(member_table, second.pk) > 1000


def statement_timeout_ms():
    """The session's current statement timeout in milliseconds, as text."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT setting FROM pg_settings WHERE name='statement_timeout'")
        return cursor.fetchone()[0]


def test_promotion_bounds_each_statement_under_the_work_lock():
    """One pathological statement fails the attempt instead of stalling others."""
    snapshot, claim = prepared()
    seen = []

    def reconcile(value):
        """Observe the limit in force for the promotion's own statements."""
        seen.append(statement_timeout_ms())
        return True

    publish(snapshot, claim, reconcile=reconcile)
    assert seen == [str(PROMOTION_STATEMENT_SECONDS * 1000)]
    # SET LOCAL ends with the promotion's transaction.
    assert statement_timeout_ms() == "0"
