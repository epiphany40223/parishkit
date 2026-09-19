"""Cleanup uses control metadata commands, not private row-reading/deleting grants."""


def add_cleanup_web_reads(tables, columns):
    """Expose exact preview identifiers and progress, never new deletion authority."""
    for table in (
        "stewardship_production_request",
        "stewardship_testing_aggregate",
        "stewardship_production_manifest",
        "stewardship_production_cancellation",
        "stewardship_production_checkpoint",
    ):
        tables.setdefault(table, set()).add("SELECT")
    for table, names in {
        "stewardship_production_target": {"id", "request_id", "created_at", "position"},
        "stewardship_recovery_replacement": {"id", "previous_id", "replacement_id"},
        "stewardship_outbox_render": {"id", "message_id", "template_id"},
        "stewardship_fact_pin": {"id", "parent_kind", "parent_id"},
    }.items():
        columns.setdefault(table, {}).setdefault("SELECT", set()).update(names)


def add_cleanup_web_commands(tables, columns):
    """Admit guarded intent only; deletion and checkpoint claims remain worker-owned."""
    for table in (
        "stewardship_testing_aggregate",
        "stewardship_production_request",
        "stewardship_production_target",
        "stewardship_production_manifest",
        "stewardship_production_cancellation",
    ):
        tables.setdefault(table, set()).add("INSERT")
    tables.setdefault("stewardship_production_event", set()).add("SELECT")
    for table, names in {
        "stewardship_production_request": {
            "id",
            "state",
            "action",
            "command_id",
            "version",
            "actor_id",
            "correlation_id",
            "failure_reason",
        },
        "stewardship_campaign_credentials": {
            "go_live_gate",
            "rehearsal_epoch_id",
            "version",
        },
        "stewardship_rehearsal_epoch": {"state", "invalidated_at", "version"},
    }.items():
        columns.setdefault(table, {}).setdefault("UPDATE", set()).update(names)


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
