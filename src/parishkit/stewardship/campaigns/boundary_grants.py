"""Boundary writes remain constrained by exact live task and lifecycle SQL proof."""


def add_boundary_grants(tables, columns, *, worker):
    """Grant scheduler allocation and the worker's guarded start/close effects."""
    tables.setdefault("stewardship_campaign_boundary", set()).update(
        {"SELECT", "INSERT"}
    )
    if not worker:
        return
    for table in ("stewardship_campaign_transition", "stewardship_runtime_transition"):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    tables.setdefault("stewardship_campaign_control", set()).add("SELECT")
    columns.setdefault("stewardship_campaign", {}).setdefault("UPDATE", set()).update(
        {
            "state",
            "structural_locked",
            "ever_active",
            "active_token_generation_id",
            "readiness_revision",
            "version",
            "actor_id",
            "correlation_id",
        }
    )
    columns.setdefault("stewardship_system_configuration", {}).setdefault(
        "UPDATE", set()
    ).update({"mode", "current_campaign_id", "version", "actor_id", "correlation_id"})
    # Closing destroys bearer substitutions and revokes Family sessions. These
    # are write-only scrub columns; no ciphertext, MAC, or session key is readable.
    for table, read, write in (
        (
            "stewardship_family_token",
            {"campaign_id", "destroyed_at", "version"},
            {"ciphertext", "digest", "destroyed_at", "version"},
        ),
        ("stewardship_family_token_generation", set(), {"state", "version"}),
        (
            "stewardship_family_session",
            {"family_id", "revoked_at", "last_activity_at", "version"},
            {"revoked_at", "version"},
        ),
    ):
        permissions = columns.setdefault(table, {})
        if read:
            permissions.setdefault("SELECT", set()).update(read)
        permissions.setdefault("UPDATE", set()).update(write)
