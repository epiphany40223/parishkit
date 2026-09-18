"""Minimum task-runtime SQL authority; provider owners extend it explicitly.

The scheduler creates hints and can cancel superseded waiting source requests,
but cannot claim or impersonate running work. Workers mutate fenced task metadata
and separately guarded one-time setup completion effects, never prepared
configuration, private credential bodies or sessions. Id-only UPDATE grants
permit row locks; owning table guards reject an id-only mutation.
"""

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole

READ_TABLES = frozenset(
    {
        "django_migrations",
        "stewardship_configuration_version",
        "stewardship_system_configuration",
        "stewardship_campaign",
        "stewardship_campaign_configuration",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_credentials",
        "stewardship_rehearsal_epoch",
        "stewardship_activation_catchup",
        "stewardship_task_run",
        "stewardship_task_event",
    }
)


def task_runtime_grants(role):
    """Return new maps; neither a queue header nor caller mutation extends policy."""
    if role not in {ServiceRole.WORKER, ServiceRole.SCHEDULER}:
        raise ConfigError("This service has no general task SQL authority.")
    tables = {table: {"SELECT"} for table in READ_TABLES}
    tables["stewardship_task_run"].add("INSERT")
    tables["stewardship_task_event"].add("INSERT")
    for table in (
        "stewardship_branding_bundle",
        "stewardship_branding_asset",
        "stewardship_setup_attempt",
        "stewardship_config_request",
        "stewardship_config_checkpoint",
        "stewardship_config_activation",
        "stewardship_setup_config_intent",
        "stewardship_setup_config_abort",
        "stewardship_setup_readiness_binding",
        "stewardship_setup_prepared",
        "stewardship_setup_completion",
        "stewardship_secret_request",
        "stewardship_credential_consumer_ack",
    ):
        tables[table] = {"SELECT"}
    tables["stewardship_credential_consumer_ack"].add("INSERT")
    for table in (
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
    ):
        tables[table] = {"INSERT"}
    columns = {
        table: {"UPDATE": {"id"}}
        for table in (
            "stewardship_system_configuration",
            "stewardship_campaign",
            "stewardship_campaign_credentials",
        )
    }
    # The ACK trigger takes FOR SHARE on its target-scoped receipt. Id-only
    # UPDATE permits that lock; RLS and mutation guards reject actual edits.
    columns["stewardship_secret_request"] = {"UPDATE": {"id"}}
    # Django INSERT RETURNING needs the declared database-default columns, not
    # read authority over unrelated historical audit or diagnostic payloads.
    columns.update(
        {
            "stewardship_audit_event": {
                "SELECT": {"created_at", "ownership_scope", "parish_id"}
            },
            "stewardship_audit_context": {"SELECT": {"created_at"}},
            "stewardship_operational_log": {"SELECT": {"created_at"}},
        }
    )
    if role is ServiceRole.WORKER:
        tables["stewardship_branding_bundle"].add("UPDATE")
        tables["stewardship_task_run"].add("UPDATE")
        # Operational observations are bounded, non-personal signals. SQL owns
        # derived state and creates notices; workers cannot insert notice rows.
        tables["stewardship_ops_incident"] = {"SELECT", "INSERT"}
        tables["stewardship_ops_notice"] = {"SELECT"}
        tables["stewardship_ops_log_receipt"] = {"SELECT", "INSERT"}
        columns["stewardship_ops_incident"] = {
            "UPDATE": {
                "signal_level",
                "action",
                "version",
                "actor_id",
                "correlation_id",
            }
        }
        from parishkit.stewardship.source.grants import add_refresh_worker_grants

        add_refresh_worker_grants(tables, columns)
        from parishkit.stewardship.accounts.setup_exchange_grants import (
            add_worker_exchange_grants,
        )

        add_worker_exchange_grants(tables, columns)
        from parishkit.stewardship.accounts.setup_completion_grants import (
            add_setup_completion_grants,
        )

        add_setup_completion_grants(tables, columns)
    else:
        columns["stewardship_ops_log_receipt"] = {"SELECT": {"log_id", "run_id"}}
        from parishkit.stewardship.source.grants import add_refresh_scheduler_grants

        add_refresh_scheduler_grants(tables, columns)
        # Setup expiry needs original session deadlines and current Admin policy,
        # never Django session keys, OAuth material or identity-provider subjects.
        columns["stewardship_portal_session"] = {
            "SELECT": {
                "id",
                "principal_id",
                "revoked_at",
                "expires_at",
                "last_activity_at",
            }
        }
        columns["stewardship_portal_user"] = {"SELECT": {"id", "email", "disabled"}}
        columns["stewardship_setup_attempt"] = {
            "UPDATE": {
                "state",
                "expired_at",
                "expiry_reason",
                "actor_id",
                "correlation_id",
                "version",
            }
        }
        columns["stewardship_setup_draft_section"] = {
            "SELECT": {"attempt_id", "scrubbed_at", "version"},
            "UPDATE": {
                "values",
                "scrubbed_at",
                "actor_id",
                "correlation_id",
                "version",
            },
        }
        columns["stewardship_setup_sealed_credential"] = {
            "SELECT": {"attempt_id", "scrubbed_at", "version"},
            "UPDATE": {
                "settings",
                "ciphertext",
                "scrubbed_at",
                "actor_id",
                "correlation_id",
                "version",
            },
        }
        from parishkit.stewardship.accounts.setup_exchange_grants import (
            add_exchange_cleanup_grants,
        )

        add_exchange_cleanup_grants(columns)
        from parishkit.stewardship.accounts.setup_mail_grants import (
            add_setup_mail_cleanup_grants,
        )

        add_setup_mail_cleanup_grants(tables, columns)
        from parishkit.stewardship.accounts.campaign_mail_grants import (
            add_campaign_mail_grants,
        )

        add_campaign_mail_grants(tables, columns, scheduler=True)
        from parishkit.stewardship.campaigns.schedule_grants import (
            add_schedule_planning_grants,
        )

        add_schedule_planning_grants(tables, columns)
    from parishkit.stewardship.campaigns.boundary_grants import add_boundary_grants

    columns["stewardship_operational_log"]["SELECT"].update({"id", "level"})
    if role is ServiceRole.WORKER:
        columns["stewardship_operational_log"]["SELECT"].update(
            {"event", "correlation_id"}
        )

    add_boundary_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from parishkit.stewardship.campaigns.cleanup_grants import add_cleanup_grants

    add_cleanup_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from parishkit.stewardship.campaigns.catchup_grants import add_catchup_grants

    add_catchup_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from parishkit.stewardship.reports.export_grants import add_export_grants

    add_export_grants(tables, columns, role=role.value)
    from parishkit.stewardship.reports.fact_grants import add_fact_grants

    add_fact_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from parishkit.stewardship.reports.digest_grants import add_digest_grants

    add_digest_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from parishkit.stewardship.reports.weekly_grants import add_weekly_grants

    add_weekly_grants(tables, columns, worker=role is ServiceRole.WORKER)
    from .family_mail_grants import add_family_mail_grants

    add_family_mail_grants(tables, columns, worker=role is ServiceRole.WORKER)
    if role is ServiceRole.SCHEDULER:
        from .family_dispatch_grants import add_dispatch_scheduler_reads

        add_dispatch_scheduler_reads(tables, columns)
    return tables, columns
