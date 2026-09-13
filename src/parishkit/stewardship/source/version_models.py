"""Normalized, content-addressed entity tables and per-snapshot membership maps.

Each entity type has its own table and version FK. Unchanged canonical payloads
are reused by identity/organization/digest; only lightweight membership rows are
new per snapshot. Relationships use stable upstream keys so a changed parent
does not force copies of every child. Staging validates those keys against the
complete snapshot before promotion; consumers never join across snapshots.
"""

import json

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class SourceVersion(ImmutableRecord):
    """Canonical JSON text is the durable payload; digests are SQL-verified too."""

    organization_id = models.PositiveBigIntegerField()
    source_key = models.CharField(max_length=200)
    digest = models.CharField(max_length=64)
    canonical = models.TextField()

    @property
    def payload(self):
        """Decode a fresh value so caller mutations cannot alter historical storage."""
        return json.loads(self.canonical)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=("organization_id", "source_key", "digest"),
                name="%(class)s_identity_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(organization_id__gt=0)
                & ~models.Q(source_key="")
                & models.Q(digest__regex=r"^[0-9a-f]{64}$"),
                name="%(class)s_valid_identity",
            ),
        ]


class SourceFamily(SourceVersion):
    """One Family payload version, independent of its Member/contact versions."""

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_family"


class SourceMember(SourceVersion):
    """One Member's census/status payload with a stable Family relationship."""

    family_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_member"


class SourceContact(SourceVersion):
    """One Family or Member contact payload, including phone/email semantics."""

    owner_kind = models.CharField(max_length=8)
    owner_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_contact"
        constraints = SourceVersion.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(owner_kind__in=("family", "member")),
                name="source_contact_owner_kind",
            ),
        ]


class SourceAddress(SourceVersion):
    """One independently versioned home/mailing address and its owner."""

    owner_kind = models.CharField(max_length=8)
    owner_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_address"
        constraints = SourceVersion.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(owner_kind__in=("family", "member")),
                name="source_address_owner_kind",
            ),
        ]


class SourceMinistry(SourceVersion):
    """Ministry identity and activity/selection metadata."""

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_ministry"


class SourceRoster(SourceVersion):
    """One Member/Ministry participation record, including role and dates."""

    member_key = models.CharField(max_length=200, db_index=True)
    ministry_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_roster"


class SourceFund(SourceVersion):
    """One giving fund identity and display/selection payload."""

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_fund"


class SourcePledge(SourceVersion):
    """One upstream pledge, scoped to a Family and fund."""

    family_key = models.CharField(max_length=200, db_index=True)
    fund_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_pledge"


class SourceContribution(SourceVersion):
    """One giving transaction, never aggregated away before canonical storage."""

    family_key = models.CharField(max_length=200, db_index=True)
    fund_key = models.CharField(max_length=200, db_index=True)

    class Meta(SourceVersion.Meta):
        db_table = "stewardship_source_contribution"


class SnapshotMembership(ImmutableRecord):
    """One entity identity appears at most once in each complete snapshot."""

    snapshot = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    source_key = models.CharField(max_length=200)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "source_key"), name="%(class)s_identity"
            ),
        ]


class SnapshotFamily(SnapshotMembership):
    """Family membership map; referenced payloads cannot be deleted."""

    payload = models.ForeignKey(SourceFamily, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_family"


class SnapshotMember(SnapshotMembership):
    """Member membership map with snapshot-scoped Family relationships."""

    payload = models.ForeignKey(SourceMember, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_member"


class SnapshotContact(SnapshotMembership):
    """Contact membership map."""

    payload = models.ForeignKey(SourceContact, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_contact"


class SnapshotAddress(SnapshotMembership):
    """Address membership map."""

    payload = models.ForeignKey(SourceAddress, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_address"


class SnapshotMinistry(SnapshotMembership):
    """Ministry membership map."""

    payload = models.ForeignKey(SourceMinistry, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_ministry"


class SnapshotRoster(SnapshotMembership):
    """Roster membership map."""

    payload = models.ForeignKey(SourceRoster, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_roster"


class SnapshotFund(SnapshotMembership):
    """Fund membership map."""

    payload = models.ForeignKey(SourceFund, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_fund"


class SnapshotPledge(SnapshotMembership):
    """Pledge membership map."""

    payload = models.ForeignKey(SourcePledge, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_pledge"


class SnapshotContribution(SnapshotMembership):
    """Contribution membership map."""

    payload = models.ForeignKey(SourceContribution, on_delete=models.PROTECT)

    class Meta(SnapshotMembership.Meta):
        db_table = "stewardship_snapshot_contribution"


ENTITY_MODELS = {
    "family": (SourceFamily, SnapshotFamily),
    "member": (SourceMember, SnapshotMember),
    "contact": (SourceContact, SnapshotContact),
    "address": (SourceAddress, SnapshotAddress),
    "ministry": (SourceMinistry, SnapshotMinistry),
    "roster": (SourceRoster, SnapshotRoster),
    "fund": (SourceFund, SnapshotFund),
    "pledge": (SourcePledge, SnapshotPledge),
    "contribution": (SourceContribution, SnapshotContribution),
}
