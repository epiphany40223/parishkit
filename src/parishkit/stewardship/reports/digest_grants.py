"""Daily digest permissions: scheduler metadata is not access to private reports."""


def add_digest_grants(tables, columns, *, worker):
    """Share opaque ownership; only the general worker can capture report inputs."""
    tables.setdefault("stewardship_daily_digest_preparation", set()).add("SELECT")
    tables.setdefault("stewardship_recovery_replacement", set()).add("SELECT")
    if worker:
        tables["stewardship_daily_digest_preparation"].add("UPDATE")
        for name in ("snapshot", "ready", "recipient"):
            tables.setdefault("stewardship_daily_digest_" + name, set()).update(
                {"SELECT", "INSERT"}
            )
    else:
        tables["stewardship_daily_digest_preparation"].add("INSERT")
        columns.setdefault("stewardship_daily_digest_snapshot", {}).setdefault(
            "SELECT", set()
        ).update(
            {
                "id",
                "campaign_id",
                "population_scope",
                "source_id",
                "submission_watermark",
                "timezone_configuration_id",
                "through_date",
            }
        )
