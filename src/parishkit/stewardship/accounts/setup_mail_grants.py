"""Setup-only mail journal authority; no Family data or staged private keys."""

from .setup_exchange_grants import (
    ATTEMPT_COLUMNS,
    CANDIDATE_METADATA,
    SESSION_COLUMNS,
    TASK_COLUMNS,
)


def mail_runtime_grants():
    """Minimal future consumer scope, separately checked from general worker SQL."""
    tables = {
        table: {"SELECT"}
        for table in (
            "django_migrations",
            "stewardship_configuration_version",
            "stewardship_system_configuration",
            "stewardship_setup_attempt",
            "stewardship_task_run",
            "stewardship_task_event",
            "stewardship_address_rule",
            "stewardship_setup_config_intent",
            "stewardship_config_request",
            "stewardship_config_checkpoint",
            "stewardship_config_activation",
        )
    }
    tables["stewardship_setup_mail_delivery"] = {"SELECT", "UPDATE"}
    tables["stewardship_setup_mail_exchange"] = {"SELECT", "INSERT"}
    tables["stewardship_task_run"].add("UPDATE")
    tables["stewardship_task_event"].add("INSERT")
    tables["stewardship_audit_event"] = {"INSERT"}
    tables["stewardship_audit_context"] = {"INSERT"}
    tables["stewardship_secret_request"] = {"SELECT"}
    tables["stewardship_credential_consumer_ack"] = {"SELECT", "INSERT"}
    columns = {
        "stewardship_secret_request": {"UPDATE": {"id"}},
        "stewardship_parish": {"SELECT": {"id", "configuration_id"}},
        "stewardship_setup_sealed_credential": {"SELECT": set(CANDIDATE_METADATA)},
        "stewardship_portal_session": {"SELECT": set(SESSION_COLUMNS)},
        "stewardship_portal_user": {"SELECT": {"id", "disabled", "email"}},
        "stewardship_setup_draft_section": {
            "SELECT": {"attempt_id", "step", "scope_digest", "scrubbed_at"}
        },
    }
    from .campaign_mail_grants import add_campaign_mail_grants

    add_campaign_mail_grants(tables, columns)
    from parishkit.stewardship.jobs.family_dispatch_grants import add_dispatch_grants

    add_dispatch_grants(tables, columns)
    return tables, columns


def add_setup_mail_cleanup_grants(tables, columns, *, read_payload=False):
    """Expiry can erase sample content and classify stale work, never claim/send."""
    from .setup_notification_grants import add_notification_cleanup

    add_notification_cleanup(tables, columns)
    if read_payload:
        tables["stewardship_setup_mail_delivery"] = {"SELECT"}
    columns["stewardship_setup_mail_delivery"] = {
        "SELECT": {
            "id",
            "created_at",
            "version",
            "attempt_id",
            "attempt_version",
            "credential_id",
            "credential_version",
            "fingerprint",
            "task_id",
            "state",
            "run_id",
            "task_fence",
            "worker_id",
            "deadline_at",
            "scrubbed_at",
        },
        "UPDATE": {
            "mail",
            "state",
            "finished_at",
            "scrubbed_at",
            "actor_id",
            "correlation_id",
            "version",
        },
    }
    columns["stewardship_setup_mail_exchange"] = {
        "SELECT": {"delivery_id", "scrubbed_at", "version"},
        "UPDATE": {
            "ciphertext",
            "scrubbed_at",
            "actor_id",
            "correlation_id",
            "version",
        },
    }
    columns["stewardship_setup_sealed_credential"]["SELECT"].update(CANDIDATE_METADATA)
    if "stewardship_setup_draft_section" in columns:
        columns["stewardship_setup_draft_section"]["SELECT"].update(
            {"attempt_id", "step", "scope_digest", "scrubbed_at"}
        )


def extend_workspace_permissions(tables, metadata):
    """The target reads no rendered mail, Family records or foreign staged secrets."""
    tables["stewardship_setup_mail_exchange"] = {"SELECT", "UPDATE"}
    tables["stewardship_setup_sealed_credential"] = {"SELECT"}
    for table, names in (
        ("stewardship_setup_attempt", ATTEMPT_COLUMNS | {"version"}),
        ("stewardship_portal_session", SESSION_COLUMNS),
        ("stewardship_portal_user", {"id", "disabled", "email"}),
        ("stewardship_task_run", TASK_COLUMNS),
        ("stewardship_address_rule", {"email", "configuration_id", "roles"}),
        (
            "stewardship_setup_draft_section",
            {"attempt_id", "step", "scope_digest", "scrubbed_at"},
        ),
        (
            "stewardship_setup_mail_delivery",
            {
                "id",
                "attempt_id",
                "attempt_version",
                "credential_id",
                "credential_version",
                "fingerprint",
                "task_id",
                "state",
            },
        ),
    ):
        tables[table] = {"SELECT"}
        metadata[table] = set(names)
    metadata["stewardship_system_configuration"].update(
        {"mode", "restore_review_required", "current_campaign_id"}
    )
