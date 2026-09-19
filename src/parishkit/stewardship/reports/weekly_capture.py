"""Retain one coherent weekly selection/cohort under fenced preparation ownership."""

import json
from uuid import UUID

from django.db import connection
from django.db.models import BigIntegerField, Func

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.responses.models import AdditionalInformationItem
from parishkit.stewardship.storage import StorageInvariantError

from .weekly_digest import WeeklyCorrection, WeeklyInformation
from .weekly_models import WeeklyDigestSnapshot
from .weekly_observation import (
    capture_weekly_observation,
    decode_observation,
    observation_document,
)
from .weekly_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .weekly_selection import WeeklyHistory, WeeklySelection, select_weekly


def retained_history(preparation):
    """Read only completed interval progress and actual accepted item coverage."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_weekly_preparation_history_v1(%s)::text",
            [preparation.pk],
        )
        value = json.loads(cursor.fetchone()[0])
    return WeeklyHistory(
        UUID(value["campaign_id"]),
        value["watermark"],
        frozenset(UUID(item) for item in value["reported"]),
        frozenset((UUID(item[0]), item[1]) for item in value["corrected"]),
    )


def capture_weekly_snapshot(claim):
    """Atomically freeze input, selected items and Admin cohort before fanout.

    Family submissions, source promotion and other lifecycle writes compete for the
    same work order. The insertion guard independently recalculates the complete
    selection, so a caller cannot omit a request or fabricate successful coverage.
    Compilation uses this copy and never requires a later source membership read.
    """
    require_work_order()
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(preparation)
    retained = WeeklyDigestSnapshot.objects.filter(preparation=preparation).first()
    if retained is not None:
        return retained
    if preparation.phase != "capture" or preparation.occurrence_id is None:
        raise StorageInvariantError(
            "Weekly capture requires completed schedule coverage."
        )
    observation = capture_weekly_observation(preparation.campaign_id)
    history = retained_history(preparation)
    selected = select_weekly(observation, history)
    snapshot = WeeklyDigestSnapshot.objects.create(
        preparation=preparation,
        campaign_id=preparation.campaign_id,
        configuration_id=scope.runtime.active_configuration_id,
        timezone_configuration_id=observation.configuration_id,
        source_id=observation.source_id,
        observed_at=observation.observed_at,
        submission_watermark=observation.watermark,
        after_watermark=history.watermark,
        observation=observation_document(observation),
        # Retain digest-semantic state, not the optimistic Staff edit counter:
        # notes/checkbox edits cannot reopen a discharged mail obligation.
        item_versions={
            str(identifier): version
            for identifier, version in AdditionalInformationItem.objects.filter(
                pk__in=[
                    item.item_id
                    for item in (*selected.information, *selected.corrections)
                ]
            )
            .annotate(
                digest_version=Func(
                    "disposition",
                    function="stewardship_information_digest_version_v1",
                    output_field=BigIntegerField(),
                )
            )
            .values_list("id", "digest_version")
        },
        information=[str(item.item_id) for item in selected.information],
        corrections=[
            [str(item.item_id), item.disposition] for item in selected.corrections
        ],
        recipients=list(
            AddressRule.objects.filter(
                configuration_id=scope.runtime.active_configuration_id,
                roles__contains=["administrator"],
            )
            .order_by("email")
            .values_list("email", flat=True)
        ),
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=preparation.pk,
    )
    checkpoint_preparation(claim, phase="fanout")
    return snapshot


def retained_selection(snapshot):
    """Decode exactly the captured subset, not today's mutable request statuses."""
    observation = decode_observation(snapshot.observation)
    if (
        observation.campaign_id != snapshot.campaign_id
        or observation.source_id != snapshot.source_id
        or observation.configuration_id != snapshot.timezone_configuration_id
        or observation.watermark != snapshot.submission_watermark
        or observation.observed_at != snapshot.observed_at
    ):
        raise StorageInvariantError(
            "Retained weekly observation has inconsistent identity."
        )
    by_id = {str(item.value.item_id): item.value for item in observation.items}
    try:
        information = tuple(by_id[key] for key in snapshot.information)
        corrections = tuple(by_id[key] for key, _ in snapshot.corrections)
        if (
            any(type(item) is not WeeklyInformation for item in information)
            or any(type(item) is not WeeklyCorrection for item in corrections)
            or any(
                item.disposition != pair[1]
                for item, pair in zip(corrections, snapshot.corrections, strict=True)
            )
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise StorageInvariantError(
            "Retained weekly selection has inconsistent items."
        ) from None
    return WeeklySelection(observation, information, corrections)
