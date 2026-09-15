"""Cleanup uses control metadata commands, not private row-reading/deleting grants."""


def add_cleanup_grants(tables, columns, *, worker):
    """Keep scheduler recovery metadata separate from worker batch commands."""
    for table in (
        "stewardship_production_request",
        "stewardship_production_manifest",
        "stewardship_production_event",
        "stewardship_production_cancellation",
        "stewardship_production_checkpoint",
    ):
        tables.setdefault(table, set()).add("SELECT")
    if not worker:
        return
    tables["stewardship_production_checkpoint"].add("INSERT")
    columns.setdefault("stewardship_production_request", {}).setdefault(
        "UPDATE", set()
    ).update(
        {
            "id",
            "state",
            "action",
            "command_id",
            "version",
            "actor_id",
            "correlation_id",
            "failure_reason",
            "run_id",
            "task_fence",
            "worker_id",
        }
    )
