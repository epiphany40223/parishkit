"""A sent Weekly digest's retained selection, read back for its saved page (#1834)."""

from parishkit.stewardship.storage import StorageInvariantError

from .weekly_digest import WeeklyCorrection, WeeklyInformation
from .weekly_observation import decode_observation
from .weekly_selection import WeeklySelection


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
