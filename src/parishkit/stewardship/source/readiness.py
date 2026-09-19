"""Go-live needs a recent *full* observation of the current tenant and window.

A fresh delta cannot turn an old full load into readiness evidence. Permanent
manifest metadata suffices; no source census or contribution payload is read.
The freshness limit is the configured source-staleness threshold, measured from
the pre-read database timestamp, consistently with operational health.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.operational_sources import configured_policy

from .cursors import SCHEMA
from .models import SourceCurrent, SourceSnapshot
from .requests import _organization, _window

MANIFEST_FIELDS = (
    "id",
    "organization_id",
    "kind",
    "state",
    "started_at",
    "completed_at",
    "promoted_at",
    "cursor",
)


@dataclass(frozen=True)
class SourceReadiness:
    """Closed reasons and nonprivate immutable evidence for the Admin checklist."""

    reason: str
    current_id: UUID | None = None
    full_id: UUID | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None

    @property
    def ready(self):
        """Missing, mismatched, future-dated or stale evidence cannot pass."""
        return self.reason == "ready"


def source_readiness(scope):
    """Verify actual current promotion and its full anchor under the owning lock."""
    require_work_order()
    try:
        organization = _organization(scope)
    except PermissionError:
        return SourceReadiness("tenant_unavailable")
    current_id = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    if current_id is None:
        return SourceReadiness("full_refresh_required")
    current = SourceSnapshot.objects.values(*MANIFEST_FIELDS).get(pk=current_id)
    window = _window(scope).digest
    if not _matches(current, organization, window, scope.instant):
        return SourceReadiness("source_scope_changed", current_id)
    try:
        full_id = UUID(current["cursor"]["full_snapshot_id"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return SourceReadiness("full_refresh_required", current_id)
    full = (
        current
        if full_id == current_id
        else SourceSnapshot.objects.filter(pk=full_id).values(*MANIFEST_FIELDS).first()
    )
    if (
        full is None
        or full["kind"] != "full"
        or not _matches(full, organization, window, scope.instant)
        or (current["kind"] == "full" and full_id != current_id)
        or full["started_at"] > current["started_at"]
        or current["cursor"].get("full_started_at") != full["started_at"].isoformat()
    ):
        return SourceReadiness("full_refresh_required", current_id)
    expires_at = full["started_at"] + timedelta(
        seconds=configured_policy().source_stale_seconds
    )
    return SourceReadiness(
        "ready" if scope.instant < expires_at else "full_refresh_stale",
        current_id,
        full_id,
        full["started_at"],
        expires_at,
    )


def _matches(row, organization, window, instant):
    """A manifest must describe completed, promoted, correctly scoped truth."""
    return (
        row["state"] == "promoted"
        and row["organization_id"] == organization
        and row["completed_at"] is not None
        and row["promoted_at"] is not None
        and row["started_at"] <= row["completed_at"] <= row["promoted_at"] <= instant
        and row["cursor"].get("schema") == SCHEMA
        and row["cursor"].get("window_digest") == window
    )
