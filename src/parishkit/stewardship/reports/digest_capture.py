"""A sent Daily digest's retained statistics and dates, read back (#1834)."""

from datetime import date

from parishkit.stewardship.storage import StorageInvariantError

from .statistics import StatisticsInputs, StatisticsUnavailable, calculate_statistics


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
