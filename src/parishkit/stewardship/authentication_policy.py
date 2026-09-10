"""Bounded non-secret deployment thresholds shared by both authentication portals."""

import logging
from dataclasses import dataclass, fields

from parishkit.config import ConfigError


@dataclass(frozen=True)
class AuthenticationLimits:
    """Windows stay fixed; deployments may tune explicitly named count thresholds."""

    admin_starts: int = 20
    admin_callbacks: int = 10
    admin_identity: int = 5
    admin_per_minute: int = 60
    admin_burst: int = 20
    access_per_minute: int = 120
    access_burst: int = 30
    family_ip: int = 100
    family_pair: int = 5
    aggregate_attempts: int = 100
    admin_sources: int = 10
    admin_identities: int = 10
    family_sources: int = 20

    def __post_init__(self):
        """Bound thresholds by the aggregate store's fixed 1,000-entry capacity."""
        if any(
            type(getattr(self, item.name)) is not int
            or not 1 <= getattr(self, item.name) <= 1000
            for item in fields(self)
        ):
            raise ConfigError("Authentication limits require counts from 1 to 1,000.")

    def warn_if_weaker(self):
        """Production startup reports only closed field names, never caller values."""
        weaker = tuple(
            item.name
            for item in fields(self)
            if getattr(self, item.name) > item.default
        )
        if weaker:
            logging.getLogger(__name__).warning(
                "Authentication thresholds weaker than defaults: %s",
                ", ".join(weaker),
            )
        return weaker
