"""Explicit SQL authority for the read-only upstream refresh owner.

Refreshing writes local source and population state, not upstream data. Payloads
are append-only; compaction, credential rotation, campaign transitions and role
configuration remain separate owners. Keep this inventory closed when adding
future source models.
"""

SOURCE_READ = frozenset(
    {
        "stewardship_applied_integration",
        "stewardship_source_current",
        "stewardship_source_lease",
        "stewardship_source_snapshot",
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_source_refresh_attempt",
        "stewardship_source_refresh_fallback",
        "stewardship_ministry_assignment",
        "stewardship_ministry_activity",
        "stewardship_chair_seed_evidence",
        "stewardship_current_chair",
        "stewardship_chair_reconciliation",
        "stewardship_chair_review",
        "stewardship_assignment_overlay",
        "stewardship_credential_deployment",
        "stewardship_credential_key_state",
        "stewardship_family_campaign",
        "stewardship_family_code_mac",
        "stewardship_family_token_generation",
    }
)

SOURCE_APPEND = frozenset(
    {
        "stewardship_source_snapshot",
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_source_refresh_attempt",
        "stewardship_source_refresh_fallback",
        "stewardship_chair_reconciliation",
        "stewardship_chair_review",
        "stewardship_assignment_overlay",
        "stewardship_family_campaign",
        "stewardship_family_code_mac",
        "stewardship_family_eligibility",
        "stewardship_family_token",
        "stewardship_campaign_credentials",
    }
)

SCHEDULER_CANCEL_COLUMNS = frozenset(
    {
        "id",
        "state",
        "action",
        "actor_id",
        "correlation_id",
        "version",
        "lease_expires_at",
    }
)


def add_configuration_reads(tables):
    """Read complete public projections for startup parity, without config writes.

    Parish and login-rule fields are part of the selected canonical document
    these roles already read. Do not describe them as column-restricted; private
    identity/session, source payload and credential grants are separate below.
    """
    for table in (
        "stewardship_parish",
        "stewardship_applied_integration",
        "stewardship_domain_rule",
        "stewardship_address_rule",
        "stewardship_address_grant",
        "stewardship_ministry_assignment",
        "stewardship_ministry_activity",
        "stewardship_schedule_revision",
        "stewardship_content_version",
    ):
        tables.setdefault(table, set()).add("SELECT")


def add_refresh_scheduler_grants(tables, columns):
    """Scheduling reads scope metadata but never source payloads or Family codes."""
    add_configuration_reads(tables)
    for table in (
        "stewardship_applied_integration",
        "stewardship_source_current",
        "stewardship_source_lease",
        "stewardship_source_snapshot",
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_source_refresh_attempt",
        "stewardship_source_refresh_fallback",
        "stewardship_source_refresh_tick",
    ):
        tables[table] = {"SELECT"}
    for table in (
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_source_refresh_tick",
    ):
        tables[table].add("INSERT")
    # A separate SQL trigger limits these columns to waiting-source cancellation;
    # knowing a worker UUID cannot let this login impersonate its live claim.
    columns["stewardship_task_run"] = {"UPDATE": set(SCHEDULER_CANCEL_COLUMNS)}
    columns["stewardship_source_lease"] = {"UPDATE": {"id"}}


def add_refresh_worker_grants(tables, columns):
    """Extend fresh task maps with only implemented source/population effects."""
    add_configuration_reads(tables)
    for table in SOURCE_READ:
        tables.setdefault(table, set()).add("SELECT")
    for table in SOURCE_APPEND:
        tables.setdefault(table, set()).add("INSERT")
    # An explicit entity vocabulary, not model discovery or a prefix wildcard.
    for entity in (
        "family",
        "member",
        "contact",
        "address",
        "ministry",
        "roster",
        "fund",
        "pledge",
        "contribution",
    ):
        for prefix in ("stewardship_source_", "stewardship_snapshot_"):
            # DELETE is separately SQL-guarded to expired setup's exact source
            # Task/fence. Ordinary refresh/retention cannot use this authority.
            tables[prefix + entity] = {"SELECT", "INSERT", "DELETE"}
    for table in (
        "stewardship_source_current",
        "stewardship_source_lease",
        "stewardship_source_snapshot",
        "stewardship_chair_review",
        "stewardship_assignment_overlay",
    ):
        tables[table].add("UPDATE")
    columns["stewardship_credential_deployment"] = {"UPDATE": {"id"}}
    # Existing sealed links are only tested for membership. The general worker
    # has the public sealing key, not private decryption or token-read authority.
    columns["stewardship_family_token"] = {
        "SELECT": {"id", "created_at", "family_id", "generation_id"}
    }
    columns["stewardship_family_eligibility"] = {"SELECT": {"created_at"}}
    columns["stewardship_family_campaign"] = {
        "UPDATE": {
            "code_ciphertext",
            "version",
            "actor_id",
            "correlation_id",
            "active",
            "portal_eligible",
            "email_eligible",
            "email_deliverable",
            "status_reason",
            "deliverability_reason",
            "eligibility_changed_at",
            "first_eligible_at",
            "first_eligible_source_generation",
            "source_generation",
        }
    }
    columns["stewardship_campaign_credentials"]["UPDATE"].update(
        {
            "source_snapshot_id",
            "source_generation",
            "eligibility_digest",
            "eligible_count",
            "population_dirty",
            "version",
        }
    )
