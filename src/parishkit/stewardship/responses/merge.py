"""Deterministic field reconciliation without rewriting Family provenance.

This module has no persistence or publication authority. The owning service
supplies only a prior effective field proposal, and supplies an Admin edit only
when its publication/resolution outcome is terminal. Decisions such as ignored
or approved do not by themselves resolve a Family's proposal.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from .comparison import ValueKind, canonical_value


class MergeState(StrEnum):
    """Derived field states, separate from staff decisions and execution history."""

    CURRENT = "current"
    SOURCE_CHANGED = "source_changed"
    FAMILY_CHANGED = "family_changed"
    UPSTREAM_CAUGHT_UP = "upstream_caught_up"
    ADMIN_RESOLVED = "admin_resolved"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KnownValue:
    """Distinguish a known null from a source field that cannot be reconstructed."""

    available: bool
    value: object = None

    def __post_init__(self):
        """Unavailable values must not carry a hidden or fabricated current value."""
        if type(self.available) is not bool or (
            not self.available and self.value is not None
        ):
            raise ValueError("Invalid source availability metadata.")


@dataclass(frozen=True)
class PriorChange:
    """Immutable Family proposal, with an optional confirmed administrative edit."""

    baseline: KnownValue
    submitted: object
    resolved_admin_edit: KnownValue = KnownValue(False)


@dataclass(frozen=True)
class EffectiveValue:
    """Only the effective display value; never include a hidden competing value."""

    value: KnownValue
    state: MergeState
    changed: bool
    conflict: bool


def merge_value(
    kind: ValueKind, current: KnownValue, prior=None, *, dialing_region=None
):
    """Apply the specified three-way merge in canonical, not display, values.

    If a prior submission did not change this field, pass no PriorChange. The
    defensive equal-baseline branch also prevents an unchanged complete answer
    from becoming a Family proposal after a later upstream change. Unavailable
    source is not evidence of either resolution or conflict: retain a prior
    Family value, if any, but do not invent an upstream value.
    """

    def key(value):
        return canonical_value(kind, value, dialing_region=dialing_region)

    def result(value, state, *, changed=False, conflict=False):
        return EffectiveValue(deepcopy(value), state, changed, conflict)

    if not isinstance(kind, ValueKind) or not isinstance(current, KnownValue):
        raise TypeError("Typed field and source availability are required.")
    if prior is not None and not isinstance(prior, PriorChange):
        raise TypeError("Trusted prior field change is required.")
    if prior is None:
        if current.available:
            key(current.value)
        return result(
            current, MergeState.CURRENT if current.available else MergeState.UNAVAILABLE
        )

    submitted = key(prior.submitted)
    baseline = key(prior.baseline.value) if prior.baseline.available else None
    if prior.baseline.available and baseline == submitted:
        # A complete unchanged answer is not an intent to overwrite later data.
        if current.available:
            state = (
                MergeState.CURRENT
                if key(current.value) == baseline
                else MergeState.SOURCE_CHANGED
            )
        else:
            state = MergeState.UNAVAILABLE
        return result(current, state)
    if not current.available:
        return result(
            KnownValue(True, prior.submitted), MergeState.UNAVAILABLE, changed=True
        )
    current_key = key(current.value)
    if current_key == submitted:
        return result(current, MergeState.UPSTREAM_CAUGHT_UP)
    if prior.resolved_admin_edit.available and current_key == key(
        prior.resolved_admin_edit.value
    ):
        return result(current, MergeState.ADMIN_RESOLVED)
    if prior.baseline.available and current_key == baseline:
        return result(
            KnownValue(True, prior.submitted), MergeState.FAMILY_CHANGED, changed=True
        )
    return result(
        KnownValue(True, prior.submitted),
        MergeState.CONFLICT,
        changed=True,
        conflict=True,
    )
