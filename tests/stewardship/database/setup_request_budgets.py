"""Short real deadlines for setup scenarios whose provider exchange is synthetic."""

from parishkit.parishsoft import ParishSoftConfig
from parishkit.stewardship.source import (
    setup_final_execution,
    setup_final_loading,
    setup_final_tasks,
    setup_loading,
)
from parishkit.stewardship.source.leases import reserve_source_request


def short_request_budgets(monkeypatch):
    """Keep real SQL clocks, locks and drainage, with smaller fake-I/O budgets.

    The actual request payload uses the smaller timeout, so the lease still
    covers that request plus the transport's full five-second forced drain.
    Only these synthetic setup scenarios reduce the additional safety margin
    and retry delay. Production defaults and transport contract tests are intact.
    """

    def configuration(*args, **kwargs):
        """Pass a genuinely short timeout to the unchanged transport preflight."""
        return ParishSoftConfig(*args, **{**kwargs, "timeout": 0.01})

    def reserve(claim, *, timeout_seconds, safety_seconds):
        """Persist an actual deadline; never rewrite the clock or bypass fences."""
        return reserve_source_request(
            claim, timeout_seconds=timeout_seconds, safety_seconds=1
        )

    for owner in (setup_loading, setup_final_loading):
        monkeypatch.setattr(owner, "ParishSoftConfig", configuration)
        monkeypatch.setattr(owner, "reserve_source_request", reserve)
    for owner in (setup_final_execution, setup_final_tasks):
        monkeypatch.setattr(owner, "retry_delay", lambda attempt: 1)
