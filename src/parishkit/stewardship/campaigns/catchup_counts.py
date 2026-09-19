"""Immutable preparation outcomes, separate from physical mail and delivery.

Capture a complete group's current selection in its own checkpoint transaction.
Configuration restarts retain old evidence but totals use only the current
configuration's completed groups. Partial digest pages never contribute twice.
"""

from django.db.models import BigIntegerField, Sum
from django.db.models.functions import Cast, Coalesce

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.reports.readiness_weekly import weekly_message_count

from .credential_models import FamilyCampaign
from .models import CatchUpCheckpoint
from .schedule_models import ScheduleFulfillment
from .work_locks import require_work_order

COUNT_KEYS = (
    "active_families",
    "eligible_families",
    "no_email_families",
    "family_messages",
    "daily_messages",
    "weekly_messages",
    "coalesced_slots",
)


def family_counts(demand, family_id, result):
    """Freeze actual group eligibility and retained current-revision outcomes."""
    require_work_order()
    family = FamilyCampaign.objects.get(pk=family_id, campaign_id=demand.campaign_id)
    counts = dict.fromkeys(COUNT_KEYS, 0)
    counts.update(
        active_families=int(family.active),
        eligible_families=int(family.active and family.email_eligible),
        no_email_families=int(family.active and not family.email_eligible),
        family_messages=int(result.selected is not None),
        coalesced_slots=_coalesced(demand, target=f"family:{family_id}"),
    )
    return counts


def _coalesced(demand, **scope):
    """Count semantic coverage once, including forwarded predecessor coverage."""
    return ScheduleFulfillment.objects.filter(
        definition__campaign_id=demand.campaign_id,
        definition__current_revision_id__isnull=False,
        mode="production",
        occurrence__due_at__lte=demand.cutoff,
        disposition="coalesced",
        **scope,
    ).count()


def digest_counts(demand, scope, definition, selected):
    """Freeze recipient-level candidates only after the entire digest is covered.

    These are selection outcomes, not rendered/sent messages. The existing
    delivery owners still recheck content, recipients and acceptance afterward.
    """
    require_work_order()
    counts = dict.fromkeys(COUNT_KEYS, 0)
    counts["coalesced_slots"] = _coalesced(
        demand, target="admins", definition=definition
    )
    if selected is not None:
        recipients = tuple(
            AddressRule.objects.filter(
                configuration_id=scope.runtime.active_configuration_id,
                roles__contains=["administrator"],
            ).values_list("email", flat=True)
        )
        if definition.kind == "daily_digest":
            counts["daily_messages"] = len(recipients)
        else:
            counts["weekly_messages"] = weekly_message_count(
                demand.campaign_id, recipients, bind=lambda value: None
            )
    return counts


def prepared_counts(demand, configuration_id):
    """Aggregate only immutable finished groups from the selected configuration."""
    require_work_order()
    # Completion pins the observed configuration; later campaign edits must not
    # erase the report or reinterpret its finished preparation as empty work.
    prefix = (
        demand.cursor.partition(":")[0] + ":"
        if demand.completed_at
        else configuration_id.hex + ":"
    )
    return CatchUpCheckpoint.objects.filter(
        demand=demand, group_key__startswith=prefix
    ).aggregate(
        **{
            key: Coalesce(
                Sum(Cast(f"outcome_counts__{key}", BigIntegerField())),
                0,
                output_field=BigIntegerField(),
            )
            for key in COUNT_KEYS
        }
    )
