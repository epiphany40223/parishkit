"""Authorized Staff follow-up changes, never edits to a Family's submitted text."""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version

from .models import AdditionalInformationItem, AdditionalInformationRevision


def authorize_information(store, actor_id):
    """Reload coherent policy; old sessions and a known item UUID are not grants."""
    principal = current_principal(store, actor_id)
    if not allows(principal, Capability.ADDITIONAL_FOLLOWUP):
        raise PermissionError("Additional-information access is unavailable.")
    return principal


def update_information(
    store,
    actor_id,
    item_id,
    *,
    expected_version,
    request_key,
    follow_up_needed,
    followed_up,
    confirm_clear,
    notes,
):
    """Append one replay-safe revision and projection under the shared work order.

    SQL independently checks ownership, replay uniqueness and the paired history/
    projection/audit. It stamps completion time and retains its original actor
    when a later Staff member only changes notes or the needed checkbox.
    """
    if (
        any(not isinstance(value, UUID) for value in (actor_id, item_id, request_key))
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or any(
            type(value) is not bool
            for value in (follow_up_needed, followed_up, confirm_clear)
        )
        or not isinstance(notes, str)
        or len(notes) > 5000
    ):
        raise ValueError("Invalid additional-information change.")
    bounded_text(notes)
    intent = dict(
        item_id=item_id,
        expected_version=expected_version,
        follow_up_needed=follow_up_needed,
        followed_up=followed_up,
        confirm_clear=confirm_clear,
        notes=notes,
    )
    with work_transaction():
        authorize_information(store, actor_id)
        item = (
            AdditionalInformationItem.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=item_id)
        )
        admit_campaign(item.submission.campaign_id, mutating=True)
        previous = AdditionalInformationRevision.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if any(
                getattr(previous, field) != value for field, value in intent.items()
            ):
                raise ValueError("This request key is already bound.")
            return previous
        check_version(item, expected_version)
        if item.followed_up_at is not None and not followed_up and not confirm_clear:
            raise ValueError("Confirm clearing the completed follow-up.")
        revision = AdditionalInformationRevision.objects.create(
            actor_id=actor_id, request_key=request_key, **intent
        )
        revision.refresh_from_db()
        AdditionalInformationItem.objects.filter(pk=item_id).update(
            follow_up_needed=follow_up_needed,
            followed_up_at=revision.followed_up_at,
            version=F("version") + 1,
        )
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.INFORMATION_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=revision.pk,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=item.submission.campaign_id,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": expected_version,
                "after_version": expected_version + 1,
            },
        )
        return revision
