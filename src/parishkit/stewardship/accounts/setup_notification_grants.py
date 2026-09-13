"""No private Slack token leaves its isolated target during setup readiness."""

from .setup_exchange_grants import ATTEMPT_COLUMNS, SESSION_COLUMNS


def add_notification_cleanup(tables, columns):
    """The scheduler can classify stale or uncertain results, never submit."""
    tables["stewardship_setup_slack_delivery"] = {"SELECT"}
    columns["stewardship_setup_slack_delivery"] = {
        "UPDATE": {"state", "finished_at", "actor_id", "correlation_id", "version"}
    }


def extend_slack_permissions(tables, metadata):
    """Only Slack's installer reads its own staged key and advances the journal."""
    tables["stewardship_setup_slack_delivery"] = {"SELECT", "UPDATE"}
    tables["stewardship_setup_sealed_credential"] = {"SELECT"}
    tables["stewardship_audit_context"] = {"INSERT"}
    for table, names in (
        ("stewardship_setup_attempt", ATTEMPT_COLUMNS | {"version"}),
        ("stewardship_portal_session", SESSION_COLUMNS),
        ("stewardship_portal_user", {"id", "disabled", "email"}),
        ("stewardship_task_run", {"id", "created_at"}),
        ("stewardship_address_rule", {"email", "configuration_id", "roles"}),
        (
            "stewardship_setup_draft_section",
            {"attempt_id", "step", "scope_digest", "scrubbed_at"},
        ),
    ):
        tables[table] = {"SELECT"}
        metadata[table] = set(names)
    metadata["stewardship_system_configuration"].update(
        {"mode", "restore_review_required", "current_campaign_id"}
    )
