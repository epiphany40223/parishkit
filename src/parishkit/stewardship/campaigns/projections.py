"""Prepare and verify immutable campaign/schedule projections within the installer."""

from .configuration import campaign_values, schedule_values
from .models import CampaignConfiguration, ScheduleRevision


def prepare_campaigns(snapshot, document, attribution):
    """Persist only validated configuration; preparation never creates a current
    draft.
    """
    sections = document["sections"]
    campaigns = {}
    for row in sections.get("campaigns", []):
        values = row["values"]
        interval = campaign_values(values)
        campaigns[row["id"]] = values
        CampaignConfiguration.objects.create(
            configuration=snapshot,
            record_id=row["id"],
            values=values,
            **{
                key: values[key]
                for key in ("name", "timezone", "start_date", "end_date")
            },
            starts_at=interval.start,
            ends_at=interval.end,
            **attribution,
        )
    for row in sections.get("schedules", []):
        values = row["values"]
        ScheduleRevision.objects.create(
            configuration=snapshot,
            record_id=row["id"],
            values=values,
            campaign_id=values["campaign_id"],
            kind=values["kind"],
            due_at=schedule_values(values, campaigns[values["campaign_id"]]),
            **attribution,
        )


def stored_campaigns(snapshot):
    """Round-trip indexed columns as well as opaque versioned references."""
    campaigns, result = {}, {"campaigns": [], "schedules": []}
    for row in sorted(
        snapshot.campaign_configurations.all(), key=lambda item: str(item.record_id)
    ):
        values = row.values | {
            "name": row.name,
            "timezone": row.timezone,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
        }
        interval = campaign_values(values)
        if (row.starts_at, row.ends_at) != (interval.start, interval.end):
            from parishkit.config import ConfigError

            raise ConfigError("Campaign boundary projection is invalid.")
        campaigns[str(row.record_id)] = values
        result["campaigns"].append({"id": str(row.record_id), "values": values})
    for row in sorted(
        snapshot.schedule_revisions.all(), key=lambda item: str(item.record_id)
    ):
        values = row.values | {"campaign_id": str(row.campaign_id), "kind": row.kind}
        campaign = campaigns.get(str(row.campaign_id))
        if campaign is None or row.due_at != schedule_values(values, campaign):
            from parishkit.config import ConfigError

            raise ConfigError("Schedule boundary projection is invalid.")
        result["schedules"].append({"id": str(row.record_id), "values": values})
    return result
