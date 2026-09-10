"""Prepare and verify immutable campaign/schedule projections within the installer."""

from .configuration import campaign_values, schedule_values
from .models import CampaignConfiguration, ScheduleRevision


def sql_boundaries_match(configuration_ids):
    """Recheck retained UTC projections against current database timezone rules.

    One set-based query verifies the loaded lineage, rather than adding a query
    per historical snapshot. Database/environment failures propagate separately
    from a resolved-boundary mismatch. No stored dates or rules are rewritten.
    """
    from django.db import connection

    if not configuration_ids:
        return True
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT NOT EXISTS (
                SELECT 1 FROM stewardship_campaign_configuration c
                WHERE c.configuration_id = ANY(%s)
                  AND (c.starts_at IS DISTINCT FROM stewardship_resolve_local_v1(
                        c.start_date::timestamp, c.timezone)
                    OR c.ends_at IS DISTINCT FROM stewardship_resolve_local_v1(
                        (c.end_date + 1)::timestamp, c.timezone))
            ) AND NOT EXISTS (
                SELECT 1 FROM stewardship_schedule_revision s
                JOIN stewardship_campaign_configuration c
                  ON c.configuration_id = s.configuration_id
                 AND c.record_id = s.campaign_id
                WHERE s.configuration_id = ANY(%s)
                  AND s.due_at IS DISTINCT FROM CASE
                    WHEN s.kind IN ('initial', 'reminder') THEN
                      stewardship_resolve_local_v1(
                        (s.values->>'date')::date + (s.values->>'time')::time,
                        c.timezone)
                    ELSE NULL END
            )""",
            [configuration_ids, configuration_ids],
        )
        return cursor.fetchone()[0]


def prepare_campaigns(snapshot, document, attribution):
    """Persist only validated configuration; preparation never creates a current
    draft.
    """
    sections = document["sections"]
    campaigns, intervals = {}, {}
    for row in sections.get("campaigns", []):
        values = row["values"]
        interval = campaign_values(values)
        intervals[row["id"]] = interval
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
            due_at=schedule_values(
                values,
                campaigns[values["campaign_id"]],
                interval=intervals[values["campaign_id"]],
            ),
            **attribution,
        )


def stored_campaigns(snapshot):
    """Round-trip indexed columns as well as opaque versioned references."""
    campaigns, intervals, result = {}, {}, {"campaigns": [], "schedules": []}
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
        if values != row.values or (row.starts_at, row.ends_at) != (
            interval.start,
            interval.end,
        ):
            from parishkit.config import ConfigError

            raise ConfigError("Campaign boundary projection is invalid.")
        campaigns[str(row.record_id)] = values
        intervals[str(row.record_id)] = interval
        result["campaigns"].append({"id": str(row.record_id), "values": values})
    for row in sorted(
        snapshot.schedule_revisions.all(), key=lambda item: str(item.record_id)
    ):
        values = row.values | {"campaign_id": str(row.campaign_id), "kind": row.kind}
        campaign = campaigns.get(str(row.campaign_id))
        if (
            values != row.values
            or campaign is None
            or row.due_at
            != schedule_values(
                values, campaign, interval=intervals[str(row.campaign_id)]
            )
        ):
            from parishkit.config import ConfigError

            raise ConfigError("Schedule boundary projection is invalid.")
        result["schedules"].append({"id": str(row.record_id), "values": values})
    return result
