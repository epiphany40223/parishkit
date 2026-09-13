"""Atomic seeded-policy effects for source promotion and configuration activation.

These internal owners do not admit a browser user or confirm new seed grants.
Each caller must already own the source/configuration transaction. SQL verifies
the exact current inputs and owner before applying overlays and review episodes.
"""

from django.db import connection

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .chair_models import ChairReconciliation, ChairSeedEvidence
from .chair_policy import ChairRelationship, SeedIdentity, seed_decisions
from .provider_context import validated_context
from .runtime_models import SystemConfiguration


def _decisions(configuration, current):
    """Read narrow source evidence and exact retained selections under ownership."""
    document = configuration.canonical_document
    try:
        integration = next(
            row["values"]
            for row in document["sections"].get("integrations", [])
            if row["values"]["kind"] == "parishsoft"
        )
        raw = integration["settings"]["organization_id"]
        organization = int(raw)
        if raw != str(organization):
            raise ValueError("Organization is not canonical.")
        validated_context("parishsoft", {"organization_id": organization})
    except (KeyError, TypeError, ValueError, StopIteration):
        raise StorageInvariantError(
            "Chair effects require the configured ParishSoft organization."
        ) from None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT organization_id,member_duid,ministry_duid,email "
            "FROM stewardship_current_chair WHERE snapshot_id=%s",
            [current.snapshot_id],
        )
        relationships = frozenset(ChairRelationship(*row) for row in cursor.fetchall())
    identities = {
        row.assignment_record_id: SeedIdentity(row.organization_id, row.member_duid)
        for row in ChairSeedEvidence.objects.filter(
            assignment_record_id__in=configuration.ministryassignment_set.filter(
                source="chair-seed"
            ).values("record_id")
        )
    }
    return [
        {
            "assignment_record_id": str(row.assignment_record_id),
            "active": row.active,
            "reason": row.reason,
        }
        for row in seed_decisions(
            document,
            organization_id=organization,
            relationships=relationships,
            identities=identities,
        )
    ]


def _reconcile(runtime, current, **owner):
    """Commit one owner-bound receipt; its SQL effect owns overlays and review tasks."""
    existing = ChairReconciliation.objects.filter(
        configuration_id=runtime.active_configuration_id,
        snapshot_id=current.snapshot_id,
    ).first()
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in owner.items()):
            raise StorageInvariantError("Chair reconciliation has another owner.")
        return existing
    return ChairReconciliation.objects.create(
        configuration=runtime.active_configuration,
        snapshot_id=current.snapshot_id,
        decisions=_decisions(runtime.active_configuration, current),
        **owner,
    )


def reconcile_source_chairs(snapshot_id, claim, *, campaign_id):
    """Apply exact-current refresh effects before the source promotion commits."""
    from parishkit.stewardship.jobs.admission import require_source_refresh
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.source.leases import verify_source
    from parishkit.stewardship.source.requests import _organization

    require_work_order()
    scope = require_source_refresh(campaign_id=campaign_id)
    verify_source(claim)
    current = SourceCurrent.objects.get(singleton=True)
    if current.organization_id != _organization(scope):
        raise PermissionError("Chair effects require the configured organization.")
    if current.snapshot_id != snapshot_id or claim.phase not in {"full", "delta"}:
        raise StorageInvariantError("Chair effects require the current refresh.")
    result = _reconcile(
        scope.runtime,
        current,
        activation_id=None,
        source_owner_id=claim.task_id,
        source_fence=claim.fence,
        actor_id=claim.worker_id,
        correlation_id=TaskRun.objects.values_list("correlation_id", flat=True).get(
            pk=claim.task_id
        ),
    )
    verify_source(claim)
    return result


def reconcile_configuration_chairs(activation):
    """Apply source-aware policy changes in the configuration activation transaction.

    Empty bootstrap source has no usable seeds. The activity preflight and SQL
    activation constraint separately hold any activity edit needing missing
    source evidence, before or during installation respectively.
    """
    require_work_order()
    runtime = SystemConfiguration.objects.select_related("active_configuration").get()
    if runtime.active_configuration_id != activation.configuration_id:
        raise StorageInvariantError("Chair effects require the current activation.")
    current = SourceCurrent.objects.filter(singleton=True).first()
    if current is None or current.snapshot_id is None:
        return None
    from .chair_models import ChairAssignmentReview

    if (
        not runtime.active_configuration.ministryassignment_set.filter(
            source="chair-seed"
        ).exists()
        and not ChairAssignmentReview.objects.filter(closed_by__isnull=True).exists()
    ):
        return None
    return _reconcile(
        runtime,
        current,
        activation_id=activation.pk,
        source_owner_id=None,
        source_fence=None,
        actor_id=activation.actor_id,
        correlation_id=activation.correlation_id,
    )
