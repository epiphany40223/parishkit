"""Activation recovery reads for scheduler; bounded checkpoint writes for worker."""


def add_catchup_grants(tables, columns, *, worker):
    """A scheduler can recover task metadata, never manufacture group completion."""
    for table in ("stewardship_catchup_checkpoint", "stewardship_catchup_failure"):
        tables.setdefault(table, set()).add("SELECT")
        if worker:
            tables[table].add("INSERT")
    if worker:
        tables.setdefault("stewardship_recovery_replacement", set()).update(
            {"SELECT", "INSERT"}
        )
        from .schedule_grants import add_schedule_planning_grants

        add_schedule_planning_grants(tables, columns)
        columns.setdefault("stewardship_activation_catchup", {}).setdefault(
            "UPDATE", set()
        ).update(
            {
                "phase",
                "cursor",
                "groups_completed",
                "items_completed",
                "completed_at",
                "failure_code",
                "version",
                "actor_id",
                "correlation_id",
            }
        )
