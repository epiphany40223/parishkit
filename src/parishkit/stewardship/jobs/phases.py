"""Closed progress vocabulary; provider messages and private values are never phases."""

from enum import StrEnum


class TaskPhase(StrEnum):
    """Coarse stages shared by owning workers, independent of terminal task state."""

    UNSPECIFIED = "unspecified"
    STARTING = "starting"
    PREPARING = "preparing"
    FETCHING = "fetching"
    VALIDATING = "validating"
    STAGING = "staging"
    PROMOTING = "promoting"
    RECONCILING = "reconciling"
    RENDERING = "rendering"
    DELIVERING = "delivering"
    VERIFYING = "verifying"
    COMPACTING = "compacting"
    DRAINING = "draining"
    DELETING = "deleting"
    UPLOADING = "uploading"


PHASE_VALUES = tuple(phase.value for phase in TaskPhase)
