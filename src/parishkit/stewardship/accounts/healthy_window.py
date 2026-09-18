"""Observed authentication recovery, never an inference from elapsed silence."""

from datetime import timedelta

HEALTHY_WINDOW = timedelta(minutes=5)
MAX_OBSERVATION_GAP = timedelta(seconds=90)


def advance_window(since, previous, observed, *, healthy, failure=None):
    """Require bounded-gap successful probes after every retained failure.

    Inputs are SQL observation timestamps, not browser or Valkey wall clocks.
    An absent baseline, failed probe, long gap or newer failure restarts proof.
    A stale sample cannot advance or resolve an existing window.
    """
    if previous is not None and observed <= previous:
        return since, False
    if not healthy or (failure is not None and failure >= observed):
        return None, False
    if (
        since is None
        or previous is None
        or observed - previous > MAX_OBSERVATION_GAP
        or (failure is not None and failure >= since)
    ):
        return observed, False
    return since, observed - since >= HEALTHY_WINDOW
