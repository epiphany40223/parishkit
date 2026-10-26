-- Frozen forward migration file 0186 (the repository-wide file sequence):
-- the setup wizard's optional Off-site backups step (#2200, admin-portal
-- spec "Off-site backups during setup"). This file is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks the replaced baseline function's copy here against the
-- baseline.
--
-- The step saves one new setup draft section, backup, holding only public
-- values: whether copies are on, the canonical Google Drive folder link and
-- whether the Administrator confirmed that the backup private key is kept
-- off the server. No key, token or credential is collected. So:
--   setup_public_step                  the draft section's step CHECK
--     admits backup (dropped and added again; tables.sql holds the same
--     final definition)
--   stewardship_setup_draft_guard_v1   replaced with its baseline body and
--     only this change: backup's declared fields (SECURITY INVOKER, as
--     before), its containment operator now qualified, as every file after
--     0178 writes it
--
-- At finalization the applied configuration holds the Off-site backups
-- integration with that folder when the step was on. The Google Workspace
-- installer answers a "Test access" check with the installed key, as the
-- applied delegated mailbox user, so no check can run while setup has only
-- a staged key. Instead the worker, in the transaction that writes the
-- configured marker (stewardship_setup_completion), calls the new
-- argument-free definer stewardship_setup_backup_check_v1(), which queues
-- one ordinary check of the applied folder as the setup's Administrator and
-- the applied delegated mailbox user, both read from the marker and the
-- applied configuration, so no caller chooses the folder or whom the
-- installer impersonates (the probe guard compares the user again). It
-- refuses unless the configured marker was written in this same
-- transaction, queues nothing when this transaction already queued one, and
-- queues nothing when no folder is applied; it answers whether it queued a
-- check. It is SECURITY DEFINER with a fixed search_path; only the general
-- worker login is granted EXECUTE (runtime_grants.runtime_functions), and
-- the body refuses any other session login that is not a member of the
-- schema owner.
--
-- The DO block at the end refuses to commit unless all of it is installed
-- as declared. No temporary objects: the migration login has no TEMP
-- privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_setup_draft_section
    DROP CONSTRAINT setup_public_step;
ALTER TABLE public.stewardship_setup_draft_section
    ADD CONSTRAINT setup_public_step CHECK (((step)::text = ANY ((ARRAY['parish'::character varying, 'branding'::character varying, 'access'::character varying, 'mail'::character varying, 'slack'::character varying, 'testing'::character varying, 'backup'::character varying, 'campaign'::character varying, 'schedules'::character varying, 'page_access_denied'::character varying, 'page_additional'::character varying, 'page_census'::character varying, 'page_closing'::character varying, 'page_financial'::character varying, 'page_login_help'::character varying, 'page_member_census'::character varying, 'page_ministry'::character varying, 'page_post_end'::character varying, 'page_pre_start'::character varying, 'page_review'::character varying, 'page_submission_confirmation'::character varying, 'page_thank_you'::character varying, 'page_welcome'::character varying, 'email_confirmation'::character varying, 'email_critical_alert'::character varying, 'email_daily_digest'::character varying, 'email_initial'::character varying, 'email_reminder'::character varying, 'email_weekly_digest'::character varying])::text[])));

-- Replaces the baseline's body; only the backup fields are new.
CREATE OR REPLACE FUNCTION public.stewardship_setup_draft_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt stewardship_setup_attempt%ROWTYPE; stamp timestamptz:=clock_timestamp();
        expected text[]; actual text[];
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup draft tombstones are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup drafts require ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM stewardship_setup_attempt WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup cannot be repopulated' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed') OR NEW.values<>'{}'::jsonb
           OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Setup scrubbing needs a terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration runtime
        JOIN stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
        JOIN stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles OPERATOR(pg_catalog.@>) '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup draft requires its original live Admin'
            USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.values)<>'object' OR octet_length(NEW.values::text)>(CASE WHEN NEW.step IN ('campaign','schedules') OR left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN 1048576 ELSE 65536 END) THEN
        RAISE EXCEPTION 'Invalid public setup shape' USING ERRCODE='23514';
    END IF;
    expected=CASE NEW.step
        WHEN 'parish' THEN ARRAY['name','online_giving_url','phone','timezone','website']
        WHEN 'branding' THEN ARRAY['bundle_id']
        -- Addresses only (#993): no hosted-domain lists.
        WHEN 'access' THEN ARRAY['admin_addresses','ministry_addresses','staff_addresses']
        WHEN 'mail' THEN ARRAY['delegated_email','reply_to','sender','sender_name']
        WHEN 'slack' THEN ARRAY['channel_id','enabled']
        WHEN 'campaign' THEN ARRAY['campaign','source_result']
        WHEN 'testing' THEN ARRAY['testing_recipient']
        -- The optional Off-site backups step (#2200): public values only.
        WHEN 'backup' THEN ARRAY['acknowledged','enabled','folder_link']
        WHEN 'schedules' THEN ARRAY['records']
        ELSE ARRAY['id','values'] END;
    SELECT array_agg(key ORDER BY key) INTO actual
        FROM jsonb_object_keys(NEW.values) key;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Only declared public setup fields can be staged'
            USING ERRCODE='23514';
    END IF;

    IF NEW.step='campaign' AND (
        jsonb_typeof(NEW.values->'campaign') IS DISTINCT FROM 'object'
        OR jsonb_typeof(NEW.values->'source_result') IS DISTINCT FROM 'string'
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_source_result result
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE result.id::text=NEW.values->>'source_result'
        )
    ) THEN
        RAISE EXCEPTION 'Setup campaign requires its validated original source result'
            USING ERRCODE='23514';
    END IF;

    IF NEW.step='schedules' OR left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN
        IF NEW.step<>'schedules' AND NEW.values<>'{"id":null,"values":null}'::jsonb AND (
            NEW.values->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
            OR NEW.values->'values'->>'kind' IS DISTINCT FROM split_part(NEW.step,'_',1)
            OR NEW.values->'values'->>'slot' IS DISTINCT FROM
                substring(NEW.step from position('_' in NEW.step)+1)
        ) THEN
            RAISE EXCEPTION 'Setup content belongs to its original campaign and slot'
                USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_draft_section draft
            JOIN public.stewardship_setup_source_result result
                ON result.id::text=draft.values->>'source_result'
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE draft.attempt_id=attempt.id AND draft.step='campaign'
                AND draft.scrubbed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Setup content requires its validated staged campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;

    IF NEW.step='schedules' THEN
        IF jsonb_typeof(NEW.values->'records') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Setup schedules require a bounded record inventory'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(NEW.values->'records')>100 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.values->'records') record
            WHERE record->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
        ) THEN
            RAISE EXCEPTION 'Setup schedules belong to their original campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_setup_backup_check_v1() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE marker public.stewardship_setup_completion%ROWTYPE; target text;
        subject text; folder text;
BEGIN
    -- Granted to the general worker alone; refuse any other session login
    -- too, as stewardship_backup_destination_v1 does (members of the schema
    -- owner, such as the migration login, may still call it).
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the worker queues the setup folder check'
            USING ERRCODE='42501';
    END IF;
    -- Only the transaction writing the configured marker may queue the
    -- check: the marker row's xmin is this transaction's own id (the
    -- marker is never updated, so its xmin stays the inserting one). The
    -- worker calls this at the top level of that transaction; a call from
    -- inside a savepoint would see the savepoint's id and fail closed.
    SELECT completed.* INTO marker FROM public.stewardship_setup_completion completed
        WHERE completed.xmin=pg_current_xact_id()::xid;
    IF marker.id IS NULL OR marker.actor_id IS NULL THEN
        RAISE EXCEPTION 'The setup folder check is queued only with the configured marker'
            USING ERRCODE='55000';
    END IF;
    -- At most one per setup: the marker is unique, so this transaction is
    -- the only one that can queue it.
    IF EXISTS (SELECT 1 FROM public.stewardship_backup_drive_probe probe
            WHERE probe.xmin=pg_current_xact_id()::xid) THEN
        RETURN false;
    END IF;
    SELECT backup.settings->>'target', workspace.settings->>'delegated_email'
    INTO target, subject
    FROM public.stewardship_config_activation activation
    JOIN public.stewardship_system_configuration runtime
        ON runtime.active_configuration_id=activation.configuration_id
    LEFT JOIN public.stewardship_applied_integration backup
        ON backup.configuration_id=activation.configuration_id
        AND backup.kind='backup'
    LEFT JOIN public.stewardship_applied_integration workspace
        ON workspace.configuration_id=activation.configuration_id
        AND workspace.kind='google_workspace'
    WHERE activation.id=marker.activation_id;
    folder=substring(target from '^https://drive\.google\.com/drive/folders/([A-Za-z0-9_-]{10,200})$');
    IF folder IS NULL OR subject IS NULL THEN
        RETURN false;
    END IF;
    INSERT INTO public.stewardship_backup_drive_probe(id,requested_by_id,folder_id,subject)
    VALUES(gen_random_uuid(),marker.actor_id,folder,subject);
    RETURN true;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_setup_backup_check_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_backup_check_v1() FROM PUBLIC;

-- Refuse to commit unless everything is installed as declared. It reads
-- only the catalog. The missing function is checked first, on its own,
-- because PostgreSQL does not short-circuit OR and the ::regprocedure casts
-- below would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_setup_backup_check_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0186: the setup folder check is not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_setup_backup_check_v1()'::regprocedure
             AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
             AND p.prorettype='boolean'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker''%'
             AND p.prosrc LIKE '%completed.xmin=pg_current_xact_id()::xid%'
             AND p.prosrc LIKE '%probe.xmin=pg_current_xact_id()::xid%'
             AND p.prosrc LIKE '%backup.kind=''backup''%'
             AND p.prosrc LIKE '%workspace.kind=''google_workspace''%')
       OR has_function_privilege('public',
           'public.stewardship_setup_backup_check_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0186: the setup folder check is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_setup_draft_guard_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%WHEN ''backup'' THEN ARRAY[''acknowledged'',''enabled'',''folder_link'']%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint c
           WHERE c.conrelid='public.stewardship_setup_draft_section'::regclass
             AND c.conname='setup_public_step'
             AND pg_get_constraintdef(c.oid) LIKE '%''backup''::character varying%') THEN
        RAISE EXCEPTION 'Migration 0186: the backup draft section is not admitted as declared';
    END IF;
END
$check$;
