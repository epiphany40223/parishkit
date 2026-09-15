"""Production cleanup selects exact Testing identities without deleting anything."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.cleanup_catalog import (
    CleanupCategory as Category,
)
from parishkit.stewardship.campaigns.cleanup_catalog import (
    inventory_queries,
    iter_inventory,
    summarize_targets,
)
from parishkit.stewardship.campaigns.cleanup_manifest import (
    delivery_summary,
    seal_manifest,
)
from parishkit.stewardship.campaigns.cleanup_manifest import (
    testing_summary as capture_summary,
)
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCodeReservation,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupManifest,
    ProductionCleanupTarget,
)
from parishkit.stewardship.campaigns.production_storage import (
    begin_transition,
)
from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.storage import StorageInvariantError

from ..test_outbox_validation import rendering
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def captured(campaign_id):
    """Collect synthetic target IDs while retaining required inventory work order."""
    with work_transaction():
        targets = tuple(iter_inventory(campaign_id))
        summary = summarize_targets(targets)
    return targets, summary


def test_catalog_covers_all_categories_and_requires_owning_work_order(response_service):
    """An unordered caller cannot race census writes while capturing a manifest."""
    with pytest.raises(StorageInvariantError):
        inventory_queries(response_service.campaign.pk)
    with work_transaction():
        queries = inventory_queries(response_service.campaign.pk)
        assert set(queries) == set(Category)
        with pytest.raises(TypeError):
            queries[Category.SUBMISSION] = None
        with pytest.raises(ValueError, match="canonical campaign"):
            inventory_queries(str(response_service.campaign.pk))


def test_exact_inventory_lookup_is_inlined_and_target_protection_is_indexed(
    response_service,
):
    """Per-row fencing must not rematerialize every campaign inventory branch."""
    import json

    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (FORMAT JSON) SELECT target_id "
            "FROM stewardship_cleanup_inventory_v1(%s) "
            "WHERE category='submissions' AND target_id=%s",
            [response_service.campaign.pk, uuid4()],
        )
        plan = json.dumps(cursor.fetchone()[0])
        assert '"Node Type": "Function Scan"' not in plan
        assert "stewardship_rehearsal_credential" not in plan
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname='production_target_lookup'"
        )
        assert "(category, target_id)" in cursor.fetchone()[0]


def test_selection_keeps_same_membership_after_epoch_invalidation(response_service):
    """The invalidation transaction cannot hide the test answers it must clean up."""
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Disposable test answer"
    response = submit(response_service, form, answers).submission
    targets, summary = captured(response_service.campaign.pk)
    assert (Category.SUBMISSION, response.pk) in {
        (target.category, target.identifier) for target in targets
    }
    assert summary.counts["submissions"] == 1
    assert summary.counts["baselines"] >= 1
    assert summary.counts["proposals"] >= 1
    assert summary.counts["submission_receipts"] == 1
    assert summary.counts["session_data"] == summary.counts["family_sessions"]
    assert summary.counts["rehearsal_credentials"] >= 1
    before = {
        model: set(model.objects.values_list("pk", flat=True))
        for model in (Submission, FamilyCampaign, RehearsalCodeReservation, AuditEvent)
    }
    invalidate_rehearsal(
        campaign_id=response_service.campaign.pk, admit=lambda *args: True
    )
    later, later_summary = captured(response_service.campaign.pk)
    assert targets == later and summary == later_summary
    for model, identifiers in before.items():
        assert identifiers <= set(model.objects.values_list("pk", flat=True))
    assert captured(uuid4())[0] == ()


def test_live_response_and_credentials_are_not_cleanup_targets(live_response_service):
    """A populated live campaign is not selectable even with a known campaign UUID."""
    form, answers = form_and_answers(live_response_service)
    answers["testing_acknowledged"] = False
    response = submit(live_response_service, form, answers).submission
    assert response.mode == "live"
    targets, summary = captured(live_response_service.campaign.pk)
    assert not targets and summary.total == 0
    assert Submission.objects.filter(pk=response.pk).exists()


def mixed_mail(response_service):
    """Create actual outbox journals for all three immutable delivery routes."""
    campaign = response_service.campaign
    configuration_id = campaign.active_configuration.configuration_id
    parish_id = Parish.objects.get(configuration_id=configuration_id).pk
    identifiers = {}
    for routing in ("testing_override", "production", "operational"):
        operational = routing == "operational"
        message = create_message(
            identity=DeliveryIdentity(
                scope_id=parish_id if operational else campaign.pk,
                campaign_id=None if operational else campaign.pk,
                semantic_key=uuid4(),
                mode="production" if routing == "production" else "testing",
                routing=routing,
                purpose="operational" if operational else "weekly_digest",
            ),
            render=rendering(configuration_id=configuration_id),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            command_id=uuid4(),
            admit=lambda *args: True,
        )
        identifiers[routing] = message
    return identifiers


def test_outbox_inventory_excludes_production_and_operational_routes(response_service):
    """Global Testing mode alone never makes operational or live delivery disposable."""
    identifiers = mixed_mail(response_service)
    targets, summary = captured(response_service.campaign.pk)
    assert {
        target.identifier for target in targets if target.category is Category.OUTBOX
    } == {identifiers["testing_override"].message_id}
    assert summary.counts["outbox_messages"] == 1
    assert summary.counts["outbox_renders"] == 1
    assert summary.counts["outbox_events"] == 1
    assert OutboxMessage.objects.count() == 3


def start_request(harness, *, inventory=None):
    """Synthetic readiness admission exercises actual gated inventory storage."""
    now = timezone.now()
    return begin_transition(
        campaign_id=harness.campaign.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        inventory=inventory or summarize_targets(iter_inventory(harness.campaign.pk)),
        summary=capture_summary(harness.campaign.pk, readiness_digest="b" * 64),
        acknowledged_at=now,
        reauthenticated_at=now - timedelta(seconds=1),
        admit=lambda *args: True,
    )


def test_manifest_matches_independent_sql_catalog_and_is_replay_safe(response_service):
    """The database validates exact membership and the streaming fingerprint."""
    with work_transaction():
        targets = tuple(iter_inventory(response_service.campaign.pk))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT category,target_id FROM stewardship_cleanup_inventory_v1(%s)",
                [response_service.campaign.pk],
            )
            assert set(cursor.fetchall()) == {
                (target.category.value, target.identifier) for target in targets
            }
        status = start_request(response_service)
        marker = seal_manifest(status.request_id)
        assert seal_manifest(status.request_id) == marker
    assert ProductionCleanupManifest.objects.count() == 1
    assert ProductionCleanupTarget.objects.count() == len(targets)


def test_manifest_rejects_mismatched_acknowledged_inventory(response_service):
    """A forged empty count cannot conceal existing rehearsal credentials."""
    with (
        pytest.raises(IntegrityError, match="acknowledged evidence"),
        work_transaction(),
    ):
        status = start_request(response_service, inventory=summarize_targets([]))
        seal_manifest(status.request_id)
    assert not ProductionCleanupManifest.objects.exists()
    assert not ProductionCleanupTarget.objects.exists()


def test_unsealed_targets_cannot_commit(response_service):
    """Neither partial capture nor invented row identity becomes a durable manifest."""
    with pytest.raises(IntegrityError, match="sealed manifest"), work_transaction():
        status = start_request(response_service)
        from parishkit.stewardship.campaigns.production_models import (
            ProductionTransitionRequest,
        )

        request = ProductionTransitionRequest.objects.get(pk=status.request_id)
        ProductionCleanupTarget.objects.create(
            request=request,
            category=Category.SUBMISSION.value,
            target_id=uuid4(),
            actor_id=request.initiated_by_id,
            correlation_id=request.correlation_id,
        )
    assert not ProductionCleanupTarget.objects.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivery_counts", {"intended_recipient": "private@example.org"}),
        ("delivery_attempts", 1),
        ("template_ids", [str(uuid4())]),
        ("testing_recipient_fingerprint", "a" * 64),
    ],
)
def test_sql_rejects_forged_delivery_summary(
    response_service, monkeypatch, field, value
):
    """Even a wrong internal aggregator cannot retain arbitrary private payloads."""
    from parishkit.stewardship.campaigns import cleanup_manifest

    original = cleanup_manifest.delivery_summary
    monkeypatch.setattr(
        cleanup_manifest,
        "delivery_summary",
        lambda campaign_id: original(campaign_id) | {field: value},
    )
    with (
        pytest.raises(IntegrityError, match="non-sensitive Testing delivery evidence"),
        work_transaction(),
    ):
        status = start_request(response_service)
        seal_manifest(status.request_id)
    assert not ProductionCleanupManifest.objects.exists()
    assert not ProductionCleanupTarget.objects.exists()


def test_sql_rejects_invented_targets_even_when_counts_are_matched(response_service):
    """Membership verification is independent of fingerprints supplied by Python."""
    from parishkit.stewardship.campaigns.cleanup_types import CleanupTarget
    from parishkit.stewardship.campaigns.production_models import (
        ProductionTransitionRequest,
    )

    invented = CleanupTarget(Category.SUBMISSION, uuid4())
    with (
        pytest.raises(IntegrityError, match="exact Testing corpus"),
        work_transaction(),
    ):
        status = start_request(
            response_service, inventory=summarize_targets([invented])
        )
        request = ProductionTransitionRequest.objects.get(pk=status.request_id)
        attribution = dict(
            actor_id=request.initiated_by_id, correlation_id=request.correlation_id
        )
        ProductionCleanupTarget.objects.create(
            request=request,
            category=invented.category.value,
            target_id=invented.identifier,
            **attribution,
        )
        ProductionCleanupManifest.objects.create(
            request=request,
            **attribution,
            **delivery_summary(response_service.campaign.pk),
        )
    assert not ProductionCleanupManifest.objects.exists()
