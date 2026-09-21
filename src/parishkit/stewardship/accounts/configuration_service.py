"""Narrow online config-installer admission around the durable activation engine.

OPS-02 provisions grants and mounts; OPS-04 wires its process loop. Neither a
role string nor a queue payload substitutes for the kernel and SQL checks here.
The service cannot stage requests, access credential staging, or read Families.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.service_boundaries import admit_online_service

from .authority import AuthorityStore
from .configuration_installation import install_request
from .configuration_schema import validate_sections
from .credential_database import _identity, admit_grants
from .setup_exchange_grants import SESSION_COLUMNS

# Include trigger-owned effects, not just the ORM statements in the installer.
# No DELETE/TRUNCATE/DDL, secret-staging, Family, source-payload or response
# grants belong here. Session access is limited to original-login liveness
# columns, never session credentials. The current Chairperson view exposes only
# relationship evidence, not the underlying census/contact corpus.
CONFIGURATION_GRANTS = {
    "stewardship_setup_prepared": {"SELECT", "INSERT"},
    "stewardship_setup_readiness_binding": {"SELECT"},
    "stewardship_setup_credential_install": {"SELECT"},
    "stewardship_setup_attempt": {"SELECT"},
    "stewardship_setup_config_intent": {"SELECT"},
    "stewardship_setup_config_abort": {"SELECT", "INSERT"},
    "stewardship_secret_request": {"SELECT"},
    "stewardship_credential_consumer_ack": {"SELECT"},
    "stewardship_provider_context": {"SELECT"},
    "stewardship_branding_bundle": {"SELECT"},
    "stewardship_branding_asset": {"SELECT"},
    "django_migrations": {"SELECT"},
    "stewardship_configuration_version": {"SELECT", "INSERT"},
    "stewardship_parish": {"SELECT", "INSERT"},
    "stewardship_applied_integration": {"SELECT", "INSERT"},
    "stewardship_domain_rule": {"SELECT", "INSERT"},
    "stewardship_address_rule": {"SELECT", "INSERT"},
    "stewardship_address_grant": {"SELECT", "INSERT"},
    "stewardship_ministry_assignment": {"SELECT", "INSERT"},
    "stewardship_ministry_activity": {"SELECT", "INSERT"},
    "stewardship_content_version": {"SELECT", "INSERT"},
    "stewardship_config_request": {"SELECT", "UPDATE"},
    "stewardship_config_checkpoint": {"SELECT", "INSERT"},
    "stewardship_config_activation": {"SELECT", "INSERT"},
    "stewardship_system_configuration": {"SELECT", "UPDATE"},
    "stewardship_policy_security_event": {"SELECT", "INSERT"},
    "stewardship_policy_epoch": {"SELECT", "INSERT"},
    "stewardship_admin_revocation": {"SELECT", "INSERT"},
    "stewardship_portal_user": {"SELECT"},
    "stewardship_assignment_overlay": {"SELECT", "INSERT", "UPDATE"},
    "stewardship_current_chair": {"SELECT"},
    "stewardship_source_current": {"SELECT"},
    "stewardship_chair_seed_evidence": {"SELECT"},
    "stewardship_chair_reconciliation": {"SELECT", "INSERT"},
    "stewardship_chair_review": {"SELECT", "INSERT", "UPDATE"},
    "stewardship_campaign_configuration": {"SELECT", "INSERT"},
    "stewardship_campaign": {"SELECT", "INSERT", "UPDATE"},
    "stewardship_campaign_work_gate": {"SELECT"},
    "stewardship_campaign_config_intent": {"SELECT"},
    "stewardship_campaign_config_abort": {"SELECT"},
    "stewardship_campaign_control": {"SELECT"},
    "stewardship_campaign_transition": {"SELECT"},
    # End edits atomically retire old work and allocate a fresh close revision.
    # Their trigger also writes a closed, metadata-only boundary audit context.
    # Full reopen activation remains with the later ADM-06 owner.
    "stewardship_campaign_boundary": {"SELECT", "INSERT", "UPDATE"},
    "stewardship_task_run": {"SELECT"},
    "stewardship_schedule_revision": {"SELECT", "INSERT"},
    "stewardship_schedule_definition": {"SELECT", "INSERT", "UPDATE"},
    "stewardship_schedule_selection": {"SELECT", "INSERT"},
    "stewardship_schedule_occurrence": {"SELECT"},
    "stewardship_schedule_fulfillment": {"SELECT"},
    "stewardship_schedule_work_summary": {"SELECT"},
    "stewardship_occurrence_transition": {"SELECT", "INSERT"},
    "stewardship_audit_event": {"INSERT"},
    "stewardship_audit_context": {"INSERT"},
}

CONFIGURATION_COLUMNS = {
    "stewardship_portal_session": {"SELECT": SESSION_COLUMNS},
    # The activation-time actor recheck share-locks the confirming user's row,
    # which PostgreSQL allows only with one UPDATE privilege; the identity
    # trigger rejects an id-only update, so the row itself stays read-only.
    "stewardship_portal_user": {"UPDATE": {"id"}},
}


def admit_configuration_database():
    """Reject impersonation, inherited/owner authority and every excess grant."""
    _identity("pk_stewardship_config_installer")
    from django.db import connection

    from parishkit.stewardship.runtime_grants import admit_columns

    allowed = {table: set(names) for table, names in CONFIGURATION_GRANTS.items()}
    for table, privileges in CONFIGURATION_COLUMNS.items():
        allowed.setdefault(table, set()).update(privileges)
    admit_grants(allowed)
    admit_columns(connection, CONFIGURATION_GRANTS, CONFIGURATION_COLUMNS)


@dataclass(frozen=True)
class ConfigurationInstaller:
    """Validated service configuration is rechecked before each resumable request."""

    configuration: object
    store: AuthorityStore

    @classmethod
    def from_configuration(cls, configuration):
        """Use the real mount table before opening any writable authority store."""
        if admit_online_service(configuration) is not ServiceRole.CONFIG_INSTALLER:
            raise ConfigError(
                "Configuration installation requires its isolated service."
            )
        admit_configuration_database()
        return cls(
            configuration,
            AuthorityStore(configuration.paths["authority"], validate_sections),
        )

    def run_request(self, request_id, *, admit_campaign=None):
        """An opaque queue reference never nominates paths, SQL roles or file content.

        The merged engine serializes installer work across actual file selection
        and durable commits, detects lost SQL sessions and reconciles only the
        exact prepared digest. Exceptional campaigns still require their owning
        admission callback. No callback silently approves missing future owners.

        Unlike target credential queues (file locks survive reconnects), this
        engine pins the exact PostgreSQL session across every file/SQL boundary
        and aborts on reconnect. Grants are admitted for each resumable invocation;
        SQL still enforces revocation on every statement. Operational grant changes
        must quiesce the service and restart admission, not alter live authority.
        """
        if not isinstance(request_id, UUID):
            raise ConfigError("A configuration request reference is required.")
        if admit_online_service(self.configuration) is not ServiceRole.CONFIG_INSTALLER:
            raise ConfigError(
                "Configuration installation requires its isolated service."
            )
        if self.store.root != self.configuration.paths["authority"]:
            raise ConfigError("Configuration installer authority path changed.")
        admit_configuration_database()
        return install_request(
            self.store,
            request_id=request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_campaign,
        )
