-- Frozen forward migration file 0034 (the repository-wide file sequence):
-- system setup may finish without a campaign (#142). This file is installed
-- by its Django migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copy of the replaced function still equals the fresh-install
-- baseline's (schema/functions.sql).
--
-- The setup completion guard accepted only a completion that had also
-- created the first campaign (with the setup attempt's id) and its coded
-- Family population. It now also accepts a system-only completion: no
-- current campaign, no campaign row at all, and a final full snapshot read
-- with the empty giving window. The one-campaign branch is unchanged, so an
-- attempt confirmed before this release still finishes as it was confirmed.
-- The function is not SECURITY DEFINER in the baseline, so nothing is
-- re-altered. No existing row changes. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_setup_completion_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.preparation_id IS DISTINCT FROM public.stewardship_setup_completion_context_v1()
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_prepared prepared
            JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
            JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
            JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            JOIN public.stewardship_config_activation activation
                ON activation.id=NEW.activation_id AND activation.request_id=intent.request_id
                AND activation.configuration_id=prepared.configuration_id
            JOIN public.stewardship_system_configuration runtime
                ON runtime.active_configuration_id=prepared.configuration_id
                AND runtime.mode='testing'
                AND runtime.testing_recipient=ready.testing_recipient
            JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=NEW.snapshot_id
                AND snapshot.task_id=NEW.task_id AND snapshot.source_fence=NEW.source_fence
                AND snapshot.state='promoted' AND snapshot.kind='full'
            JOIN public.stewardship_source_current current ON current.snapshot_id=snapshot.id
            JOIN public.stewardship_source_lease lease ON lease.owner_id=NEW.task_id
                AND lease.fence=NEW.source_fence AND lease.task_fence=NEW.task_fence
            WHERE prepared.id=NEW.preparation_id AND NEW.actor_id=attempt.owner_id
                AND (
                    -- System-only setup (#142): no campaign, so no giving
                    -- window, no Families and no Family codes yet.
                    (runtime.current_campaign_id IS NULL
                        AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign)
                        AND snapshot.cursor->>'window_digest'=encode(sha256(convert_to(
                            public.stewardship_source_current_window_v1(NULL),'UTF8')),'hex'))
                    OR
                    -- Setup that also created its first campaign (an attempt
                    -- confirmed before #142): that campaign, its window and its
                    -- complete, coded population.
                    (runtime.current_campaign_id=attempt.id
                        AND EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials population
                            WHERE population.campaign_id=attempt.id
                                AND population.source_snapshot_id=snapshot.id
                                AND population.source_generation=snapshot.generation
                                AND NOT population.population_dirty
                                AND population.eligible_count=(SELECT count(*)
                                    FROM public.stewardship_family_campaign
                                    WHERE campaign_id=attempt.id AND portal_eligible))
                        AND snapshot.cursor->>'window_digest'=encode(sha256(convert_to(
                            public.stewardship_source_current_window_v1(attempt.id),'UTF8')),'hex')
                        AND (SELECT count(*) FROM public.stewardship_family_campaign WHERE campaign_id=attempt.id)
                            = (SELECT count(*) FROM public.stewardship_snapshot_family WHERE snapshot_id=snapshot.id)
                        AND NOT EXISTS (
                            SELECT 1 FROM public.stewardship_snapshot_family member
                            JOIN public.stewardship_source_family payload ON payload.id=member.payload_id
                            LEFT JOIN public.stewardship_family_campaign family ON family.campaign_id=attempt.id
                                AND family.family_duid=member.source_key::bigint
                            WHERE member.snapshot_id=snapshot.id AND (
                                family.id IS NULL OR family.source_generation IS DISTINCT FROM snapshot.generation
                                OR family.active IS DISTINCT FROM (payload.canonical::jsonb->>'active')::boolean
                                OR family.portal_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'portal_eligible')::boolean
                                OR family.email_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'email_eligible')::boolean
                            )
                        )
                        AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign family
                            WHERE family.campaign_id=attempt.id AND family.portal_eligible
                                AND (coalesce(family.code_ciphertext,'')='' OR NOT EXISTS (
                                    SELECT 1 FROM public.stewardship_family_code_mac mac WHERE mac.family_id=family.id))))
                )
        ) THEN
        RAISE EXCEPTION 'Setup completion requires exact activation, source and population'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- Refuse to commit unless the replaced guard is the one installed: it must
-- admit the system-only shape and keep the one-campaign population check.
DO $check$
DECLARE body text;
BEGIN
    SELECT prosrc INTO body FROM pg_proc
        WHERE oid='public.stewardship_setup_completion_insert_v1()'::regprocedure;
    IF body IS NULL
        OR position('System-only setup (#142)' IN body)=0
        OR position('stewardship_source_current_window_v1(NULL)' IN body)=0
        OR position('stewardship_campaign_credentials population' IN body)=0 THEN
        RAISE EXCEPTION 'The campaign-free setup completion guard was not installed';
    END IF;
END
$check$;
