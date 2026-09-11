"""Finite, deployment-wide process/connection/response budgets for OPS-04.

These are admission inputs, not measured performance evidence. Scale/rollout
settings must match the rendered process topology. Each web thread can retain
one interactive connection; download connections are additional and isolated.
"""

from dataclasses import dataclass, fields

from parishkit.config import ConfigError


@dataclass(frozen=True)
class RuntimeBudget:
    """Reserve interactive, background and operator capacity even at saturation."""

    web_processes: int = 2
    web_threads: int = 8
    replicas: int = 1
    rollout_overlap: int = 2
    download_capacity: int = 4
    download_pool_per_process: int = 2
    background_connections: int = 24
    operator_connections: int = 8
    auxiliary_connections: int = 4
    database_connections: int = 100
    database_reserved: int = 3
    download_seconds: int = 300
    download_idle_seconds: int = 330
    drain_seconds: int = 360
    server_timeout_seconds: int = 370
    proxy_timeout_seconds: int = 380

    def __post_init__(self):
        """Reject booleans, infinite/unbounded values and inconsistent headroom."""
        if any(
            type(getattr(self, item.name)) is not int
            or not 1 <= getattr(self, item.name) <= 10000
            for item in fields(self)
        ):
            raise ConfigError("Runtime budgets require bounded positive integers.")
        if (
            self.download_capacity > 32
            or self.download_pool_per_process > self.download_capacity
            or self.download_pool_per_process >= self.web_threads
            or self.download_capacity >= self.web_processes * self.web_threads
            or self.background_connections < 2
            or self.operator_connections < 2
            or self.total_connections > self.database_connections
            or not (
                self.download_seconds
                < self.download_idle_seconds
                < self.drain_seconds
                < self.server_timeout_seconds
                <= self.proxy_timeout_seconds
            )
        ):
            raise ConfigError("Runtime connection or timeout budgets are inconsistent.")

    @property
    def total_connections(self):
        """Count every replica/process, including simultaneous old/new rollouts."""
        web = self.web_processes * (self.web_threads + self.download_pool_per_process)
        return (
            self.replicas * self.rollout_overlap * web
            + self.background_connections
            + self.operator_connections
            + self.auxiliary_connections
            + self.database_reserved
        )

    def validate_database(self, *, maximum, reserved):
        """The live PostgreSQL server, not just YAML, must accommodate the budget."""
        if (
            type(maximum) is not int
            or type(reserved) is not int
            or maximum < self.database_connections
            or reserved > self.database_reserved
            or reserved < 0
        ):
            raise ConfigError("PostgreSQL capacity differs from the runtime budget.")


def parse_budget(value):
    """No arbitrary server flags or silently ignored deployment fields."""
    names = {item.name for item in fields(RuntimeBudget)}
    if type(value) is not dict or value.keys() - names:
        raise ConfigError("Runtime budget configuration shape is invalid.")
    return RuntimeBudget(**value)
