-- Frozen forward migration file 0187 (the repository-wide file sequence,
-- number assigned centrally): the operational log's closed event list
-- admits consumer_readmission (#2228), the INFO line a worker or
-- mail-dispatch process started without a setup credential logs once
-- setup completes, before it drains and exits to be admitted again
-- (operations spec, Re-admission after the configured marker). The line
-- goes to the process log; the database check admits the name too so
-- Python's event set and the database's closed set cannot drift (every
-- Event must be admitted). This file is installed by its Django migration
-- in the audit app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A
-- later change gets its own numbered file. A fresh install runs the
-- baseline, the earlier files and then this; the baseline already carries
-- the change, so the install ends in the same catalog as an upgraded
-- database.
--
-- One object changes: operational_event_safe, 0106's list plus
-- consumer_readmission. No function, table, index or grant changes, so no
-- SECURITY DEFINER attribute or runtime grant is involved, and the safe
-- context and log writer keep their bodies: no direct entry may record
-- this event. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The full event list: 0106's plus the new name. ADD CONSTRAINT validates
-- the existing rows, which the old list already admitted.
ALTER TABLE public.stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE public.stewardship_operational_log ADD CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy',
        'go_live_refresh_held','go_live_step_refused','go_live_sequencing_stopped',
        'admin_command_failed','debug_logging_switched_off',
        'retry_refusal_unrecorded','admin_input_refused','consumer_readmission'));

-- Refuse to commit unless the constraint is installed, validated and
-- admits every name, old and new, so an upgrade cannot report success
-- with one lost. No temporary objects: the migration login has no TEMP
-- grant.
DO $check$
DECLARE definition text; missing text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_operational_log'::regclass
       AND conname='operational_event_safe' AND contype='c' AND convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'Migration 0187: operational_event_safe is not installed and validated';
    END IF;
    SELECT string_agg(name, ', ') INTO missing
      FROM unnest(ARRAY[
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy',
        'go_live_refresh_held','go_live_step_refused','go_live_sequencing_stopped',
        'admin_command_failed','debug_logging_switched_off',
        'retry_refusal_unrecorded','admin_input_refused','consumer_readmission'
        ]) AS name
     WHERE position(quote_literal(name) IN definition)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0187: operational_event_safe does not admit: %', missing;
    END IF;
END
$check$;
