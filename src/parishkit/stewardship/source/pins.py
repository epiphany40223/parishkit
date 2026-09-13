"""Explicit retained-parent source references, serialized with corpus compaction."""

from datetime import datetime
from uuid import UUID

from django.db import transaction

from .canonical import InvalidSourcePayload
from .leases import _now
from .snapshot_models import SourceSnapshot, SourceSnapshotPin
from .snapshots import _admit


def pin_snapshot(snapshot_id, *, parent_kind, parent_id, admit, expires_at=None):
    """A parent's pin and a compactor compete for the same snapshot row lock."""
    if not isinstance(parent_id, UUID):
        raise ValueError("Source protection requires a parent UUID.")
    with transaction.atomic():
        snapshot = SourceSnapshot.objects.select_for_update().get(pk=snapshot_id)
        _admit(admit, "pin", snapshot)
        if snapshot.state != "promoted" or snapshot.compacted_at is not None:
            raise InvalidSourcePayload("The source input is no longer reconstructable.")
        if parent_kind == "form_baseline":
            if (
                not isinstance(expires_at, datetime)
                or expires_at.tzinfo is None
                or expires_at.utcoffset() is None
                or expires_at <= _now()
            ):
                raise ValueError(
                    "A Family baseline pin requires a future aware expiry."
                )
        elif expires_at is not None:
            raise ValueError("Retained source inputs require explicit parent release.")
        pin, created = SourceSnapshotPin.objects.get_or_create(
            snapshot=snapshot,
            parent_kind=parent_kind,
            parent_id=parent_id,
            defaults={"expires_at": expires_at},
        )
        if not created and pin.expires_at != expires_at:
            raise ValueError("An existing parent pin has a different expiry.")
        return pin


def release_snapshot_pin(pin_id, *, admit):
    """Release only the named parent's protection; no other pin is shortened."""
    with transaction.atomic():
        existing = SourceSnapshotPin.objects.filter(pk=pin_id).first()
        if existing is None:
            _admit(admit, "unpin", None)
            return False
        snapshot = SourceSnapshot.objects.select_for_update().get(
            pk=existing.snapshot_id
        )
        _admit(admit, "unpin", snapshot)
        return SourceSnapshotPin.objects.filter(pk=pin_id).delete()[0] == 1
