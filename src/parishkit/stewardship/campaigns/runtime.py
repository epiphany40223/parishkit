"""Internal lifecycle storage transactions; owning services supply admission.

No route, task queue or provider operation exposes these functions. The owning
ADM/BG services must verify the registry obligations against durable evidence
inside the callback, including token-generation readiness and external work.
"""

from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.accounts.installation_lock import installation_lock
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .domain import CampaignState, SystemMode, UTCInterval
from .lifecycle import Action, CampaignFacts, transition_target
from .models import Campaign, CampaignTransition, RuntimeTransition
from .work_locks import lock_work_order


def campaign_facts(campaign, runtime):
    """Construct fresh canonical policy input from the locked immutable projection."""
    projection = campaign.active_configuration
    return CampaignFacts(
        CampaignState(campaign.state),
        SystemMode(runtime.mode),
        UTCInterval(projection.starts_at, projection.ends_at),
        runtime.current_campaign_id == campaign.pk,
        runtime.restore_review_required,
        campaign.delivery_paused,
        campaign.activationcatchupdemand_set.filter(completed_at__isnull=True).exists(),
    )


@contextmanager
def campaign_transaction(campaign_id, *, correlation_id):
    """Serialize YAML/lifecycle decisions, then take global/runtime/campaign locks."""
    if not isinstance(campaign_id, UUID) or not isinstance(correlation_id, UUID):
        raise TypeError("Campaign and correlation identities must be UUIDs.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Campaign workflow must own its transaction.")
    with (
        installation_lock(),
        correlation(correlation_id),
        transaction.atomic(durable=True),
    ):
        lock_work_order()
        runtime = SystemConfiguration.objects.select_for_update().get()
        campaign = (
            Campaign.objects.select_for_update(of=("self",))
            .select_related("active_configuration")
            .get(pk=campaign_id)
        )
        yield campaign, runtime


def _now():
    """Use the same authoritative database instant as persistent transition guards."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_campaign_now_v1()")
        return cursor.fetchone()[0]


def _emit(
    campaign,
    runtime,
    *,
    action,
    request_id,
    actor_id,
    correlation_id,
    token_generation_id=None,
    boundary_id=None,
    task_fence=None,
    reason="",
):
    """Insert the immutable command; SQL commits runtime/history effects together."""
    if action not in {
        Action.ACTIVATE,
        Action.START,
        Action.CLOSE,
        Action.WITHDRAW,
        Action.ARCHIVE,
        Action.UNARCHIVE,
    }:
        raise StorageInvariantError(
            "Campaign action requires its separate owning workflow."
        )
    target = transition_target(action, campaign_facts(campaign, runtime), _now())
    if target is None:
        raise StorageInvariantError("Campaign transition is not admitted.")
    next_mode = (
        "production"
        if action in {Action.ACTIVATE, Action.REOPEN}
        else "testing"
        if action is Action.WITHDRAW
        else runtime.mode
    )
    return CampaignTransition.objects.create(
        campaign=campaign,
        request_id=request_id,
        action=action.value,
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        before_state=campaign.state,
        after_state=target.value,
        before_mode=runtime.mode,
        after_mode=next_mode,
        configuration_id=runtime.active_configuration_id,
        token_generation_id=token_generation_id,
        boundary_id=boundary_id,
        task_fence=task_fence,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )


def transition_campaign(
    *,
    campaign_id,
    action,
    request_id,
    expected_version,
    expected_runtime_version,
    actor_id,
    correlation_id,
    admit,
    token_generation_id=None,
    boundary_id=None,
    task_fence=None,
    reason="",
):
    """Recheck admission on retries; reject changed intent under an existing key."""
    if not callable(admit) or not isinstance(action, Action):
        raise TypeError("Canonical action and owning admission callback are required.")
    if not isinstance(request_id, UUID) or (
        actor_id is not None and not isinstance(actor_id, UUID)
    ):
        raise TypeError("Lifecycle command identifiers must be UUIDs.")
    if any(
        type(value) is not int or value < 1
        for value in (expected_version, expected_runtime_version)
    ):
        raise ValueError("Expected versions must be positive integers.")
    if any(
        value is not None and not isinstance(value, UUID)
        for value in (token_generation_id, boundary_id)
    ):
        raise TypeError("Optional lifecycle identifiers must be UUIDs.")
    if task_fence is not None and (type(task_fence) is not int or task_fence < 1):
        raise ValueError("Task fence must be a positive integer.")
    if type(reason) is not str or len(reason) > 1024:
        raise ValueError("Lifecycle reason must be bounded text.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit(action, campaign, runtime, None)
        existing = CampaignTransition.objects.filter(request_id=request_id).first()
        if existing is not None:
            if (
                existing.campaign_id,
                existing.action,
                existing.expected_version,
                existing.expected_runtime_version,
                existing.actor_id,
                existing.token_generation_id,
                existing.boundary_id,
                existing.task_fence,
                existing.reason,
            ) != (
                campaign_id,
                action.value,
                expected_version,
                expected_runtime_version,
                actor_id,
                token_generation_id,
                boundary_id,
                task_fence,
                reason,
            ):
                raise StorageInvariantError(
                    "Lifecycle command key has different intent."
                )
            return existing
        if (
            campaign.version != expected_version
            or runtime.version != expected_runtime_version
        ):
            raise StaleRecordError(
                "Campaign or runtime changed; reload before retrying."
            )
        return _emit(
            campaign,
            runtime,
            action=action,
            request_id=request_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            token_generation_id=token_generation_id,
            boundary_id=boundary_id,
            task_fence=task_fence,
            reason=reason,
        )


def return_to_testing(
    *,
    campaign_id,
    request_id,
    expected_runtime_version,
    actor_id,
    correlation_id,
    admit,
):
    """Clear the archived current pointer without rewriting historical campaign data."""
    if (
        not callable(admit)
        or not isinstance(request_id, UUID)
        or not isinstance(actor_id, UUID)
    ):
        raise TypeError("Return to Testing requires attributed owning admission.")
    if type(expected_runtime_version) is not int or expected_runtime_version < 1:
        raise ValueError("Runtime version must be a positive integer.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit(Action.RETURN_TESTING, campaign, runtime, None)
        existing = RuntimeTransition.objects.filter(request_id=request_id).first()
        if existing:
            if (
                existing.action,
                existing.before_campaign_id,
                existing.expected_version,
                existing.actor_id,
            ) != ("return_testing", campaign_id, expected_runtime_version, actor_id):
                raise StorageInvariantError("Runtime command key has different intent.")
            return existing
        if runtime.version != expected_runtime_version:
            raise StaleRecordError("Runtime changed; reload before retrying.")
        if (
            transition_target(
                Action.RETURN_TESTING, campaign_facts(campaign, runtime), _now()
            )
            is None
        ):
            raise StorageInvariantError("Return to Testing is not admitted.")
        return RuntimeTransition.objects.create(
            request_id=request_id,
            expected_version=runtime.version,
            action="return_testing",
            before_mode=runtime.mode,
            after_mode="testing",
            before_campaign_id=campaign_id,
            after_campaign_id=None,
            reason="",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
