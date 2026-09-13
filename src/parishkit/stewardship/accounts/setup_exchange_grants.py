"""Closed metadata needed to recheck original-login source exchange ownership."""

SESSION_COLUMNS = frozenset(
    {"id", "principal_id", "revoked_at", "expires_at", "last_activity_at"}
)
ATTEMPT_COLUMNS = frozenset(
    {"id", "state", "owner_id", "session_id", "base_id", "source_task_id"}
)
TASK_COLUMNS = frozenset(
    {
        "id",
        "root_id",
        "created_at",
        "task_type",
        "domain_request_id",
        "initiated_by_id",
        "state",
        "fence",
        "worker_id",
        "lease_expires_at",
    }
)
LEASE_COLUMNS = frozenset(
    {"owner_id", "task_fence", "worker_id", "fence", "phase", "expires_at"}
)
CANDIDATE_METADATA = frozenset(
    {"id", "attempt_id", "target", "version", "fingerprint", "settings", "scrubbed_at"}
)


def extend_installer_permissions(target, tables, metadata):
    """Only ParishSoft's isolated target gains the source relay and safe proof."""
    if target != "parishsoft":
        return
    tables["stewardship_setup_source_exchange"] = {"SELECT", "UPDATE"}
    tables["stewardship_setup_sealed_credential"] = {"SELECT"}
    for table, names in (
        ("stewardship_setup_attempt", ATTEMPT_COLUMNS),
        ("stewardship_portal_session", SESSION_COLUMNS),
        ("stewardship_portal_user", {"id", "disabled"}),
        ("stewardship_task_run", TASK_COLUMNS),
        ("stewardship_source_lease", LEASE_COLUMNS),
    ):
        tables[table] = {"SELECT"}
        metadata[table] = set(names)
    metadata["stewardship_system_configuration"].update(
        {"mode", "restore_review_required", "current_campaign_id"}
    )


def add_worker_exchange_grants(tables, columns):
    """Worker reads only ParishSoft candidate metadata, never stored credentials."""
    tables["stewardship_setup_source_exchange"] = {"SELECT", "INSERT"}
    columns["stewardship_setup_sealed_credential"] = {"SELECT": set(CANDIDATE_METADATA)}
    columns["stewardship_portal_session"] = {"SELECT": set(SESSION_COLUMNS)}
    columns.setdefault("stewardship_portal_user", {}).setdefault(
        "SELECT", set()
    ).update({"id", "disabled", "email"})
    tables["stewardship_setup_source_result"] = {"SELECT", "INSERT"}
    columns["stewardship_setup_draft_section"] = {
        "SELECT": {"attempt_id", "step", "values", "scrubbed_at"}
    }
    columns["stewardship_setup_attempt"] = {
        "UPDATE": {"state", "actor_id", "correlation_id", "version"}
    }


def add_exchange_cleanup_grants(columns):
    """Web and scheduler can scrub under the terminal trigger, not read replies."""
    columns["stewardship_setup_source_exchange"] = {
        "SELECT": {"attempt_id", "scrubbed_at", "version"},
        "UPDATE": {
            "ciphertext",
            "scrubbed_at",
            "actor_id",
            "correlation_id",
            "version",
        },
    }


def add_web_setup_catalog_grants(tables, columns):
    """The original web wizard reads result bindings, never relay ciphertext."""
    tables["stewardship_setup_source_result"] = {"SELECT"}
    columns["stewardship_source_snapshot"]["SELECT"].update(
        {"state", "task_id", "source_fence"}
    )
    columns["stewardship_setup_source_exchange"]["SELECT"].update(
        {
            "id",
            "attempt_id",
            "credential_id",
            "credential_version",
            "fingerprint",
            "task_id",
            "task_fence",
            "source_fence",
        }
    )
