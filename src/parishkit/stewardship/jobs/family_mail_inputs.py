"""Current, Family-scoped source and public content for the preparation owner.

Every query stays on one protected promoted snapshot. This reader has no send
authority and never overlays a proposed census value onto recipient selection.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.families import FamilyRecipients, family_recipients
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshots import read_snapshot
from parishkit.stewardship.source.version_models import (
    SnapshotContact,
    SnapshotFamily,
    SnapshotMember,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.presentation import campaign_year, parish_date

from .recipient_models import RecipientRefusal, RecipientRefusalResolution


@dataclass(frozen=True)
class FamilyMailSource:
    """A materialized, private single-household input, not a retained source copy."""

    snapshot_id: UUID
    generation: int
    recipients: FamilyRecipients = field(repr=False)
    family_name: str = field(repr=False)
    member_names: str = field(repr=False)
    active_members: int


def _name(values):
    """Use stable source names without inventing missing personal information."""
    return " ".join(
        value.strip()
        for key in ("firstName", "lastName")
        if isinstance(value := values.get(key), str) and value.strip()
    )


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
    with read_snapshot(current.snapshot_id):
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
        # Names describe the heads who actually have eligible addresses, not
        # unrelated adults/minors or a proposed replacement census contact.
        names = [
            _name(members[str(head)])
            for head in sorted(value["active_head_duids"])
            if any(
                item["valid"] and item["value"] in projection.eligible
                for item in contacts.get(f"member:{head}", {}).get("emails", [])
            )
        ]
        return FamilyMailSource(
            current.snapshot_id,
            current.generation,
            projection,
            value.get("mailingName") or _name(value) or "Family",
            " and ".join(name for name in names if name),
            sum(member["active"] is True for member in members.values()),
        )


def public_values(source, *, parish, campaign, public_origin):
    """Format parish civil dates independently of the worker's timezone.

    The assembled runtime owns validation of public_origin. No code, token or
    family-specific URL is accepted as a public substitution.
    """
    financial = campaign["financial"]
    start = parish_date(date.fromisoformat(financial["start"])) if financial else ""
    end = parish_date(date.fromisoformat(financial["end"])) if financial else ""
    return {
        "parish_name": parish["name"],
        "parish_website": parish["website"],
        "parish_phone": parish["phone"],
        "campaign_name": campaign["name"],
        "campaign_start": parish_date(date.fromisoformat(campaign["start_date"])),
        "campaign_end": parish_date(date.fromisoformat(campaign["end_date"])),
        "campaign_timezone": campaign["timezone"],
        "campaign_year": campaign_year(campaign),
        "financial_start": start,
        "financial_end": end,
        "financial_period": f"{start} – {end}" if financial else "",
        "family_name": source.family_name,
        "family_member_names": source.member_names,
        "generic_family_url": public_origin + "/",
        "pronoun": "I" if source.active_members == 1 else "We",
    }
