"""Inactive link preparation adds no private-token read or campaign-mode authority."""


def add_token_preparation_grants(tables, columns, *, worker):
    """Both task owners read metadata; only the general worker writes sealed staging."""
    for table in (
        "stewardship_production_tokens",
        "stewardship_production_token_cancel",
        "stewardship_family_token_generation",
        "stewardship_credential_key_state",
        "stewardship_credential_deployment",
        "stewardship_source_current",
    ):
        tables.setdefault(table, set()).add("SELECT")
    # PostgreSQL requires UPDATE privilege for FOR UPDATE. The immutable id
    # and mandatory version guard reject an actual edit through this grant.
    columns.setdefault("stewardship_credential_deployment", {}).setdefault(
        "UPDATE", set()
    ).add("id")
    columns.setdefault("stewardship_family_token", {}).setdefault(
        "SELECT", set()
    ).update({"id", "generation_id", "destroyed_at"})
    if not worker:
        return
    tables["stewardship_family_token_generation"].add("INSERT")
    columns.setdefault("stewardship_family_token_generation", {}).setdefault(
        "UPDATE", set()
    ).update(
        {
            "id",
            "checkpoint",
            "version",
            "state",
            "coverage_digest",
            "coverage_count",
            "completed_at",
        }
    )
    # Source issuance and closing-boundary grants already include token INSERT
    # and write-only scrub columns. Never turn them into ciphertext/digest SELECT.
