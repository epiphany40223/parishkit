"""Exact weekly recipient impact from nonprivate selection/acceptance metadata."""

from uuid import UUID

from parishkit.stewardship.campaigns.schedule_models import ScheduleFulfillment
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .weekly_models import (
    WeeklyDigestRecipient,
    WeeklyDigestSnapshot,
    WeeklyManualRequest,
)
from .weekly_selection import WeeklyHistory, weekly_item_selected


def weekly_message_count(campaign_id, recipients, *, bind):
    """Mirror automatic interval selection and per-recipient accepted coverage.

    Testing history and manual interval completion cannot advance the live
    automatic watermark. Manual provider acceptance still establishes reporting
    and duplicate suppression, just as it does in the execution owner. This
    reader never requests item text, source names or compiled message bodies.
    """
    require_work_order()
    snapshots = WeeklyDigestSnapshot.objects.filter(
        preparation__campaign_id=campaign_id,
        preparation__mode="production",
        preparation__rehearsal_epoch_id__isnull=True,
    )
    fulfilled = ScheduleFulfillment.objects.filter(
        mode="production", target="admins", disposition__in=("delivered", "empty")
    ).values("occurrence_id")
    completed = snapshots.filter(preparation__occurrence_id__in=fulfilled).exclude(
        preparation_id__in=WeeklyManualRequest.objects.values("id")
    )
    watermark, corrected = 0, set()
    for row in (
        completed.order_by("id")
        .values("id", "submission_watermark", "corrections")
        .iterator(chunk_size=200)
    ):
        bind(row)
        watermark = max(watermark, row["submission_watermark"])
        corrected.update((UUID(item), state) for item, state in row["corrections"])
    reported, covered = set(), {address: set() for address in recipients}
    accepted = WeeklyDigestRecipient.objects.filter(
        snapshot__in=snapshots, outbox__state="delivered"
    )
    for row in (
        accepted.order_by("id")
        .values("id", "address", "information", "corrections")
        .iterator(chunk_size=200)
    ):
        bind(row)
        information = {UUID(item) for item in row["information"]}
        reported.update(information)
        if row["address"] in covered:
            covered[row["address"]].update(
                (item, "current_actionable") for item in information
            )
            covered[row["address"]].update(
                (UUID(item), state) for item, state in row["corrections"]
            )
    history = WeeklyHistory(
        campaign_id, watermark, frozenset(reported), frozenset(corrected)
    )
    needed = set()
    items = AdditionalInformationItem.objects.filter(
        submission__campaign_id=campaign_id, submission__mode="live"
    )
    for row in (
        items.order_by("submission__campaign_sequence")
        .values("id", "disposition", "submission__campaign_sequence")
        .iterator(chunk_size=200)
    ):
        bind(row)
        if weekly_item_selected(
            row["id"], row["submission__campaign_sequence"], row["disposition"], history
        ):
            needed.update(
                address
                for address in recipients
                if (row["id"], row["disposition"]) not in covered[address]
            )
    return len(needed)
