"""Closed cleanup target vocabulary, independent of ORM and transport admission."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class CleanupCategory(StrEnum):
    """Exact record classes, excluding permanent history and live credentials."""

    BASELINE = "baselines"
    SESSION_DATA = "session_data"
    SESSION = "family_sessions"
    MINISTRY = "ministry_requests"
    OCCURRENCE = "occurrences"
    OCCURRENCE_EVENT = "occurrence_events"
    OUTBOX_EVENT = "outbox_events"
    OUTBOX = "outbox_messages"
    OUTBOX_RENDER = "outbox_renders"
    PROPOSAL = "proposals"
    REHEARSAL = "rehearsal_credentials"
    REHEARSAL_MAC = "rehearsal_macs"
    FULFILLMENT = "schedule_fulfillments"
    PIN = "source_pins"
    RECEIPT = "submission_receipts"
    SUBMISSION = "submissions"
    PRIOR_INVENTORY = "prior_inventory_targets"
    DIGEST_RECIPIENT = "daily_digest_recipients"
    DIGEST_READY = "daily_digest_ready"
    DIGEST_SNAPSHOT = "daily_digest_snapshots"
    DIGEST_FACT_PIN = "daily_digest_fact_pins"
    RECOVERY_REPLACEMENT = "recovery_replacements"


@dataclass(frozen=True, repr=False)
class CleanupTarget:
    """One private manifest identity, without census values or bearer secrets.

    SESSION_DATA uses the owning FamilySession UUID, not the Django session key.
    Its deletion unit must resolve that exact protected parent before deleting
    the metadata and parent together. The key never enters retained inventory.
    """

    category: CleanupCategory
    identifier: UUID

    def __post_init__(self):
        """Reject strings/coercions so arbitrary table names cannot become targets."""
        if not isinstance(self.category, CleanupCategory) or not isinstance(
            self.identifier, UUID
        ):
            raise ValueError("Cleanup targets require canonical category and identity.")
