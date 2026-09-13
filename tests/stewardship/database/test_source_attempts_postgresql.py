"""Concrete source claims retain their configuration, credential and cursor proof."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.source.attempts import (
    begin_refresh_attempt,
    verify_refresh_attempt,
)
from parishkit.stewardship.source.credentials import SourceCredential
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshAttempt,
    SourceRefreshRequest,
)
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)

from ..configuration_factory import configuration_document, configuration_version
from ..policy_factory import address
from .campaign_builders import add_draft, change
from .source_builders import source_corpus
from .test_source_requests_postgresql import claim, command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def configured(tmp_path):
    """Install a real nonsecret document matching the synthetic private file bytes."""
    credential = SourceCredential(b"SYNTHETIC-PRIVATE-KEY")
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    document["sections"]["integrations"][0]["values"]["credential_fingerprint"] = (
        credential.fingerprint
    )
    version = configuration_version(document)
    store, actor = AuthorityStore(tmp_path, validate_sections), uuid4()
    prepare_initial_configuration(
        store,
        version,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return credential, store, version, actor


def setup(tmp_path):
    """Claim one real configured full request with its initial source ownership."""
    credential, store, version, actor = configured(tmp_path)
    execution = claim(command())
    with execution.effect():
        source_claim = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    return credential, execution, source_claim, store, version, actor


def stage(attempt, execution, source_claim, *, cursor_changes=None):
    """Use valid storage facts so completion tests isolate concrete owner proof."""
    snapshot = attempt.snapshot
    corpus = source_corpus()
    with execution.effect():
        for kind, values in corpus.items():
            stage_entities(
                snapshot.pk, source_claim, kind=kind, entities=values, admit=permit
            )
        cursor = refresh_cursor(
            snapshot_id=snapshot.pk,
            kind="full",
            started_at=snapshot.started_at,
            window_digest=attempt.request.window_digest,
            evidence={},
        )
        cursor.update(cursor_changes or {})
        return finish_snapshot(
            snapshot.pk,
            source_claim,
            expected_counts={kind: len(rows) for kind, rows in corpus.items()},
            cursor=cursor,
            admit=permit,
        )


def test_attempt_replay_binds_one_manifest_and_loaded_credential(tmp_path):
    """The same worker claim cannot open competing observations on replay."""
    credential, execution, source_claim, _, version, _ = setup(tmp_path)
    first = begin_refresh_attempt(execution, source_claim, credential)
    second = begin_refresh_attempt(execution, source_claim, credential)
    assert first.pk == second.pk and SourceSnapshot.objects.count() == 1
    assert first.configuration_id == version.version_id
    assert first.credential_fingerprint == credential.fingerprint
    assert first.task_fence == execution.claim.fence
    assert verify_refresh_attempt(first.pk, execution, source_claim).pk == first.pk
    assert "PRIVATE" not in str(SourceRefreshAttempt.objects.values().get())


def test_wrong_loaded_key_cannot_create_any_snapshot(tmp_path):
    """Loaded private bytes must match the applied provider receipt."""
    _, execution, source_claim, *_ = setup(tmp_path)
    with pytest.raises(PermissionError, match="credential"):
        begin_refresh_attempt(
            execution, source_claim, SourceCredential(b"DIFFERENT-PRIVATE-KEY")
        )
    assert (
        not SourceSnapshot.objects.exists()
        and not SourceRefreshAttempt.objects.exists()
    )


def test_another_execution_cannot_verify_an_attempt(tmp_path):
    """An attempt UUID alone cannot authorize another worker's source work."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    wrong = replace(source_claim, worker_id=uuid4())
    with pytest.raises(ValueError, match="exact"):
        verify_refresh_attempt(attempt.pk, execution, wrong)


def test_complete_bound_snapshot_can_promote_and_excludes_false_coalescing(tmp_path):
    """Promoted work cannot fulfill a later command during delayed Task completion."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    snapshot = stage(attempt, execution, source_claim)
    with execution.effect():
        promote_snapshot(snapshot.pk, source_claim, admit=permit, reconcile=permit)
    release_source(source_claim)
    execution.transition("retryable_failure", retry_seconds=1)
    assert command().request_id != attempt.request_id


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unknown"},
        {"window_digest": "0" * 64},
        {"watermark": "2020-01-01T00:00:00+00:00"},
        {"full_snapshot_id": str(uuid4())},
        {"full_started_at": "2020-01-01T00:00:00+00:00"},
    ],
)
def test_sql_cannot_complete_a_forged_cursor(tmp_path, changes):
    """SQL checks pre-read time, complete-full provenance and window identity."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    with pytest.raises(IntegrityError):
        stage(attempt, execution, source_claim, cursor_changes=changes)
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "staging"
    assert SourceCurrent.objects.get().snapshot_id is None


def test_a_generic_source_manifest_cannot_complete_a_real_refresh_without_attempt(
    tmp_path,
):
    """The generic staging substrate cannot bypass concrete refresh bindings."""
    _, execution, source_claim, *_ = setup(tmp_path)
    with execution.effect():
        snapshot = begin_snapshot(source_claim, organization_id=12345, admit=permit)
    with pytest.raises(IntegrityError), execution.effect():
        finish_snapshot(
            snapshot.pk,
            source_claim,
            expected_counts=dict.fromkeys(source_corpus(), 0),
            cursor={},
            admit=permit,
        )


def test_changed_campaign_blocks_attempt_verification_and_sql_promotion(tmp_path):
    """Current scope is checked independently by the service and completion SQL."""
    credential, execution, source_claim, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    stage(attempt, execution, source_claim)
    add_draft(store, version, actor)
    with pytest.raises(PermissionError):
        verify_refresh_attempt(attempt.pk, execution, source_claim)
    with pytest.raises(IntegrityError):
        promote_snapshot(
            attempt.snapshot_id, source_claim, admit=permit, reconcile=permit
        )
    assert SourceCurrent.objects.get().snapshot_id is None


def test_harmless_configuration_edit_preserves_historical_attempt_provenance(tmp_path):
    """A display-only edit does not discard a correctly scoped ongoing load."""
    credential, execution, source_claim, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    parish = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": parish["values"] | {"name": "Renamed Parish"},
            }
        ],
    )
    assert (
        verify_refresh_attempt(attempt.pk, execution, source_claim).configuration_id
        == version.version_id
    )


def test_raw_attempt_requires_work_scope(tmp_path):
    """Even otherwise valid attempt records require the owning work lock."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    request = SourceRefreshRequest.objects.get()
    with execution.effect():
        snapshot = begin_snapshot(
            source_claim, organization_id=request.organization_id, admit=permit
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceRefreshAttempt.objects.create(
            request=request,
            snapshot=snapshot,
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            configuration_id=request.configuration_id,
            credential_fingerprint=credential.fingerprint,
            actor_id=execution.claim.worker_id,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"credential_fingerprint": "0" * 64},
        {"task_fence": 99},
        {"request_id": uuid4()},
        {"configuration_id": uuid4()},
        {"actor_id": uuid4()},
    ],
)
def test_sql_rejects_wrong_attempt_bindings_even_inside_work_scope(tmp_path, changes):
    """A work lock cannot substitute for actual task/provider/configuration proof."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    request = SourceRefreshRequest.objects.get()
    with execution.effect():
        snapshot = begin_snapshot(
            source_claim, organization_id=request.organization_id, admit=permit
        )
    arguments = dict(
        request_id=request.pk,
        snapshot_id=snapshot.pk,
        task_id=execution.claim.run_id,
        task_fence=execution.claim.fence,
        configuration_id=request.configuration_id,
        credential_fingerprint=credential.fingerprint,
        actor_id=execution.claim.worker_id,
    )
    with pytest.raises(IntegrityError), execution.effect():
        SourceRefreshAttempt.objects.create(**(arguments | changes))
    assert not SourceRefreshAttempt.objects.exists()


def test_attempt_history_cannot_be_rewritten_or_downgraded(tmp_path):
    """Retained input evidence is immutable and blocks destructive downgrade."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as sql,
    ):
        sql.execute(
            "UPDATE stewardship_source_refresh_attempt SET credential_fingerprint=%s "
            "WHERE id=%s",
            ("0" * 64, attempt.pk),
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as sql,
    ):
        sql.execute(
            "DELETE FROM stewardship_source_refresh_attempt WHERE id=%s", (attempt.pk,)
        )
    targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError):
            MigrationExecutor(connection).migrate(
                [("stewardship_source", "0011_sourcerefreshattempt")]
            )
    finally:
        MigrationExecutor(connection).migrate(targets)
    assert (
        SourceRefreshAttempt.objects.get(pk=attempt.pk).credential_fingerprint
        == credential.fingerprint
    )
