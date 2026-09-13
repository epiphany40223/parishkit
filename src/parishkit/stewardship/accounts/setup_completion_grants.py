"""Only the atomic first-setup owner may exercise these added worker writes.

Every newly writable table is protected by the initial-completion SQL context
guard. Prepared public projections, YAML files, credentials and sessions remain
outside this authority. Ordinary background admission cannot select this path.
"""


def add_setup_completion_grants(tables, columns):
    """Supply trigger effects, not the installer's general configuration grants."""
    # Final activation rechecks every initial target's public ACK receipt. It
    # needs neither sealed credential identifiers/bytes nor provider context.
    columns.setdefault("stewardship_setup_credential_install", {}).setdefault(
        "SELECT", set()
    ).update({"id", "readiness_id", "request_id", "target", "fingerprint"})
    for table in (
        "stewardship_campaign_config_abort",
        "stewardship_campaign_config_intent",
    ):
        tables.setdefault(table, set()).add("SELECT")
    for table in (
        "stewardship_setup_completion",
        "stewardship_config_activation",
        "stewardship_config_checkpoint",
        "stewardship_policy_security_event",
        "stewardship_policy_epoch",
        "stewardship_schedule_selection",
        "stewardship_occurrence_transition",
    ):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    for table in ("stewardship_schedule_definition",):
        tables.setdefault(table, set()).update({"SELECT", "INSERT", "UPDATE"})
    tables.setdefault("stewardship_campaign", set()).update({"SELECT", "INSERT"})
    columns.setdefault("stewardship_campaign", {}).setdefault("UPDATE", set()).update(
        {"id", "active_configuration_id", "version", "actor_id", "correlation_id"}
    )
    columns.setdefault("stewardship_system_configuration", {}).setdefault(
        "UPDATE", set()
    ).update(
        {
            "id",
            "active_configuration_id",
            "configuration_sequence",
            "testing_recipient",
            "version",
            "actor_id",
            "correlation_id",
        }
    )
    for table in (
        "stewardship_campaign_boundary",
        "stewardship_schedule_occurrence",
    ):
        tables.setdefault(table, set()).update({"SELECT", "UPDATE"})
    columns.setdefault("stewardship_config_request", {}).setdefault(
        "UPDATE", set()
    ).add("id")
    _completion_scrub(columns)


def _completion_scrub(columns):
    """Erase temporary inputs without granting reads of private bodies.

    Foreign-target rows are visible only during this exact completion's live
    source fence. SQL cleanup guards permit null/empty scrub values only.
    """
    for table, read, write in (
        ("stewardship_setup_draft_section", {"version"}, {"values"}),
        (
            "stewardship_setup_sealed_credential",
            {"attempt_id", "scrubbed_at", "version"},
            {"settings", "ciphertext"},
        ),
        ("stewardship_setup_source_exchange", set(), {"ciphertext"}),
        (
            "stewardship_setup_mail_delivery",
            {"id", "attempt_id", "scrubbed_at", "state", "version"},
            {"mail", "state", "finished_at"},
        ),
        (
            "stewardship_setup_mail_exchange",
            {"delivery_id", "scrubbed_at", "version"},
            {"ciphertext"},
        ),
    ):
        grants = columns.setdefault(table, {})
        if read:
            grants.setdefault("SELECT", set()).update(read)
        grants.setdefault("UPDATE", set()).update(
            write | {"scrubbed_at", "actor_id", "correlation_id", "version"}
        )
