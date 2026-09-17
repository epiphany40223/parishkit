"""Transactionally retained exact daily statistics and source protection."""

from datetime import date

from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from .digest_models import DailyDigestSnapshot
from .digest_ownership import bound_preparation, current_preparation
from .statistics import StatisticsInputs, StatisticsUnavailable, calculate_statistics
from .statistics_selection import capture_statistics


def capture_daily_snapshot(claim):
    """Freeze once, then replay that observation even after source promotion.

    The caller retains the work-order transaction across capture and source pin.
    Source compaction competes for the same snapshot lock: if it won, the entire
    effect rolls back rather than leaving an unprotected asynchronous input.
    Reporting statistics always use live submissions, including when Testing
    mode routes the resulting digest to its designated rehearsal recipient.
    """
    require_work_order()
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(preparation)
    if preparation.phase != "facts" or preparation.occurrence_id is None:
        raise StorageInvariantError("Daily capture requires completed date coverage.")
    retained = DailyDigestSnapshot.objects.filter(preparation=preparation).first()
    if retained is not None:
        return retained
    dates, after = [], None
    while True:
        lock_task_claim(claim)
        page = covered_dates(
            preparation.occurrence_id, after=after, mode=preparation.mode
        )
        if not page:
            break
        dates.extend(page)
        after = page[-1]
    if not dates:
        raise StorageInvariantError("Daily capture requires a nonempty covered range.")
    configuration = scope.campaign.active_configuration
    if dates[0] < configuration.start_date or dates[-1] > configuration.end_date:
        raise StorageInvariantError("Daily coverage exceeds its configured interval.")
    inputs = capture_statistics(preparation.campaign_id)
    statistics = calculate_statistics(inputs)
    if (
        statistics.source_id is None
        or statistics.active is None
        or statistics.campaign_id != preparation.campaign_id
        or statistics.configuration_id != preparation.campaign_configuration_id
    ):
        raise StatisticsUnavailable("Daily statistics require complete exact inputs.")
    snapshot = DailyDigestSnapshot.objects.create(
        preparation=preparation,
        campaign_id=preparation.campaign_id,
        configuration_id=scope.runtime.active_configuration_id,
        source_id=statistics.source_id,
        population_scope="historical",
        submission_watermark=statistics.submission_watermark,
        timezone_configuration_id=statistics.configuration_id,
        through_date=dates[-1],
        observed_at=statistics.observed_at,
        statistics_inputs=inputs.canonical,
        covered_dates=[day.isoformat() for day in dates],
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=preparation.pk,
    )

    def admit(action, source):
        """A source row lock is not permission to protect an unrelated input."""
        lock_task_claim(claim)
        current_preparation(preparation)
        return action == "pin" and source.pk == snapshot.source_id

    pin_snapshot(
        snapshot.source_id,
        parent_kind="digest",
        parent_id=snapshot.pk,
        admit=admit,
    )
    return snapshot


def retained_statistics(snapshot):
    """Recalculate only the owned observation, rejecting detached binding drift."""
    statistics = calculate_statistics(StatisticsInputs(snapshot.statistics_inputs))
    if (
        statistics.campaign_id != snapshot.campaign_id
        or statistics.configuration_id != snapshot.timezone_configuration_id
        or statistics.source_id != snapshot.source_id
        or statistics.submission_watermark != snapshot.submission_watermark
        or statistics.observed_at != snapshot.observed_at
        or statistics.active is None
    ):
        raise StatisticsUnavailable(
            "Retained daily statistics have inconsistent inputs."
        )
    return statistics


def retained_dates(snapshot):
    """Decode canonical complete coverage without consulting live schedules."""
    try:
        values = snapshot.covered_dates
        if type(values) is not list or not values:
            raise ValueError
        dates = tuple(date.fromisoformat(item) for item in values)
        if (
            [day.isoformat() for day in dates] != values
            or dates != tuple(sorted(set(dates)))
            or dates[-1] != snapshot.through_date
        ):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise StorageInvariantError(
            "Retained daily coverage is inconsistent."
        ) from None
    return dates
