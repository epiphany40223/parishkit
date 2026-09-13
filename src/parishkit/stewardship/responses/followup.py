"""Local additional-information effects owned by the final-submit transaction."""

from uuid import uuid4

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .models import AdditionalInformationItem


def derive_additional_information(submission, prior):
    """Replace/withdraw actionable text without creating work for repeated text."""
    require_work_order()
    if submission.mode != "live":
        return None
    text = submission.answers["additional_information"]
    previous_text = prior.answers["additional_information"] if prior else ""
    if text == previous_text:
        return None
    current = list(
        AdditionalInformationItem.objects.select_for_update(of=("self",)).filter(
            submission__family_id=submission.family_id, disposition="current_actionable"
        )[:2]
    )
    if len(current) > 1:
        raise StorageInvariantError("The Family follow-up selection is inconsistent.")
    replacement_id = uuid4() if text else None
    for old in current:
        AdditionalInformationItem.objects.filter(pk=old.pk).update(
            disposition="superseded" if text else "withdrawn",
            replacement_id=replacement_id,
            version=F("version") + 1,
        )
    if text:
        return AdditionalInformationItem.objects.create(
            id=replacement_id,
            submission=submission,
            text=text,
            actor_id=submission.family_id,
        )
    return None
