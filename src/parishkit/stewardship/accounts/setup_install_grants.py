"""Safe frozen-setup proof and target-only initial credential intake grants."""

from .setup_exchange_grants import SESSION_COLUMNS


def extend_initial_permissions(target, tables, metadata):
    """No target gains another target's ciphertext or any Family/source payload."""
    if target not in {"parishsoft", "google_workspace", "slack"}:
        return
    tables["stewardship_secret_request"].add("INSERT")
    tables["stewardship_sealed_credential_staging"].add("INSERT")
    tables["stewardship_provider_context"].add("INSERT")
    tables["stewardship_setup_credential_install"] = {"SELECT", "INSERT"}
    tables["stewardship_setup_config_intent"] = {"SELECT"}
    # SetupAttempt is exclusively ownership/state metadata, unlike PortalSession.
    tables["stewardship_setup_attempt"] = {"SELECT"}
    metadata.pop("stewardship_setup_attempt", None)
    for table, names in (
        (
            "stewardship_setup_readiness_binding",
            {
                "id",
                "intent_id",
                "source_result_id",
                "mail_delivery_id",
                "slack_delivery_id",
            },
        ),
        ("stewardship_setup_source_result", {"id", "exchange_id"}),
        ("stewardship_portal_session", SESSION_COLUMNS | {"authenticated_at"}),
        ("stewardship_portal_user", {"id", "email", "disabled"}),
        ("stewardship_address_rule", {"configuration_id", "email", "roles"}),
    ):
        tables.setdefault(table, {"SELECT"})
        metadata.setdefault(table, set()).update(names)
    for table, names in (
        (
            "stewardship_setup_mail_delivery",
            {"id", "credential_id", "credential_version", "fingerprint", "state"},
        ),
        (
            "stewardship_setup_slack_delivery",
            {"id", "credential_id", "credential_version", "fingerprint", "state"},
        ),
        (
            "stewardship_setup_source_exchange",
            {"id", "credential_id", "credential_version", "fingerprint"},
        ),
    ):
        if table not in tables:
            tables[table] = {"SELECT"}
            metadata[table] = set(names)
        elif table in metadata:
            metadata[table].update(names)
    metadata["stewardship_system_configuration"].update(
        {"mode", "restore_review_required", "current_campaign_id"}
    )
