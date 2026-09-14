"""Family-scoped queries against exactly one protected source snapshot."""

from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.version_models import (
    SnapshotContact,
    SnapshotContribution,
    SnapshotFamily,
    SnapshotMember,
    SnapshotMinistry,
    SnapshotPledge,
    SnapshotRoster,
)

from .financial_inputs import (
    InvalidFinancialSource,
    financial_definition,
    financial_inputs,
    giving_observation,
)
from .inputs import FormInputsUnavailable, census_inputs
from .ministry import InvalidMinistrySource, ministry_inputs


def load_census_inputs(
    snapshot_id,
    family_duid,
    *,
    configuration,
    document=None,
    campaign_id=None,
    parish_name="",
):
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
        configuration=configuration
        | {
            # Non-legacy page slots select their sole revision in the immutable
            # content section, not the older campaign.content_versions mapping.
            "content_versions": configuration["content_versions"]
            | {
                row["values"]["slot"]: row["id"]
                for row in (document or {}).get("sections", {}).get("content", [])
                if row["values"]["campaign_id"] == str(campaign_id)
                and row["values"]["kind"] == "page"
                and row["values"]["slot"] == "member_census"
            }
        },
        parish_name=parish_name,
        ministries=ministries,
        financial=load_financial_inputs(
            snapshot_id,
            family_duid,
            configuration=configuration,
            campaign_id=campaign_id,
        )
        if "financial" in configuration["modules"]
        else None,
    )


def load_financial_inputs(snapshot_id, family_duid, *, configuration, campaign_id):
    """Read only this Family's mapped giving from the caller's protected snapshot.

    Both membership and version ownership are qualified in each query. Missing
    window coverage avoids detail queries altogether; neither missing mappings
    nor an old census-only snapshot can be used as proof of a zero balance.
    """
    try:
        definition = financial_definition(configuration, campaign_id=campaign_id)
        cursor = SourceSnapshot.objects.values_list("cursor", flat=True).get(
            pk=snapshot_id
        )
        observation = giving_observation(cursor, definition)

        def records(model):
            """Do not fetch unrelated households, funds or snapshots for aggregation."""
            if observation is None:
                return ()
            return (
                row.payload.payload
                for row in model.objects.filter(
                    snapshot_id=snapshot_id,
                    payload__family_key=str(family_duid),
                    payload__fund_key__in=[
                        str(key) for key in definition.comparison.funds
                    ],
                )
                .select_related("payload")
                .iterator(chunk_size=200)
            )

        return financial_inputs(
            definition,
            observation,
            family_duid=family_duid,
            pledges=records(SnapshotPledge),
            contributions=records(SnapshotContribution),
        )
    except InvalidFinancialSource:
        raise FormInputsUnavailable("The Family form inputs are unavailable.") from None
