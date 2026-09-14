"""Family-scoped queries against exactly one protected source snapshot."""

from parishkit.stewardship.source.version_models import (
    SnapshotContact,
    SnapshotFamily,
    SnapshotMember,
    SnapshotMinistry,
    SnapshotRoster,
)

from .inputs import FormInputsUnavailable, census_inputs
from .ministry import InvalidMinistrySource, ministry_inputs


def load_census_inputs(snapshot_id, family_duid, *, configuration, document=None):
    """Read only this Family's records while the caller retains the snapshot pin.

    The owner must admit the Family and pin/lock the reconstructable snapshot
    before calling, retaining protection through all three materialized queries.
    Each membership query has the same explicit snapshot identity. Relationships
    are taken from those historical payloads, never today's global entity rows.
    """
    family = (
        SnapshotFamily.objects.filter(
            snapshot_id=snapshot_id, source_key=str(family_duid)
        )
        .select_related("payload")
        .first()
    )
    if family is None:
        raise FormInputsUnavailable("The Family form inputs are unavailable.")
    members = [
        row.payload.payload
        for row in SnapshotMember.objects.filter(
            snapshot_id=snapshot_id, payload__family_key=str(family_duid)
        ).select_related("payload")
    ]
    active_ids = [
        str(member["memberDUID"])
        for member in members
        if member["active"] and not member["deceased"]
    ]
    contacts = (
        {
            row.payload.owner_key: row.payload.payload
            for row in SnapshotContact.objects.filter(
                snapshot_id=snapshot_id,
                payload__owner_kind="member",
                payload__owner_key__in=active_ids,
            ).select_related("payload")
        }
        if "census" in configuration["modules"]
        else {}
    )
    ministries = None
    if "ministry" in configuration["modules"]:
        selected = [str(value) for value in configuration["ministry_duids"]]
        catalog = [
            row.payload.payload
            for row in SnapshotMinistry.objects.filter(
                snapshot_id=snapshot_id, source_key__in=selected
            ).select_related("payload")
        ]
        roster = [
            row.payload.payload
            for row in SnapshotRoster.objects.filter(
                snapshot_id=snapshot_id,
                payload__member_key__in=active_ids,
                payload__ministry_key__in=selected,
            ).select_related("payload")
        ]
        try:
            ministries = ministry_inputs(
                catalog,
                roster,
                member_duids=tuple(sorted(int(value) for value in active_ids)),
                selected_duids=configuration["ministry_duids"],
                document=document,
                organization_id=family.payload.organization_id,
            )
        except InvalidMinistrySource:
            raise FormInputsUnavailable(
                "The Family form inputs are unavailable."
            ) from None
    return census_inputs(
        family.payload.payload,
        members,
        contacts,
        configuration=configuration,
        ministries=ministries,
    )
