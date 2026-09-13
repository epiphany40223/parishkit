"""Source and local activity atomically reconcile seeded scope and review history."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F

from parishkit.stewardship.accounts.chair_models import (
    ChairAssignmentReview,
    ChairReconciliation,
    ChairSeedEvidence,
)
from parishkit.stewardship.accounts.chair_reconciliation import reconcile_source_chairs
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy_models import AssignmentOverlay
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.leases import SourceFenceLost, release_source
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshots import promote_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from ..policy_factory import address, assignment
from ..test_ministry_activity import activity
from ..test_source_corpus import source
from .campaign_builders import change, initialized
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_current_chair_postgresql import publish, rows
from .test_source_families_postgresql import (
    prepare,
    source_singletons,  # noqa: F401
)
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def configured(tmp_path, *, manual_scope=True, manual_role=False):
    """Start with an explicitly selected seed and an independent manual scope."""
    from parishkit.stewardship.accounts.policy_models import MinistryAssignment

    leader = address("valid@example.org", ("ministry_leader",), seeded=True)
    if manual_role:
        leader["values"]["grants"]["ministry_leader"]["manual"] = str(uuid4())
    store, _, actor = initialized(
        tmp_path,
        [
            address(),
            leader,
            assignment("valid@example.org", 4, seeded=True),
            *([assignment("valid@example.org", 8)] if manual_scope else []),
        ],
    )
    snapshot = publish(source())
    seed = MinistryAssignment.objects.get(source="chair-seed")
    # Only a schema-owner fixture can supply prior Admin-confirmed evidence.
    with work_transaction():
        ChairSeedEvidence.objects.create(
            assignment=seed,
            assignment_record_id=seed.record_id,
            snapshot=snapshot,
            organization_id=12345,
            member_duid=3,
            roster_keys=[row[-1] for row in rows()],
            actor_id=actor,
        )
    publish(source())
    assert AssignmentOverlay.objects.get().active
    assert not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    return store, seed, actor


@pytest.mark.parametrize(
    "manual_scope,manual_role", [(True, False), (False, False), (False, True)]
)
def test_next_authorization_preserves_exact_manual_provenance(
    tmp_path, manual_scope, manual_role
):
    """Fresh authorization sees seed loss without deleting independent scope or role."""
    from parishkit.stewardship.accounts.policy import current_principal

    from .test_policy_postgresql import user

    store, _, _ = configured(
        tmp_path, manual_scope=manual_scope, manual_role=manual_role
    )
    account = user("valid@example.org")
    before = current_principal(store, account.pk)
    assert "ministry_leader" in before.roles and 4 in before.ministries
    data = source()
    data.members[3]["emailAddress"] = ""
    publish(data)
    after = current_principal(store, account.pk)
    assert ("ministry_leader" in after.roles) == (manual_scope or manual_role)
    assert after.ministries == (frozenset({8}) if manual_scope else frozenset())
    publish(source())
    assert current_principal(store, account.pk) == before


def test_source_disappearance_repetition_and_return_preserve_review_episodes(tmp_path):
    """Repeat imports update one task; returning identity closes it with audit."""
    store, seed, _ = configured(tmp_path)
    document = store.active()
    initial_count = ChairAssignmentReview.objects.count()
    data = source()
    data.members[3]["emailAddress"] = ""
    first = publish(data)
    overlay = AssignmentOverlay.objects.get()
    task = ChairAssignmentReview.objects.get(closed_by__isnull=True)
    assert not overlay.active and overlay.reason == "relationship_missing"
    assert overlay.source_snapshot_id == task.latest_by.snapshot_id == first.pk
    second = publish(data)
    task.refresh_from_db()
    assert task.latest_by.snapshot_id == second.pk
    assert ChairAssignmentReview.objects.count() == initial_count + 1
    assert task.opened_by.snapshot_id == first.pk
    restored = publish(source())
    task.refresh_from_db()
    assert task.close_reason == "relationship_returned"
    assert task.closed_by.snapshot_id == restored.pk
    assert AssignmentOverlay.objects.get().active
    assert store.active() == document
    assert AssignmentOverlay.objects.count() == 1  # No overlay for manual scope.
    assert task.assignment_record_id == seed.record_id
    assert AuditEvent.objects.filter(event_type="chair_reconciled").count() == (
        ChairReconciliation.objects.count()
    )


@pytest.mark.usefixtures("config_role")
def test_local_activity_uses_real_restricted_installer_and_restores_same_seed(tmp_path):
    """Policy activation needs no census grants and never changes source truth."""
    store, seed, actor = configured(tmp_path)
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    override = activity(ministry_duid=4)
    request = record_request(
        base_digest=store.active().digest,
        patch=[{"operation": "add", "section": "ministries", **override}],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
        admit=lambda: True,
    )
    with as_config_installer():
        admit_configuration_database()
        result = install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        )
        assert result.state == "applied"
    overlay = AssignmentOverlay.objects.get(assignment_record_id=seed.record_id)
    review = ChairAssignmentReview.objects.get(closed_by__isnull=True)
    assert not overlay.active and overlay.reason == "ministry_inactive"
    assert review.latest_by.activation_id is not None
    assert SourceCurrent.objects.get().snapshot_id == snapshot_id
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "remove", "section": "ministries", "id": override["id"]}],
        ).state
        == "applied"
    )
    overlay.refresh_from_db()
    review.refresh_from_db()
    assert overlay.active and review.close_reason == "relationship_returned"
    assert SourceCurrent.objects.get().snapshot_id == snapshot_id


def test_explicit_assignment_removal_closes_open_review_without_a_new_grant(tmp_path):
    """Review closure follows applied policy, not a guessed replacement."""
    store, seed, actor = configured(tmp_path)
    data = source()
    data.members[3]["emailAddress"] = ""
    publish(data)
    review = ChairAssignmentReview.objects.get(closed_by__isnull=True)
    result = change(
        store,
        store.active(),
        actor,
        [{"operation": "remove", "section": "login_rules", "id": str(seed.record_id)}],
    )
    assert result.state == "applied"
    review.refresh_from_db()
    assert review.close_reason == "assignment_removed"
    assert not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    assert not AssignmentOverlay.objects.get().active


@pytest.mark.parametrize("failure", ["omitted", "later_failure", "wrong_decision"])
def test_incomplete_promotion_never_commits_pointer_or_partial_policy(
    tmp_path, failure
):
    """SQL verifies required effects, even if a caller falsely claims completion."""
    configured(tmp_path)
    previous = SourceCurrent.objects.get().snapshot_id
    receipts = ChairReconciliation.objects.count()
    data = source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)

    def effects(value):
        """Exercise failures after the source pointer changes inside its transaction."""
        if failure == "omitted":
            return True
        if failure == "wrong_decision":
            runtime = SystemConfiguration.objects.get()
            ChairReconciliation.objects.create(
                configuration_id=runtime.active_configuration_id,
                snapshot_id=value.pk,
                source_owner_id=claim.task_id,
                source_fence=claim.fence,
                actor_id=claim.worker_id,
                correlation_id=TaskRun.objects.get(pk=claim.task_id).correlation_id,
                decisions=[],
            )
        reconcile_source_chairs(value.pk, claim, campaign_id=None)
        raise RuntimeError("Synthetic later effect failure")

    error = RuntimeError if failure == "later_failure" else IntegrityError
    with pytest.raises(error), work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    assert SourceCurrent.objects.get().snapshot_id == previous
    assert AssignmentOverlay.objects.get().active
    assert ChairReconciliation.objects.count() == receipts
    assert not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    release_source(claim)


def test_seeded_activation_cannot_skip_real_effects(tmp_path, monkeypatch):
    """The deferred activation constraint replaces the temporary blanket barrier."""
    store, _, actor = configured(tmp_path)
    previous = SystemConfiguration.objects.get().active_configuration_id
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.chair_reconciliation.reconcile_configuration_chairs",
        lambda activation: None,
    )
    with pytest.raises(IntegrityError, match="reconciliation"):
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "ministries",
                    **activity(ministry_duid=4),
                }
            ],
        )
    assert SystemConfiguration.objects.get().active_configuration_id == previous
    assert AssignmentOverlay.objects.get().active


def test_reconciliation_requires_outer_owner_and_live_source_fence(tmp_path):
    """Readiness, guessed worker IDs and stale evidence cannot authorize writes."""
    configured(tmp_path)
    snapshot, claim = prepare(source())
    with pytest.raises(StorageInvariantError, match="ordered transaction"):
        reconcile_source_chairs(snapshot.pk, claim, campaign_id=None)
    with pytest.raises(SourceFenceLost), work_transaction():
        reconcile_source_chairs(
            snapshot.pk, replace(claim, worker_id=uuid4()), campaign_id=None
        )
    with (
        pytest.raises(StorageInvariantError, match="current refresh"),
        work_transaction(),
    ):
        reconcile_source_chairs(snapshot.pk, claim, campaign_id=None)
    release_source(claim)


def test_exact_effect_retry_has_one_receipt_review_and_audit(tmp_path):
    """Repeating an owning effect inside promotion never duplicates its history."""
    configured(tmp_path)
    data = source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)

    def effects(value):
        """Exercise the legitimate idempotency branch with the same current fences."""
        first = reconcile_source_chairs(value.pk, claim, campaign_id=None)
        assert reconcile_source_chairs(value.pk, claim, campaign_id=None).pk == first.pk
        assert (
            first.correlation_id == TaskRun.objects.get(pk=claim.task_id).correlation_id
        )
        return True

    with work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    release_source(claim)
    receipt = ChairReconciliation.objects.get(snapshot=snapshot)
    assert ChairAssignmentReview.objects.filter(opened_by=receipt).count() == 1
    assert AuditEvent.objects.filter(pk=receipt.pk).count() == 1


def test_closed_reviews_cannot_be_reopened_or_deleted(tmp_path):
    """Automatic return closes an episode permanently; later loss opens another."""
    configured(tmp_path)
    review = ChairAssignmentReview.objects.get()
    with pytest.raises(IntegrityError, match="Closed Chair"), transaction.atomic():
        ChairAssignmentReview.objects.filter(pk=review.pk).update(
            version=F("version") + 1, close_reason="assignment_removed"
        )
    with (
        pytest.raises(IntegrityError, match="cannot be deleted"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_chair_review WHERE id=%s", [review.pk])


def test_source_promotion_and_activity_activation_serialize_exact_inputs(tmp_path):
    """An installer waiting behind promotion uses the newly committed source."""
    store, _, actor = configured(tmp_path)
    data = source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)
    promoted, release, installing = Event(), Event(), Event()

    def source_writer():
        """Hold the actual source transaction after its effects and before commit."""
        try:

            def effects(value):
                """Pause only after the new source and its complete effects exist."""
                reconcile_source_chairs(value.pk, claim, campaign_id=None)
                promoted.set()
                assert release.wait(10)
                return True

            with work_transaction():
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
            release_source(claim)
        finally:
            connections.close_all()

    def policy_writer():
        """Use real intake, YAML publication and activation on another session."""
        try:
            installing.set()
            return change(
                store,
                store.active(),
                actor,
                [
                    {
                        "operation": "add",
                        "section": "ministries",
                        **activity(ministry_duid=4),
                    }
                ],
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        source_result = pool.submit(source_writer)
        try:
            assert promoted.wait(10)
            policy_result = pool.submit(policy_writer)
            assert installing.wait(5)
            assert not policy_result.done()
        finally:
            release.set()
        source_result.result(timeout=10)
        assert policy_result.result(timeout=10).state == "applied"
    runtime = SystemConfiguration.objects.get()
    receipt = ChairReconciliation.objects.get(
        configuration_id=runtime.active_configuration_id, snapshot_id=snapshot.pk
    )
    assert receipt.activation_id is not None
    overlay = AssignmentOverlay.objects.get()
    assert not overlay.active and overlay.reason == "ministry_inactive"
    assert overlay.source_snapshot_id == snapshot.pk
    assert ChairAssignmentReview.objects.get(closed_by__isnull=True).latest_by_id == (
        receipt.pk
    )
