"""Which schema functions may run without a fixed ``search_path`` (#389 L9).

A function with no ``SET search_path`` resolves unqualified names through
its caller's path. That is safe only for SECURITY INVOKER functions whose
callers cannot plant objects on that path, and admission refuses every
login CREATE on any schema and TEMP (``credential_database.admit_grants``,
``runtime_database.require_no_temporary_authority``). The functions below
predate the rule that every function sets one; they are all invokers
(mostly row guards) and were left as they are rather than altered in a
migration. Every new function, and every definer, must set ``search_path``:
this list only ever shrinks.
"""

import re
from pathlib import Path

SCHEMA = Path(__file__).parents[2] / "src/parishkit/stewardship/schema"
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION\s+(?:public\.)?(\w+)\((.*?)\$(\w*)\$",
    re.S,
)
# The reviewed invoker functions without a fixed search_path.
UNPINNED = frozenset(
    {
        "stewardship_activation_catchup_mutable_v1",
        "stewardship_address_grant_immutable_v1",
        "stewardship_address_rule_immutable_v1",
        "stewardship_admin_revocation_immutable_v1",
        "stewardship_applied_integration_immutable_v1",
        "stewardship_assignment_overlay_mutable_v1",
        "stewardship_audit_context_immutable_v1",
        "stewardship_audit_event_immutable_v1",
        "stewardship_auth_incident_mutable_v1",
        "stewardship_automation_notice_ack_immutable_v1",
        "stewardship_automation_notice_immutable_v1",
        "stewardship_backup_run_immutable_v1",
        "stewardship_branding_asset_immutable_v1",
        "stewardship_branding_asset_insert_v1",
        "stewardship_branding_bundle_mutable_v1",
        "stewardship_campaign_artwork_v1",
        "stewardship_campaign_boundary_mutable_v1",
        "stewardship_campaign_config_abort_immutable_v1",
        "stewardship_campaign_config_intent_immutable_v1",
        "stewardship_campaign_configuration_immutable_v1",
        "stewardship_campaign_control_immutable_v1",
        "stewardship_campaign_credentials_mutable_v1",
        "stewardship_campaign_mail_test_mutable_v1",
        "stewardship_campaign_mutable_v1",
        "stewardship_campaign_transition_immutable_v1",
        "stewardship_campaign_work_gate_mutable_v1",
        "stewardship_catchup_checkpoint_immutable_v1",
        "stewardship_catchup_failure_immutable_v1",
        "stewardship_chair_reconciliation_immutable_v1",
        "stewardship_chair_review_mutable_v1",
        "stewardship_chair_seed_evidence_immutable_v1",
        "stewardship_chair_seed_intent_immutable_v1",
        "stewardship_cleanup_inventory_v1",
        "stewardship_config_activation_immutable_v1",
        "stewardship_config_checkpoint_immutable_v1",
        "stewardship_config_request_immutable_v1",
        "stewardship_configuration_version_immutable_v1",
        "stewardship_content_version_immutable_v1",
        "stewardship_credential_consumer_ack_immutable_v1",
        "stewardship_credential_deployment_mutable_v1",
        "stewardship_credential_key_state_mutable_v1",
        "stewardship_critical_event_ack_immutable_v1",
        "stewardship_daily_fact_set_mutable_v1",
        "stewardship_domain_rule_immutable_v1",
        "stewardship_due_work_critical_v1",
        "stewardship_due_work_since_v1",
        "stewardship_fact_compaction_guard",
        "stewardship_fact_compaction_immutable_v1",
        "stewardship_fact_compaction_pair_guard",
        "stewardship_fact_day_guard",
        "stewardship_fact_delete_pair_guard",
        "stewardship_fact_demand_guard",
        "stewardship_fact_demand_mutable_v1",
        "stewardship_fact_disposable",
        "stewardship_fact_pin_mutable_v1",
        "stewardship_fact_pointer_mutable_v1",
        "stewardship_fact_reference_guard",
        "stewardship_fact_set_guard",
        "stewardship_fact_source_pin_guard",
        "stewardship_family_campaign_mutable_v1",
        "stewardship_family_code_mac_immutable_v1",
        "stewardship_family_eligibility_immutable_v1",
        "stewardship_family_session_mutable_v1",
        "stewardship_family_token_generation_mutable_v1",
        "stewardship_family_token_mutable_v1",
        "stewardship_hosted_file_mutable_v1",
        "stewardship_limiter_health_mutable_v1",
        "stewardship_ministry_activity_immutable_v1",
        "stewardship_ministry_assignment_immutable_v1",
        "stewardship_occurrence_transition_immutable_v1",
        "stewardship_operational_log_immutable_v1",
        "stewardship_operational_log_writer_v1",
        "stewardship_ops_cohort_immutable_v1",
        "stewardship_ops_incident_mutable_v1",
        "stewardship_ops_log_receipt_immutable_v1",
        "stewardship_ops_notice_immutable_v1",
        "stewardship_ops_recipient_immutable_v1",
        "stewardship_parish_branding_v1",
        "stewardship_parish_immutable_v1",
        "stewardship_policy_epoch_immutable_v1",
        "stewardship_policy_security_ack_immutable_v1",
        "stewardship_policy_security_event_immutable_v1",
        "stewardship_portal_session_mutable_v1",
        "stewardship_portal_user_mutable_v1",
        "stewardship_postclose_resolution_immutable_v1",
        "stewardship_production_cancellation_immutable_v1",
        "stewardship_provider_context_immutable_v1",
        "stewardship_provider_context_insert_v1",
        "stewardship_public_credential_handoff_immutable_v1",
        "stewardship_public_handoff_insert_v1",
        "stewardship_refresh_attempt_guard_v1",
        "stewardship_refresh_command_guard_v1",
        "stewardship_refresh_fallback_guard_v1",
        "stewardship_refresh_snapshot_completion_v1",
        "stewardship_rehearsal_credential_mutable_v1",
        "stewardship_rehearsal_epoch_mutable_v1",
        "stewardship_rehearsal_reservation_immutable_v1",
        "stewardship_restore_delivery_hold_mutable_v1",
        "stewardship_restore_hold_resolution_immutable_v1",
        "stewardship_runtime_transition_immutable_v1",
        "stewardship_schedule_definition_mutable_v1",
        "stewardship_schedule_fulfillment_immutable_v1",
        "stewardship_schedule_occurrence_mutable_v1",
        "stewardship_schedule_revision_immutable_v1",
        "stewardship_schedule_selection_immutable_v1",
        "stewardship_secret_checkpoint_immutable_v1",
        "stewardship_secret_request_mutable_v1",
        "stewardship_security_cohort_immutable_v1",
        "stewardship_security_recipient_immutable_v1",
        "stewardship_setup_attempt_mutable_v1",
        "stewardship_setup_completion_immutable_v1",
        "stewardship_setup_config_abort_immutable_v1",
        "stewardship_setup_config_intent_immutable_v1",
        "stewardship_setup_credential_install_immutable_v1",
        "stewardship_setup_draft_section_mutable_v1",
        "stewardship_setup_mail_delivery_mutable_v1",
        "stewardship_setup_mail_exchange_mutable_v1",
        "stewardship_setup_prepared_immutable_v1",
        "stewardship_setup_readiness_binding_immutable_v1",
        "stewardship_setup_sealed_credential_mutable_v1",
        "stewardship_setup_slack_delivery_mutable_v1",
        "stewardship_setup_source_exchange_mutable_v1",
        "stewardship_setup_source_result_immutable_v1",
        "stewardship_source_compaction_immutable_v1",
        "stewardship_source_corpus_evidence",
        "stewardship_source_current_guard",
        "stewardship_source_current_mutable_v1",
        "stewardship_source_lease_mutable_v1",
        "stewardship_source_lease_owner_guard",
        "stewardship_source_pin_guard",
        "stewardship_source_pin_mutable_v1",
        "stewardship_source_promotion_pair_guard",
        "stewardship_source_refresh_attempt_immutable_v1",
        "stewardship_source_refresh_command_immutable_v1",
        "stewardship_source_refresh_fallback_immutable_v1",
        "stewardship_source_refresh_request_immutable_v1",
        "stewardship_source_refresh_tick_immutable_v1",
        "stewardship_source_snapshot_guard",
        "stewardship_source_snapshot_mutable_v1",
        "stewardship_system_configuration_mutable_v1",
        "stewardship_task_event_immutable_v1",
        "stewardship_task_run_mutable_v1",
    }
)


def _functions():
    """Each function's name and header (up to its body) in every schema file."""
    for path in sorted([*SCHEMA.glob("*.sql"), *SCHEMA.glob("migrations/*.sql")]):
        for name, header, _ in FUNCTION.findall(path.read_text(encoding="utf-8")):
            yield path.name, name, header


def test_no_new_function_lacks_a_fixed_search_path():
    """A function without ``SET search_path`` must be on the reviewed list."""
    missing = {name for _, name, header in _functions() if "search_path" not in header}
    # A function that gained one leaves the list, so it cannot regress.
    assert missing == UNPINNED, {
        "new without search_path": sorted(missing - UNPINNED),
        "now set, drop from the list": sorted(UNPINNED - missing),
    }


def test_every_definer_sets_its_search_path():
    """A SECURITY DEFINER body runs as its owner, so it never trusts the path."""
    assert not [
        (file, name)
        for file, name, header in _functions()
        if "SECURITY DEFINER" in header and "search_path" not in header
    ]
