"""Exact immutable input binding shared by preparation and final activation.

No dataclass authorizes a command. Owning services load these values under the
common work order, verify current Admin/task admission and compare the complete
binding before using any generation. Activity timestamps deliberately are not
source or eligibility revisions.
"""

import re
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TokenPreparationInputs:
    """A generation must cover precisely this source, population, epoch and key set."""

    configuration_id: UUID
    source_snapshot_id: UUID
    source_generation: int
    credential_epoch: UUID
    key_inventory_digest: str
    eligibility_digest: str
    eligible_count: int

    def __post_init__(self):
        """Reject coercions and malformed metadata before allocating durable work."""
        if any(
            not isinstance(value, UUID)
            for value in (
                self.configuration_id,
                self.source_snapshot_id,
                self.credential_epoch,
            )
        ):
            raise ValueError("Token preparation requires canonical input identities.")
        if (
            type(self.source_generation) is not int
            or not 1 <= self.source_generation < 2**63
            or type(self.eligible_count) is not int
            or not 0 <= self.eligible_count < 2**63
        ):
            raise ValueError("Token preparation requires bounded input counts.")
        if any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (self.key_inventory_digest, self.eligibility_digest)
        ):
            raise ValueError("Token preparation requires exact input fingerprints.")

    @classmethod
    def retained(cls, preparation):
        """Reconstitute only immutable metadata; no Family or secret values are read."""
        return cls(
            configuration_id=preparation.configuration_id,
            source_snapshot_id=preparation.source_snapshot_id,
            source_generation=preparation.source_generation,
            credential_epoch=preparation.credential_epoch,
            key_inventory_digest=preparation.key_inventory_digest,
            eligibility_digest=preparation.eligibility_digest,
            eligible_count=preparation.eligible_count,
        )

    def covers(self, generation):
        """Ready metadata is comparable without enumerating Family tokens again."""
        return (
            generation.state == "ready"
            and generation.configuration_id == self.configuration_id
            and generation.source_snapshot_id == self.source_snapshot_id
            and generation.source_generation == self.source_generation
            and generation.credential_epoch == self.credential_epoch
            and generation.key_inventory_digest == self.key_inventory_digest
            and generation.coverage_digest == self.eligibility_digest
            and generation.coverage_count == self.eligible_count
            and generation.completed_at is not None
        )
