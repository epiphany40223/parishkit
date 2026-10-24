"""Read-only exact Admin mail impact, without materializing dates or report facts.

Report emails (#1786) replaced the Daily and Weekly digests: the impact counts
each report's immediately due send, one message per address of its cohort
now (report-emails spec, "Catch-up, recovery and holds", Go live impact
counts). A report as of the end of the previous day is counted as the Daily
was (``daily_messages``), one as of when it is made as the Weekly was
(``weekly_messages``); the digests' key names stay because the recorded
confirmation outcomes and the go-live command's output name them. A send
that would be skipped as empty (report-emails spec, "Sending only when there
is content") makes no message and is counted as an empty report.
"""

import hashlib
from dataclasses import dataclass

from parishkit.stewardship.storage import StorageInvariantError

from .models import RestoreDeliveryHold
from .readiness_families import _bind
from .schedule_evaluation import SchedulePlan
from .schedule_models import ScheduleDefinition, ScheduleFulfillment, ScheduleOccurrence
from .schedule_recovery import digest_recovery_kind
from .work_locks import require_work_order


@dataclass(frozen=True)
class DigestImpact:
    """As-of physical messages, distinct from covered dates and empty intervals."""

    daily_messages: int
    weekly_messages: int
    coalesced_slots: int
    empty_weekly_reports: int
    blocked_groups: int
    digest: str


def digest_impact(campaign, *, cutoff, recipients):
    """Coalesce the complete due range using the execution owner's count planner.

    Read at most 100 dates' metadata per page. ``recipients`` (the
    Administrators' addresses) is bound into the digest as before; each
    report's own cohort decides its message count. These counts exclude
    Testing history even while the campaign is still in Testing mode.
    """
    from .report_values import REPORT_LIMIT

    require_work_order()
    definitions = list(
        ScheduleDefinition.objects.filter(
            campaign=campaign,
            current_revision__isnull=False,
            kind="report",
        )
        .select_related("current_revision")
        .order_by("id")[: REPORT_LIMIT + 1]
    )
    if len(definitions) > REPORT_LIMIT:
        raise StorageInvariantError("Readiness report definitions exceed their bound.")
    digest = hashlib.sha256(b"stewardship-readiness-digests-v1\x00")

    def bind(value):
        """Frame each relevant input without persisting report contents."""
        _bind(digest, value)

    bind(recipients)
    daily, weekly, coalesced, empty, blocked = 0, 0, 0, 0, 0
    for definition in definitions:
        bind((definition.pk, definition.version, definition.current_revision_id))
        count, held = _candidates(campaign, definition, cutoff, bind)
        choice = digest_recovery_kind(definition.kind, count)
        bind((count, held, choice))
        if held:
            blocked += 1
            continue
        if choice == "empty":
            continue
        coalesced += count - 1
        values = definition.current_revision.values
        messages = _report_messages(campaign, values, bind)
        if messages == 0:
            empty += 1
        elif values["as_of"] == "previous_day_end":
            daily += messages
        else:
            weekly += messages
    return DigestImpact(daily, weekly, coalesced, empty, blocked, digest.hexdigest())


def _report_messages(campaign, values, bind):
    """One send's messages: its cohort, unless it would be skipped as empty.

    It is skipped only when the report sends only with something to report
    and every block is a list block with nothing to list (every list block
    today is the open requests'); a status block always has content.
    """
    from parishkit.stewardship.reports.report_blocks import REGISTRY
    from parishkit.stewardship.reports.report_blocks.registry import LIST
    from parishkit.stewardship.reports.report_email_cohort import frozen_cohort
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    cohort = frozen_cohort(values, campaign_configuration_root(campaign))
    bind(len(cohort))
    if values["content_required"] and all(
        REGISTRY[entry["block"]].kind == LIST for entry in values["blocks"]
    ):
        open_items = AdditionalInformationItem.objects.filter(
            submission__campaign_id=campaign.pk,
            submission__mode="live",
            disposition="current_actionable",
            followed_up_at__isnull=True,
        ).count()
        bind(open_items)
        if open_items == 0:
            return 0
    return len(cohort)


def campaign_configuration_root(campaign):
    """The applied configuration a report's cohort is resolved under now."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).get()


def _candidates(campaign, definition, cutoff, bind):
    """Count uncovered original dates; allocated/uncertain work fails closed."""
    plan = SchedulePlan.from_values(
        definition.current_revision.values, campaign.active_configuration.values
    )
    common = {"definition": definition, "mode": "production", "target": "admins"}
    # A recovery aggregate is not an original civil-date slot. Never omit its
    # pending authority merely because date pagination cannot encounter its key.
    blocked = False
    for row in (
        ScheduleOccurrence.objects.filter(
            **common, due_at__lte=cutoff, slot__startswith="recovery:"
        )
        .order_by("id")
        .values("id", "state", "version")
        .iterator(chunk_size=200)
    ):
        bind(row)
        blocked |= row["state"] in {"pending", "running", "delivery_unknown"}
    after, count = None, 0
    while True:
        page = plan.page(through=cutoff, after=after, limit=100)
        keys = [slot.key for slot in page.slots]
        excluded = set()
        for model, field, filters in (
            (ScheduleFulfillment, "disposition", {}),
            (
                RestoreDeliveryHold,
                "state",
                {"state__in": ("unreviewed", "assumed_delivered")},
            ),
        ):
            for row in (
                model.objects.filter(**common, slot__in=keys, **filters)
                .order_by("slot")
                .values("slot", field)
            ):
                bind((field, row))
                excluded.add(row["slot"])
        existing = {}
        for row in (
            ScheduleOccurrence.objects.filter(
                **common,
                revision_id=definition.current_revision_id,
                slot__in=keys,
                production_cycle=campaign.production_cycle,
            )
            .order_by("slot", "-recovery_generation")
            .distinct("slot")
            .values("id", "slot", "state", "task_id", "outbox_id", "version")
        ):
            bind(row)
            existing[row["slot"]] = row
        for slot in page.slots:
            bind((slot.key, slot.due_at))
            if slot.key in excluded:
                continue
            row = existing.get(slot.key)
            if row is None:
                count += 1
            elif row["state"] in {"running", "delivery_unknown"} or (
                row["state"] == "pending" and (row["task_id"] or row["outbox_id"])
            ):
                blocked = True
            elif row["state"] == "pending":
                count += 1
        if page.exhausted:
            return count, blocked
        after = page.cursor
