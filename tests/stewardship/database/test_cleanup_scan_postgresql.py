"""Dependency-blocked inventories advance bounded, durable scan checkpoints."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client

from parishkit.stewardship.campaigns import cleanup_tasks
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCheckpoint,
)
from parishkit.stewardship.campaigns.rehearsals import code_context, prepare_rehearsals
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import TaskClaim

from .response_builders import response_source
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_cleanup_tasks_postgresql import queued, run
from .test_family_auth_postgresql import login
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def response_population(harness, count):
    """Build independent actual submissions, baselines and pins through their owners."""
    data = response_source()
    for index in range(count - 1):
        family_id, member_id = 100 + index, 10_000 + index
        data.families[family_id] = data.families[1] | {
            "familyDUID": family_id,
            "familyID": family_id,
        }
        data.members[member_id] = data.members[3] | {
            "memberDUID": member_id,
            "familyDUID": family_id,
        }
    snapshot, claim = prepare(data)
    snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    families = list(
        FamilyCampaign.objects.filter(portal_eligible=True).values_list("pk", flat=True)
    )
    for offset in range(0, len(families), 500):
        prepare_rehearsals(
            campaign_id=harness.campaign.pk,
            family_ids=families[offset : offset + 500],
            general=harness.rings.general,
            mac=harness.rings.mac,
            public=harness.rings.public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=lambda *args: True,
        )
    for index, credential in enumerate(RehearsalCredential.objects.order_by("pk")):
        code = harness.rings.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        ).decode()
        client, response = login(
            code,
            Client(
                enforce_csrf_checks=True,
                REMOTE_ADDR=f"192.0.{index // 250}.{index % 250 + 1}",
            ),
        )
        assert response.status_code == 302
        family = replace(
            harness, client=client, request=response.wsgi_request, snapshot=snapshot
        )
        form, answers = form_and_answers(family)
        assert submit(family, form, answers).submission is not None


def test_blocked_prefix_advances_without_rescanning(response_service):
    """A scan-only checkpoint survives replay, and later sweeps finish its parents."""
    response_population(response_service, 21)
    status = running_request(response_service)
    claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    command = uuid4()
    with work_transaction():
        first = cleanup_tasks.apply_checkpoint(
            status.request_id, claim, maximum=10, command_id=command
        )
    checkpoint = ProductionCleanupCheckpoint.objects.get(request_id=status.request_id)
    assert checkpoint.scanned_count == checkpoint.scan_position == 10
    assert checkpoint.deleted_count == checkpoint.scan_round == 0
    with work_transaction():
        assert (
            cleanup_tasks.apply_checkpoint(
                status.request_id, claim, maximum=10, command_id=command
            )
            == first
        )
    assert (
        ProductionCleanupCheckpoint.objects.filter(request_id=status.request_id).count()
        == 1
    )
    with work_transaction():
        cleanup_tasks.apply_checkpoint(status.request_id, claim, maximum=10)
    second = ProductionCleanupCheckpoint.objects.get(
        request_id=status.request_id, sequence=2
    )
    assert second.scan_position == 20 and second.deleted_count == 0
    # Finish through the same real checkpoint owner with small transaction budgets.
    current = first
    for _ in range(status.inventory_total):
        if current.processed_count == current.inventory_total:
            break
        with work_transaction():
            current = cleanup_tasks.apply_checkpoint(
                status.request_id, claim, maximum=10
            )
    assert current.processed_count == current.inventory_total
    rows = ProductionCleanupCheckpoint.objects.filter(request_id=status.request_id)
    assert rows.filter(scan_round__gt=0).exists()
    assert not rows.filter(scanned_count__gt=10).exists()
    assert not rows.filter(deleted_count__gt=10).exists()


def test_source_pin_dependency_lookup_has_parent_index(response_service):
    """The planner's exact parent lookups do not scan unrelated snapshot pins."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname='source_pin_owner_lookup'"
        )
        assert "(parent_kind, parent_id)" in cursor.fetchone()[0]
        cursor.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname='production_target_position'"
        )
        assert '(request_id, "position")' in cursor.fetchone()[0]


def test_default_worker_retains_scan_only_progress(response_service, monkeypatch):
    """A zero-deletion checkpoint is progress, not completion or task failure."""
    form, answers = form_and_answers(response_service)
    submit(response_service, form, answers)
    status = queued(response_service)
    original = cleanup_tasks.apply_checkpoint

    def small_batch(*args, **kwargs):
        """Keep the worker's ordinary claim/heartbeat and restrict only its budget."""
        return original(*args, **kwargs, maximum=2)

    monkeypatch.setattr(cleanup_tasks, "apply_checkpoint", small_batch)
    assert run(status)
    assert ProductionCleanupCheckpoint.objects.filter(
        request_id=status.request_id, deleted_count=0
    ).exists()


def test_entire_scan_without_progress_fails_instead_of_looping(response_service):
    """A permanently blocked inventory cannot renew its task lease forever."""
    status = running_request(response_service)
    with transaction.atomic():
        # Model a planner with no eligible dependencies only inside this
        # rolled-back fixture transaction; all checkpoint/fence guards remain.
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE OR REPLACE FUNCTION stewardship_cleanup_batch_targets_v1("
                "request_uuid uuid, maximum_rows integer) "
                "RETURNS TABLE(category text, target_id uuid) LANGUAGE sql STABLE "
                "SET search_path TO pg_catalog, public, pg_temp "
                "AS 'SELECT NULL::text,NULL::uuid WHERE false'"
            )
        for _ in range((status.inventory_total + 1) // 2):
            assert delete_batch(status).deleted_count == 0
        with pytest.raises(IntegrityError, match="no dependency-ready batch"):
            delete_batch(status)
        transaction.set_rollback(True)
    assert not ProductionCleanupCheckpoint.objects.filter(
        request_id=status.request_id
    ).exists()
