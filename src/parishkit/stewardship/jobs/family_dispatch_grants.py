"""Compiled Family mail permissions never grant general-code or web decryption."""

METADATA_FIELDS = (
    "id",
    "task_id",
    "semantic_key",
    "campaign_id",
    "family_id",
    "mode",
    "routing",
    "purpose",
    "credential_namespace",
    "rehearsal_epoch_id",
    "state",
    "version",
    "attempt",
    "render_id",
    "run_id",
    "task_fence",
    "worker_id",
    "provider_deadline",
    "not_before",
    "pause_hold_id",
    "pause_version",
    "token_generation_id",
    "credential_epoch_id",
)


def add_dispatch_grants(tables, columns):
    """The isolated mail role resolves tokens and journals only fenced deliveries."""
    from parishkit.stewardship.reports.digest_grants import add_digest_dispatch_grants
    from parishkit.stewardship.reports.weekly_grants import add_weekly_dispatch_grants

    add_digest_dispatch_grants(tables, columns, private=True)
    add_weekly_dispatch_grants(tables, columns, private=True)
    add_receipt_reads(columns)
    for table in (
        "stewardship_ops_incident",
        "stewardship_ops_notice",
        "stewardship_ops_cohort",
        "stewardship_ops_recipient",
        "stewardship_policy_security_event",
        "stewardship_security_cohort",
        "stewardship_security_recipient",
        "stewardship_campaign",
        "stewardship_campaign_configuration",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_credentials",
        "stewardship_rehearsal_epoch",
        "stewardship_credential_deployment",
        "stewardship_credential_key_state",
        "stewardship_family_token_generation",
        "stewardship_source_current",
        "stewardship_source_snapshot",
        "stewardship_schedule_definition",
        "stewardship_schedule_revision",
        "stewardship_activation_catchup",
        "stewardship_restore_delivery_hold",
        "stewardship_recipient_resolution",
        "stewardship_family_mail_preparation",
        "stewardship_family_mail_test",
        "stewardship_family_eligibility",
    ):
        tables.setdefault(table, set()).add("SELECT")
    for entity in ("family", "member", "contact"):
        for prefix in ("stewardship_source_", "stewardship_snapshot_"):
            tables.setdefault(prefix + entity, set()).add("SELECT")
    for table in ("stewardship_outbox_message", "stewardship_schedule_occurrence"):
        tables.setdefault(table, set()).update({"SELECT", "UPDATE"})
    for table in (
        "stewardship_outbox_render",
        "stewardship_outbox_event",
        "stewardship_schedule_fulfillment",
        "stewardship_occurrence_transition",
        "stewardship_delivery_pause_hold",
        "stewardship_recipient_refusal",
    ):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    tables["stewardship_schedule_occurrence"].add("INSERT")
    for table in (
        "stewardship_system_configuration",
        "stewardship_campaign",
        "stewardship_campaign_credentials",
        "stewardship_schedule_definition",
        "stewardship_source_snapshot",
    ):
        columns.setdefault(table, {}).setdefault("UPDATE", set()).add("id")
    for table, fields in {
        "stewardship_family_campaign": {
            "id",
            "campaign_id",
            "family_duid",
            "source_generation",
            "active",
            "portal_eligible",
            "email_eligible",
            "email_deliverable",
            "effective_submission_id",
        },
        "stewardship_family_token": {
            "id",
            "family_id",
            "campaign_id",
            "generation_id",
            "destroyed_at",
            "ciphertext",
            "digest",
        },
        "stewardship_rehearsal_credential": {
            "id",
            "family_id",
            "epoch_id",
            "token_ciphertext",
            "token_digest",
        },
        "stewardship_submission": {"family_id", "mode", "rehearsal_epoch_id"},
    }.items():
        columns.setdefault(table, {}).setdefault("SELECT", set()).update(fields)


def add_dispatch_scheduler_reads(tables, columns):
    """Schedule opaque work without access to recipients, bodies or sealed values."""
    from parishkit.stewardship.reports.digest_grants import add_digest_dispatch_grants
    from parishkit.stewardship.reports.weekly_grants import add_weekly_dispatch_grants

    add_digest_dispatch_grants(tables, columns, private=False)
    add_weekly_dispatch_grants(tables, columns, private=False)
    columns.setdefault("stewardship_outbox_message", {}).setdefault(
        "SELECT", set()
    ).update(METADATA_FIELDS)
    add_receipt_reads(columns)


def add_receipt_reads(columns):
    """Purpose identity reads never expose census, pledge or free-text answers."""
    columns.setdefault("stewardship_submission", {}).setdefault("SELECT", set()).update(
        {
            "id",
            "family_id",
            "campaign_id",
            "mode",
            "rehearsal_epoch_id",
            "submitted_at",
            "configuration_id",
        }
    )
    columns.setdefault("stewardship_submission_receipt", {}).setdefault(
        "SELECT", set()
    ).update({"id", "submission_id", "outbox_id", "disposition"})
