"""Bounded deletion of disposable response detail from an invalidated test epoch."""

from django.db.models import Exists, OuterRef

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.models import SourceSnapshotPin
from parishkit.stewardship.storage import StorageInvariantError

from .baselines import end_baseline
from .models import (
    FamilyFormBaseline,
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)


def cleanup_test_responses(epoch_id, *, batch_size):
    """Remove only exact invalidated-epoch detail, preserving all live responses.

    Each category has its own bounded batch. Remove older proposal links first,
    then standalone baselines and retained-input pins; delete submission chains
    newest first, removing their baselines before the next predecessor. A large
    parent can take several calls without unbounded pin deletion. No timestamp,
    caller boolean, session expiry or campaign name grants this authority.
    """
    require_work_order()
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Response cleanup requires a bounded batch size.")
    epoch = RehearsalEpoch.objects.select_for_update().get(pk=epoch_id)
    if (
        epoch.state != "invalidated"
        or CampaignCredentialState.objects.filter(rehearsal_epoch=epoch).exists()
    ):
        raise StorageInvariantError("An active rehearsal cannot be cleaned up.")
    scope = {"submission__mode": "test", "submission__rehearsal_epoch_id": epoch.pk}
    count = 0
    for model in (ProposedChange, SubmissionReceiptOccurrence):
        records = list(
            model.objects.filter(**scope).order_by(
                "submission__campaign_sequence", "pk"
            )[:batch_size]
        )
        for record in records:
            # Explicit retention owner, complemented by exact-epoch SQL guards.
            count += model._base_manager.filter(pk=record.pk).delete()[0]
    baselines = list(
        FamilyFormBaseline.objects.filter(
            mode="test", rehearsal_epoch_id=epoch.pk, submission__isnull=True
        ).order_by("pk")[:batch_size]
    )
    for baseline in baselines:
        if baseline.state == "open":
            end_baseline(baseline, state="cancelled")
        count += FamilyFormBaseline.objects.filter(pk=baseline.pk).delete()[0]
    responses = Submission.objects.filter(mode="test", rehearsal_epoch_id=epoch.pk)
    pins = list(
        SourceSnapshotPin.objects.filter(
            parent_kind="submission", parent_id__in=responses.values("pk")
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:batch_size]
    )
    count += SourceSnapshotPin.objects.filter(pk__in=pins).delete()[0]
    eligible = responses.filter(
        ~Exists(ProposedChange.objects.filter(submission_id=OuterRef("pk"))),
        ~Exists(
            SubmissionReceiptOccurrence.objects.filter(submission_id=OuterRef("pk"))
        ),
        ~Exists(
            SourceSnapshotPin.objects.filter(
                parent_kind="submission", parent_id=OuterRef("pk")
            )
        ),
    ).order_by("-campaign_sequence")[:batch_size]
    for response in list(eligible):
        if (
            Submission.objects.filter(prior_submission=response).exists()
            or FamilyFormBaseline.objects.filter(prior_submission=response).exists()
        ):
            continue
        count += Submission._base_manager.filter(pk=response.pk).delete()[0]
        count += FamilyFormBaseline.objects.filter(pk=response.baseline_id).delete()[0]
    return count
