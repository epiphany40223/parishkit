"""Current, Family-scoped source and public content for the preparation owner.

Every query stays on one protected promoted snapshot. This reader has no send
authority and never overlays a proposed census value onto recipient selection.
"""

from dataclasses import dataclass, field
from uuid import UUID

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.families import FamilyRecipients, family_recipients
from parishkit.stewardship.source.family_names import (
    family_display_name,
    name_placeholders,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshots import read_snapshot
from parishkit.stewardship.source.version_models import (
    SnapshotContact,
    SnapshotFamily,
    SnapshotMember,
)
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_mail_values import campaign_values
from .recipient_models import RecipientRefusal, RecipientRefusalResolution


@dataclass(frozen=True)
class FamilyMailSource:
    """A materialized, private single-household input, not a retained source copy."""

    snapshot_id: UUID
    generation: int
    recipients: FamilyRecipients = field(repr=False)
    # The name placeholders (family_names.name_placeholders): family_name,
    # head_salutation, family_member_names and all_family_member_names.
    names: dict[str, str] = field(repr=False)
    active_members: int


def _name(values):
    """A Member as ``heads_salutation_name`` reads one: stable source names only."""
    return {"first": values.get("firstName"), "last": values.get("lastName")}


def household_names(family, members):
    """The name placeholders of one Family from its snapshot payloads (#471).

    ``members`` maps each of the Family's Member source keys to its payload.
    The heads are the Family's ``active_head_duids``, every one of them, not
    only those with an eligible email address, so an email greets the same
    people a Family page does. Every active, listed (not deceased) Member is
    named in DUID order, exactly the Members the Family form lists
    (``responses.source_inputs``). A missing name falls back to the Family's
    display name, as the Testing banner and receipts always did.
    """
    family_name = family_display_name(family, "Family")
    heads = [_name(members[str(head)]) for head in sorted(family["active_head_duids"])]
    listed = [
        _name(member)
        for key, member in sorted(members.items(), key=lambda item: int(item[0]))
        if member["active"] is True and not member["deceased"]
    ]
    return name_placeholders(family_name, heads, listed)


def load_family_mail_source(family):
    """Read only one household and its unresolved organization-scoped refusals."""
    require_work_order()
    current = SourceCurrent.objects.get(singleton=True)
    population = CampaignCredentialState.objects.get(campaign_id=family.campaign_id)
    if (
        population.population_dirty
        or population.source_snapshot_id != current.snapshot_id
        or population.source_generation != current.generation
        or family.source_generation != current.generation
    ):
        raise PermissionError("Family mail requires current source reconciliation.")
    key = str(family.family_duid)
    with read_snapshot(current.snapshot_id, metadata_only=True):
        row = (
            SnapshotFamily.objects.filter(
                snapshot_id=current.snapshot_id, source_key=key
            )
            .select_related("payload")
            .first()
        )
        if row is None:
            raise StorageInvariantError("Family mail source is unavailable.")
        value = row.payload.payload
        members = {
            member.source_key: member.payload.payload
            for member in SnapshotMember.objects.filter(
                snapshot_id=current.snapshot_id, payload__family_key=key
            ).select_related("payload")
        }
        contacts = {
            contact.source_key: contact.payload.payload
            for contact in SnapshotContact.objects.filter(
                snapshot_id=current.snapshot_id,
                payload__owner_kind="member",
                payload__owner_key__in=[
                    str(head) for head in value["active_head_duids"]
                ],
            ).select_related("payload")
        }
        suppressed = frozenset(
            RecipientRefusal.objects.filter(
                organization_id=current.organization_id, family_duid=family.family_duid
            )
            .exclude(pk__in=RecipientRefusalResolution.objects.values("refusal_id"))
            .values_list("address", flat=True)
        )
        (projection,) = family_recipients(
            {"family": {key: value}, "member": members, "contact": contacts},
            suppressed_addresses=suppressed,
        )
        # Names come from the source snapshot only, never from a proposed
        # replacement census value.
        return FamilyMailSource(
            current.snapshot_id,
            current.generation,
            projection,
            household_names(value, members),
            sum(member["active"] is True for member in members.values()),
        )


def public_values(source, *, parish, campaign, public_origin):
    """Format parish civil dates independently of the worker's timezone.

    The assembled runtime owns validation of public_origin. No code, token or
    family-specific URL is accepted as a public substitution.
    """
    return {
        **campaign_values(parish=parish, campaign=campaign),
        **source.names,
        "generic_family_url": public_origin + "/",
        "pronoun": "I" if source.active_members == 1 else "We",
    }
