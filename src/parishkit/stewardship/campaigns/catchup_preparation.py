"""Bounded activation traversal with outcomes and cursor committed together.

The activation-time Family cohort is stable even if later source generations
add targets. Current eligibility and schedule configuration are always re-read.
A selected configuration change restarts traversal with a distinct checkpoint
namespace, preserving every former outcome instead of rewinding history.
"""

from uuid import UUID

from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.ownership import TaskClaim, lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .catchup_errors import CatchUpPreparationHeld
from .catchup_tasks import eligible, owned_demand
from .credential_models import FamilyCampaign
from .family_schedule_planning import plan_family
from .models import CatchUpCheckpoint
from .schedule_models import ScheduleDefinition
from .work_locks import require_work_order


def _checkpoint(demand, claim, *, key, cursor, phase, items=0, complete=False):
    """Append only after outcomes; the SQL effect advances the durable demand."""
    lock_task_claim(claim)
    return CatchUpCheckpoint.objects.create(
        demand=demand,
        sequence=demand.groups_completed + 1,
        group_key=key,
        cursor=cursor,
        items=items,
        phase=phase,
        complete=complete,
        task_id=claim.run_id,
        fence=claim.fence,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )


def cohort(demand):
    """Activation serializes population writes; later additions belong to BG-06."""
    return FamilyCampaign.objects.filter(
        campaign_id=demand.campaign_id, created_at__lte=demand.created_at
    )


def receipt_key(configuration, kind, identifier):
    """Stable indexed receipt identity survives worker retries, not new config."""
    return f"{configuration.hex}:{kind}:{identifier}"


def prepare_batch(demand, claim):
    """Prepare at most one bounded group/page in the caller's live effect scope."""
    require_work_order()
    if not isinstance(claim, TaskClaim):
        raise TypeError("Catch-up preparation requires a real task claim.")
    current = owned_demand(_status(lock_task_claim(claim)))
    if current.pk != demand.pk or current.completed_at or not eligible(current):
        raise PermissionError("Catch-up preparation is not admitted.")
    scope = _scope(current.campaign_id)
    configuration = scope.campaign.active_configuration_id
    prefix = configuration.hex + ":"
    if current.cursor.startswith(prefix):
        position = current.cursor[len(prefix) :]
    else:
        position = "families:"
    phase, _, cursor = position.partition(":")
    if phase == "families":
        families = cohort(current).order_by("id")
        if cursor:
            families = families.filter(id__gt=UUID(cursor))
        family = families.values_list("id", flat=True).first()
        if family is not None:
            result = plan_family(claim, family_id=family, worker_id=claim.worker_id)
            if result.held:
                raise CatchUpPreparationHeld("Catch-up Family group requires recovery.")
            return _checkpoint(
                current,
                claim,
                key=receipt_key(configuration, "family", family),
                cursor=prefix + f"families:{family.hex}",
                phase="families",
                items=result.examined,
            )
        return _checkpoint(
            current,
            claim,
            key=prefix + "families:end",
            cursor=prefix + "digests:",
            phase="digests",
        )
    if phase == "digests":
        from .catchup_digest import prepare_digest

        definitions = list(
            ScheduleDefinition.objects.select_for_update(of=("self",))
            .select_related("current_revision")
            .filter(
                campaign_id=current.campaign_id,
                current_revision_id__isnull=False,
                kind__in=("daily_digest", "weekly_digest"),
            )
            .order_by("id")[:3]
        )
        if len(definitions) > 2:
            raise StorageInvariantError(
                "Digest definitions exceed configuration bounds."
            )
        for definition in definitions:
            key = receipt_key(configuration, "digest", definition.pk)
            if not CatchUpCheckpoint.objects.filter(
                demand=current, group_key=key
            ).exists():
                return prepare_digest(current, claim, scope, definition, cursor)
        # Prove the stable activation cohort was visited under this current
        # configuration. Empty occurrence tables are deliberately irrelevant.
        receipts = CatchUpCheckpoint.objects.filter(
            demand=current, group_key__startswith=prefix + "family:"
        ).count()
        if receipts != cohort(current).count():
            raise StorageInvariantError("Catch-up cohort coverage is incomplete.")
        return _checkpoint(
            current,
            claim,
            key=prefix + "complete",
            cursor=prefix + "complete:",
            phase="complete",
            complete=True,
        )
    raise StorageInvariantError("Catch-up cursor has no compiled continuation.")
