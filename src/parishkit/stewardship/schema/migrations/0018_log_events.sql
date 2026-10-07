-- Frozen forward migration file 0018 (the repository-wide file sequence;
-- Django's stewardship_jobs.0012): four reviewed operational events for
-- lines that had none of their own (#541, #546, #584). This file is
-- installed by jobs/migrations/0012_log_events.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
-- A later change gets its own numbered file. A fresh install runs the
-- baseline, 0002 to 0017 and then this; the baseline tables.sql already
-- carries the widened constraint, so the install ends in the same catalog as
-- an upgraded database.
--
-- Operational event names are a closed vocabulary: observability.Event in
-- Python and this check constraint in SQL, which a database test keeps equal.
-- The new names:
--   startup_waiting               an online service began waiting at startup
--                                 for its database (INFO, once per process)
--   startup_wait_ended            that wait ended with the database answering
--                                 (INFO, with the seconds waited)
--   debug_logging_enabled         the process keeps free-text messages and
--                                 tracebacks (WARNING, at every start)
--   refresh_lead_window_conflict  a scheduled full ParishSoft refresh falls in
--                                 a Production reminder's preparation window
--                                 (WARNING; it rode on startup_validated)
-- All four are written to the process log today; admitting them here keeps
-- every Python event writable to stewardship_operational_log. No table,
-- column or function changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The full list: 0008's (unchanged by 0009 to 0017) plus the four new
-- names. ADD CONSTRAINT validates the existing rows, which the old list
-- already admitted.
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
        'refresh_lead_window_conflict'));

-- Refuse to commit unless the constraint is installed, validated and lists
-- every name above, old and new, so an upgrade cannot report success with a
-- name lost. No temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE definition text; missing text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_operational_log'::regclass
       AND conname='operational_event_safe' AND contype='c' AND convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'operational_event_safe is not installed and validated';
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
        'refresh_lead_window_conflict']) AS name
     WHERE position(quote_literal(name) IN definition)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'operational_event_safe does not admit: %', missing;
    END IF;
END
$check$;
