"""Closed Testing inventory selectors; selection alone never authorizes deletion.

Only the Production-cleanup owner may persist these identities alongside its
gate and request. Each later batch must repeat ownership checks against the
recorded selection. No caller supplies table names, filters or retention modes.
"""

import hashlib
from collections import Counter
from types import MappingProxyType
from uuid import UUID

from django.db.models import Q

from parishkit.stewardship.jobs.outbox_models import (
    OutboxEvent,
    OutboxMessage,
    OutboxRender,
)
from parishkit.stewardship.responses.models import (
    FamilyFormBaseline,
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.models import SourceSnapshotPin
from parishkit.stewardship.workflows.models import MinistryRequest

from .cleanup_types import CleanupCategory as CleanupCategory
from .cleanup_types import CleanupTarget as CleanupTarget
from .credential_models import (
    FamilySession,
    RehearsalCodeFingerprint,
    RehearsalCredential,
    RehearsalEpoch,
)
from .production_models import ProductionCleanupTarget
from .production_storage import CleanupInventory
from .schedule_models import (
    OccurrenceTransition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from .work_locks import require_work_order


def summarize_targets(targets):
    """Fingerprint an exact canonically ordered selection without retaining IDs.

    The caller streams each category then primary key in ascending order.
    Reject duplicates and disorder rather than sorting a potentially large
    manifest in memory. Counts and the digest are evidence, not admission proof.
    """
    digest = hashlib.sha256(b"stewardship-production-cleanup-inventory-v1\x00")
    counts = Counter()
    previous = None
    for target in targets:
        if not isinstance(target, CleanupTarget):
            raise ValueError("Cleanup inventory requires typed targets.")
        key = (target.category.value, target.identifier.int)
        if previous is not None and key <= previous:
            raise ValueError(
                "Cleanup inventory must be unique and canonically ordered."
            )
        digest.update(target.category.value.encode("ascii") + b"\x00")
        digest.update(target.identifier.bytes)
        counts[target.category.value] += 1
        previous = key
    return CleanupInventory(digest.hexdigest(), dict(counts))


def inventory_queries(campaign_id):
    """Select only one campaign's immutable Testing-owned records under work order.

    Include earlier invalidated epochs as well as the current one. Production
    Family identity, anonymous code reservations and permanent safe audit events
    never enter this catalog. AdditionalInformationItem is live-only; Testing
    additional-information text resides in the inventoried Submission answers.
    Source corpora remain protected by their other owners after pins are removed.
    """
    require_work_order()
    if not isinstance(campaign_id, UUID):
        raise ValueError("Cleanup inventory requires a canonical campaign identity.")
    epochs = RehearsalEpoch.objects.filter(campaign_id=campaign_id).values("pk")
    responses = Submission.objects.filter(
        campaign_id=campaign_id, mode="test", rehearsal_epoch_id__in=epochs
    )
    baselines = FamilyFormBaseline.objects.filter(
        family__campaign_id=campaign_id, mode="test", rehearsal_epoch_id__in=epochs
    )
    sessions = FamilySession.objects.filter(
        family__campaign_id=campaign_id,
        mode="testing",
        rehearsal_epoch_id__in=epochs,
    )
    messages = OutboxMessage.objects.filter(
        campaign_id=campaign_id, mode="testing", routing="testing_override"
    )
    occurrences = ScheduleOccurrence.objects.filter(
        definition__campaign_id=campaign_id,
        mode="testing",
        routing="testing_override",
    )
    credentials = RehearsalCredential.objects.filter(
        epoch_id__in=epochs, family__campaign_id=campaign_id
    )
    queries = {
        CleanupCategory.BASELINE: baselines,
        CleanupCategory.SESSION_DATA: sessions.filter(session__isnull=False),
        CleanupCategory.SESSION: sessions,
        CleanupCategory.MINISTRY: MinistryRequest.objects.filter(
            submission_id__in=responses.values("pk")
        ),
        CleanupCategory.OCCURRENCE: occurrences,
        CleanupCategory.OCCURRENCE_EVENT: OccurrenceTransition.objects.filter(
            occurrence_id__in=occurrences.values("pk")
        ),
        CleanupCategory.OUTBOX_EVENT: OutboxEvent.objects.filter(
            message_id__in=messages.values("pk")
        ),
        CleanupCategory.OUTBOX: messages,
        CleanupCategory.OUTBOX_RENDER: OutboxRender.objects.filter(
            message_id__in=messages.values("pk")
        ),
        CleanupCategory.PROPOSAL: ProposedChange.objects.filter(
            submission_id__in=responses.values("pk")
        ),
        CleanupCategory.REHEARSAL: credentials,
        CleanupCategory.REHEARSAL_MAC: RehearsalCodeFingerprint.objects.filter(
            credential_id__in=credentials.values("pk"), epoch_id__in=epochs
        ),
        CleanupCategory.FULFILLMENT: ScheduleFulfillment.objects.filter(
            definition__campaign_id=campaign_id,
            mode="testing",
            occurrence_id__in=occurrences.values("pk"),
        ),
        CleanupCategory.PIN: SourceSnapshotPin.objects.filter(
            Q(parent_kind="submission", parent_id__in=responses.values("pk"))
            | Q(parent_kind="form_baseline", parent_id__in=baselines.values("pk"))
        ),
        CleanupCategory.RECEIPT: SubmissionReceiptOccurrence.objects.filter(
            submission_id__in=responses.values("pk")
        ),
        CleanupCategory.SUBMISSION: responses,
        CleanupCategory.PRIOR_INVENTORY: ProductionCleanupTarget.objects.filter(
            request__campaign_id=campaign_id, request__state="cancelled"
        ),
    }
    return MappingProxyType(queries)


def iter_inventory(campaign_id):
    """Stream only UUID identities in deterministic category/primary-key order."""
    for category, query in sorted(inventory_queries(campaign_id).items()):
        for identifier in (
            query.order_by("pk").values_list("pk", flat=True).iterator(chunk_size=500)
        ):
            yield CleanupTarget(category, identifier)
