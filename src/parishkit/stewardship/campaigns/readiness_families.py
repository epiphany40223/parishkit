"""Read complete live-mail groups without allocating occurrences or outboxes.

The caller owns the common work lock through preview collection. Only current
Production semantic evidence participates: rehearsal submissions/deliveries are
intentionally absent. Queries batch Families, not individual message histories.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from uuid import uuid5

from parishkit.stewardship.storage import StorageInvariantError

from .credential_models import FamilyCampaign
from .models import RestoreDeliveryHold
from .readiness_impact import FamilyImpact, FamilyImpactInput, summarize_family_impact
from .schedule_evaluation import SchedulePlan
from .schedule_models import ScheduleDefinition, ScheduleFulfillment, ScheduleOccurrence
from .schedule_recovery import RecoverySlot
from .work_locks import require_work_order

FAMILY_FIELDS = (
    "id",
    "active",
    "email_eligible",
    "email_deliverable",
    "effective_submission_id",
    "source_generation",
)
OCCURRENCE_FIELDS = (
    "id",
    "definition_id",
    "revision_id",
    "target",
    "due_at",
    "state",
    "task_id",
    "outbox_id",
    "version",
)


@dataclass(frozen=True)
class FamilyImpactEvidence:
    """Safe counts and a digest of every input, not a first-page approximation."""

    counts: FamilyImpact
    digest: str


def family_impact(campaign, *, cutoff):
    """Compute exact counts with at most 200 Families' current groups in memory.

    The digest excludes last-activity noise but includes all eligibility, live
    response, source, schedule, coverage and current-attempt inputs. A source or
    delivery change can therefore invalidate a preview even if totals coincide.
    This enumeration belongs before the later short activation transaction.
    """
    require_work_order()
    definitions = list(
        ScheduleDefinition.objects.filter(
            campaign=campaign,
            current_revision__isnull=False,
            kind__in=("initial", "reminder"),
        )
        .select_related("current_revision")
        .order_by("id")[:101]
    )
    if len(definitions) > 100:
        raise StorageInvariantError("Readiness schedule group exceeds its bound.")
    digest = hashlib.sha256(b"stewardship-readiness-families-v1\x00")
    due = []
    for definition in definitions:
        revision = definition.current_revision
        _bind(digest, (definition.pk, definition.version, revision.pk))
        page = SchedulePlan.from_values(
            revision.values, campaign.active_configuration.values
        ).page(through=cutoff)
        if page.slots:
            if page.slots[0].due_at != revision.due_at:
                raise StorageInvariantError("Readiness schedule due time differs.")
            due.append(definition)
    _bind(digest, [row.pk for row in due])
    counts = summarize_family_impact(
        _groups(campaign.pk, definitions, due, digest),
        cutoff=cutoff,
        closed=cutoff >= campaign.active_configuration.ends_at,
    )
    return FamilyImpactEvidence(counts, digest.hexdigest())


def _bind(digest, value):
    """Frame canonical metadata so batch boundaries never affect the fingerprint."""
    digest.update(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))
    digest.update(b"\n")


def _groups(campaign_id, definitions, due, digest):
    """Read every UUID-ordered page; no name, code or recipient address is loaded."""
    after = None
    while True:
        query = FamilyCampaign.objects.filter(campaign_id=campaign_id)
        if after is not None:
            query = query.filter(pk__gt=after)
        families = list(query.order_by("pk").values(*FAMILY_FIELDS)[:200])
        if not families:
            return
        targets = [f"family:{row['id']}" for row in families]
        covered, held, existing = _page_evidence(definitions, targets, digest)
        for family in families:
            _bind(digest, family)
            target = f"family:{family['id']}"
            coverage, holds = covered[target], held[target]
            excluded = set(coverage) | set(holds)
            slots = []
            for definition in due:
                if definition.pk in excluded:
                    continue
                row = existing.get((target, definition.current_revision_id))
                slots.append(_slot(definition, target, row))
            yield FamilyImpactInput(
                family_id=family["id"],
                active=family["active"],
                email_eligible=family["email_eligible"],
                deliverable=family["email_deliverable"],
                responded=family["effective_submission_id"] is not None,
                initial_delivered=any(
                    coverage.get(row.pk) == "delivered"
                    or "assumed_delivered" in holds.get(row.pk, set())
                    for row in definitions
                    if row.kind == "initial"
                ),
                slots=tuple(slots),
                initial_unreviewed=any(
                    "unreviewed" in holds.get(row.pk, set())
                    for row in definitions
                    if row.kind == "initial"
                ),
            )
        after = families[-1]["id"]


def _page_evidence(definitions, targets, digest):
    """One query per evidence kind for a full page, never one query per Family."""
    filters = {
        "definition_id__in": [row.pk for row in definitions],
        "mode": "production",
        "target__in": targets,
        "slot": "once",
    }
    covered, held = defaultdict(dict), defaultdict(dict)
    for model, field, result, extra in (
        (ScheduleFulfillment, "disposition", covered, {}),
        (
            RestoreDeliveryHold,
            "state",
            held,
            {"state__in": ("unreviewed", "assumed_delivered")},
        ),
    ):
        for row in (
            model.objects.filter(**filters, **extra)
            .order_by("target", "definition_id", field, "id")
            .values("target", "definition_id", field)
            .iterator(chunk_size=200)
        ):
            _bind(digest, (field, row))
            if model is RestoreDeliveryHold:
                # More than one restore can hold the same semantic slot. Do
                # not let an assumed-delivered receipt overwrite uncertainty.
                result[row["target"]].setdefault(row["definition_id"], set()).add(
                    row[field]
                )
            else:
                result[row["target"]][row["definition_id"]] = row[field]
    existing = {}
    for row in (
        ScheduleOccurrence.objects.filter(
            **filters,
            revision_id__in=[row.current_revision_id for row in definitions],
        )
        .order_by("target", "revision_id", "-recovery_generation")
        .distinct("target", "revision_id")
        .values(*OCCURRENCE_FIELDS)
    ):
        _bind(digest, row)
        existing[(row["target"], row["revision_id"])] = row
    return covered, held, existing


def _slot(definition, target, existing):
    """Virtual identities predict new work; existing uncertain work stays blocked."""
    return RecoverySlot(
        occurrence_id=(
            existing["id"]
            if existing is not None
            else uuid5(definition.current_revision_id, "readiness:" + target)
        ),
        definition_id=definition.pk,
        kind=definition.kind,
        mode="production",
        target=target,
        slot="once",
        due_at=definition.current_revision.due_at,
        state=existing["state"] if existing is not None else "pending",
        safely_cancellable=(
            existing is None
            or (
                existing["state"] == "pending"
                and existing["task_id"] is None
                and existing["outbox_id"] is None
            )
        ),
    )
