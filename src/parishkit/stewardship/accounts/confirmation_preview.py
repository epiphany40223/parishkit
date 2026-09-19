"""Enumerate exact post-cleanup impact before the short final confirmation."""

from dataclasses import dataclass
from datetime import datetime

from parishkit.stewardship.campaigns.confirmation_window import confirmation_deadline
from parishkit.stewardship.campaigns.domain import UTCInterval
from parishkit.stewardship.campaigns.readiness_digests import (
    DigestImpact,
    digest_impact,
)
from parishkit.stewardship.campaigns.readiness_families import (
    FamilyImpactEvidence,
    family_impact,
)
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .confirmation_readiness import (
    ConfirmationReadiness,
    collect_readiness,
    impact_revision,
)

# The campaign permits 100 Family schedules plus one daily and one weekly digest.
MAX_CONFIRMATION_SCHEDULES = 102


@dataclass(frozen=True)
class ConfirmationPreview:
    """Current readiness and exact as-of counts, without private Family details."""

    readiness: ConfirmationReadiness
    families: FamilyImpactEvidence
    digests: DigestImpact
    expires_at: datetime | None
    problems: tuple[str, ...]


def collect_preview(request, service, campaign_id, transition_id, preparation_id):
    """Bind enumerated inputs to their revision and earliest time-based boundary.

    The final owner calls collect_readiness, never this function. These read-only
    queries create neither occurrences nor outgoing messages. Concurrent effects
    not covered by work ordering still change the transactional impact revision,
    which is checked again after enumeration and locked by final confirmation.
    """
    with work_transaction():
        state = collect_readiness(
            request, service, campaign_id, transition_id, preparation_id
        )
        families = family_impact(state.campaign, cutoff=state.observed_at)
        digests = digest_impact(
            state.campaign,
            cutoff=state.observed_at,
            recipients=state.configuration.admin_recipients,
        )
        problems = list(state.problems)
        if families.counts.blocked_families or digests.blocked_groups:
            problems.append("production_delivery_unresolved")
        expires = None
        if not problems:
            projection = state.campaign.active_configuration
            definitions = list(
                ScheduleDefinition.objects.filter(
                    campaign_id=campaign_id, current_revision__isnull=False
                ).select_related("current_revision")[: MAX_CONFIRMATION_SCHEDULES + 1]
            )
            if len(definitions) > MAX_CONFIRMATION_SCHEDULES:
                raise StorageInvariantError(
                    "Confirmation schedules exceed their bound."
                )
            expires = confirmation_deadline(
                observed_at=state.observed_at,
                source_expires_at=state.source.expires_at,
                interval=UTCInterval(projection.starts_at, projection.ends_at),
                plans=(
                    SchedulePlan.from_values(
                        row.current_revision.values, projection.values
                    )
                    for row in definitions
                ),
            )
            if _now() >= expires:
                raise StaleRecordError(
                    "Confirmation preview expired while being prepared."
                )
        if impact_revision() != state.impact_revision:
            raise StaleRecordError("Mail impact changed while preparing this preview.")
        return ConfirmationPreview(state, families, digests, expires, tuple(problems))
