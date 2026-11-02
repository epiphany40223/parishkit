"""Finite, deployment-wide process/connection/response budgets for OPS-04.

These are admission inputs, not measured performance evidence. Scale/rollout
settings must match the rendered process topology. Each web thread can retain
one interactive connection; download connections are additional and isolated.
"""

from dataclasses import dataclass, fields

from parishkit.config import ConfigError

# Docker kills a whole container ``drain_seconds`` (its stop grace period)
# after its SIGTERM. A process that must still kill its own children at a
# limit (the web master's workers, a worker's sibling consumer) does so this
# much sooner, so its durable timeout entry is written before Docker's kill
# (the entry's own connection and statement limits are two seconds each).
STOP_MARGIN_SECONDS = 15

# What each one-shot login can open at once (#2333): its Django connection
# and its process's private timeout-log connection (audit.timeouts).
ONE_SHOT_CONNECTIONS = 2
# The backup login runs several processes at once by design: a running
# backup holds Django plus pg_dump (2), a scheduled run waits up to 30
# minutes for the backup lock holding its Django connection (1), a
# request-mode poll connects before it checks the lock (1), and a timed-out
# wait still needs its timeout-log connection to record the timeout (1).
BACKUP_CONNECTIONS = 5
# Each web process's observers beside its request threads: readiness,
# metrics and the periodic authentication health check.
WEB_OBSERVER_CONNECTIONS = 3


@dataclass(frozen=True)
class RuntimeBudget:
    """Reserve interactive, background and operator capacity even at saturation.

    Every database login's connection limit comes from ``login_limit``, and
    ``total_connections`` adds them all up (#2333): the limits must fit
    outside PostgreSQL's superuser-reserved slots, and the operator
    superuser's ``operator_connections`` must still fit on top of them, so
    the operator can connect in an incident even when every login is at its
    limit. ``background_connections`` and ``auxiliary_connections`` bound
    the rendered topology (``validate_topology``) and the web observers;
    they are not added to the total, which counts the logins themselves.
    """

    web_processes: int = 2
    web_threads: int = 8
    replicas: int = 1
    rollout_overlap: int = 2
    download_capacity: int = 4
    download_pool_per_process: int = 2
    background_connections: int = 36
    operator_connections: int = 8
    auxiliary_connections: int = 12
    # Web-login connections beyond the serving processes: pk-admin sessions
    # and the Testing load check's readers run in the web container on the
    # web login (#2333).
    web_spare_connections: int = 10
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
            or self.auxiliary_connections
            < 3 * self.web_processes * self.replicas * self.rollout_overlap
            or self.download_seconds > 900
            or self.download_seconds <= 5
            or self.drain_seconds <= 60
            or self.download_idle_seconds > 1200
            or self.drain_seconds > 1800
            or self.total_connections
            > self.database_connections - self.database_reserved
            or self.total_connections + self.operator_connections
            > self.database_connections
            # An in-flight download must be able to finish in a web stop.
            or self.download_seconds >= self.web_grace_seconds
            or not (
                self.download_seconds
                < self.download_idle_seconds
                < self.drain_seconds
                < self.server_timeout_seconds
                <= self.proxy_timeout_seconds
            )
        ):
            raise ConfigError("Runtime connection or timeout budgets are inconsistent.")

    def validate_topology(self, *, background_processes, operator_processes=1):
        """Account for concrete launched services, not an arbitrary budget label."""
        if (
            type(background_processes) is not int
            or background_processes < 0
            or type(operator_processes) is not int
            or operator_processes < 1
            or background_processes * self.rollout_overlap > self.background_connections
            or operator_processes > self.operator_connections
        ):
            raise ConfigError("Rendered process topology exceeds connection budgets.")

    @property
    def web_grace_seconds(self):
        """How long a web stop lets in-flight requests finish.

        This is Gunicorn's ``graceful_timeout``: the stop grace less
        STOP_MARGIN_SECONDS, so a worker still serving is killed, and logged,
        before Docker's own kill (#374).
        """
        return self.drain_seconds - STOP_MARGIN_SECONDS

    def login_limit(self, role):
        """The connection limit of one database login, for ``role``.

        ``role`` is a ``ServiceRole`` or ``"download"`` (a ``StrEnum``
        compares equal to its value, so no import is needed here). Compose
        stops a container before starting its replacement, so no two
        generations of a service ever hold connections at once (#2333);
        the background formulas below keep the overlap factor only because
        its slots serve each container's second process or timeout log.
        """
        serving = self.web_processes * self.replicas
        if role == "download":
            return serving * self.download_pool_per_process
        if role == "web":
            return (
                serving * (self.web_threads + WEB_OBSERVER_CONNECTIONS)
                + self.web_spare_connections
            )
        if role in {"worker", "mail-dispatch"}:
            # Two consumer processes (#336, and the two mail consumers),
            # each with a task, a lease-renewal and a timeout-log
            # connection (runtime_process.split_source and split_mail).
            return self.rollout_overlap * 3
        if role in {"scheduler", "config-installer", "credential-installer"}:
            # One loop or singleton session plus its timeout-log slot.
            return self.rollout_overlap
        if role == "backup-worker":
            return BACKUP_CONNECTIONS
        return ONE_SHOT_CONNECTIONS

    @property
    def total_connections(self):
        """The sum of every database login's limit, the inspect login's included.

        The login inventory is ``runtime_identities.database_identities``, so a
        login a release adds is counted without touching this function.
        """
        from .inspect_login import INSPECT_CONNECTIONS
        from .runtime_identities import database_identities

        return INSPECT_CONNECTIONS + sum(
            self.login_limit(role) for _, _, role, _ in database_identities()
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
