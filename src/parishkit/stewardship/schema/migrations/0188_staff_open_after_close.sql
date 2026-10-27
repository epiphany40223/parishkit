-- Frozen forward migration file 0188 (the repository-wide file sequence,
-- assigned by the coordinator): Staff can always enter a Family's response
-- through Open form, also after the campaign closes (#2294; reports spec,
-- "Open form"; parishioner-portal spec, "After the campaign ends"). This
-- file is installed by its Django migration in the campaigns app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks each replaced baseline function's copy here
-- against the baseline. Reversing needs its own forward migration.
--
-- The Administrator decided (2026-10-27) that Staff and Administrators can
-- always use Open form to submit or resubmit a Family's response, before or
-- after the campaign closes, with no cutoff, while Families themselves stay
-- closed out after the end, exactly as before. So the close is bypassed
-- only by a Family session that Open form's hand-off created, never by a
-- Family's own code, link or session:
--   stewardship_family_session.assisted_by_id   new nullable column: the
--     portal user whose Open form hand-off started the session; NULL for
--     every Family's own sign-in. A soft reference, like
--     stewardship_submission.entered_by_id: no foreign key.
--   stewardship_family_assisted_login_v1        new SECURITY DEFINER, the
--     hand-off's sign-in, and the only writer of assisted_by_id. It takes
--     the Family's code MACs (never a link) and the Admin portal session
--     being redeemed, requires that session to be live and its user a
--     current Administrator or Staff member, and admits a Production
--     campaign that has started and is scheduled, active or closed. PUBLIC
--     has no EXECUTE; database-grants grants it to the web login only
--     (runtime_grants.runtime_functions), and its body refuses any other
--     login outside the schema owner's role.
--   stewardship_family_session_mutable_v1        replaced with its baseline
--     body plus assisted_by_id among the immutable bindings, so the web
--     login's UPDATE on Family sessions can never set or clear it.
--   stewardship_family_baseline_guard_v1         replaced: a live form for
--     an assisted session is also admitted after the end and in a closed
--     campaign; everything else is unchanged.
--   stewardship_submission_guard_v1              replaced the same way for
--     the final submit; its jsonb and containment operators are now
--     qualified, as every file after 0178 writes them.
--   stewardship_submission_receipt_command_v1    replaced: the receipt of a
--     response an assisted session submitted in a closed campaign is
--     admitted, so it gets the ordinary receipt (SECURITY DEFINER, as
--     before, stated again below).
--   stewardship_token_campaign_effects_v1        replaced: the close no
--     longer revokes a session Open form started, so Staff entering a
--     response at the close keep their work; every Family's own session is
--     still revoked at the close, and every session when a scheduled
--     campaign returns to draft (SECURITY DEFINER, as before).
-- stewardship_family_login_v1, the Family's own code and link sign-in, is
-- unchanged: after the end it still refuses, and every emailed code and
-- link keeps working exactly as before. No row, grant or credential
-- changes. No temporary objects.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Only if missing, so the file may run whole again (the users-swap tests
-- reinstall a closure migration's file after dropping its definer).
ALTER TABLE public.stewardship_family_session ADD COLUMN IF NOT EXISTS assisted_by_id uuid;

-- stewardship_family_session_mutable_v1: the baseline body plus assisted_by_id.
CREATE OR REPLACE FUNCTION public.stewardship_family_session_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."family_id" IS DISTINCT FROM OLD."family_id" OR NEW."mode" IS DISTINCT FROM OLD."mode" OR NEW."rehearsal_epoch_id" IS DISTINCT FROM OLD."rehearsal_epoch_id" OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" OR NEW."credential_epoch" IS DISTINCT FROM OLD."credential_epoch" OR NEW."assisted_by_id" IS DISTINCT FROM OLD."assisted_by_id" OR (OLD."revoked_at" IS NOT NULL AND NEW."revoked_at" IS DISTINCT FROM OLD."revoked_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                -- #306: activity renews the idle limit only up to this
                -- statement's clock, never to a future instant.
                IF NEW."last_activity_at" IS DISTINCT FROM OLD."last_activity_at"
                   AND NEW."last_activity_at" > statement_timestamp() THEN
                    RAISE EXCEPTION 'Session activity cannot be recorded in the future'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- New: Open form's sign-in. Python's hand-off (accounts/assisted_entry.py)
-- calls it instead of stewardship_family_login_v1, with the Family's code
-- MACs and the Admin portal session whose hand-off it redeems. It admits
-- what stewardship_family_login_v1 admits in Production, and also the
-- current campaign after its end or close, never an archived one, never
-- before it starts, never in Testing; it requires that Admin session to be
-- live (unrevoked, within its 12-hour and 60-minute deadlines,
-- accounts/session_policy.py) and its user a current Administrator or
-- Staff member; and it records that user on the new row.
CREATE FUNCTION public.stewardship_family_assisted_login_v1(session_key character varying, family_uuid uuid, correlation uuid, code_digests jsonb, admin_session uuid) RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    runtime_row public.stewardship_system_configuration;
    campaign_row public.stewardship_campaign;
    config_row public.stewardship_campaign_configuration;
    credential_row public.stewardship_campaign_credentials;
    admin_row public.stewardship_portal_session;
    link_epoch uuid;
    matched uuid[];
    instant timestamptz := statement_timestamp();
    session_uuid uuid := gen_random_uuid();
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web login signs in through Open form' USING ERRCODE='42501';
    END IF;
    -- A code, for a fresh live Django session of this sign-in, as
    -- stewardship_family_login_v1 requires; Open form never uses a link.
    IF code_digests IS NULL OR family_uuid IS NULL OR correlation IS NULL
       OR admin_session IS NULL
       OR NOT EXISTS (SELECT 1 FROM public.django_session
                      WHERE django_session.session_key=stewardship_family_assisted_login_v1.session_key
                        AND expire_date>instant)
       OR EXISTS (SELECT 1 FROM public.stewardship_portal_session
                  WHERE session_id=stewardship_family_assisted_login_v1.session_key)
       OR EXISTS (SELECT 1 FROM public.stewardship_family_session
                  WHERE session_id=stewardship_family_assisted_login_v1.session_key) THEN
        RETURN NULL;
    END IF;
    -- The Admin whose hand-off this is: a live portal session of a current
    -- Administrator or Staff member (the roles that may open forms,
    -- accounts/assisted_entry.py may_open).
    SELECT * INTO admin_row FROM public.stewardship_portal_session
        WHERE id=admin_session FOR SHARE;
    IF admin_row.id IS NULL OR admin_row.revoked_at IS NOT NULL
       OR instant>=admin_row.expires_at
       OR instant>=admin_row.last_activity_at + interval '60 minutes'
       OR NOT public.stewardship_export_authorized_v1(admin_row.principal_id) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO runtime_row FROM public.stewardship_system_configuration FOR SHARE;
    SELECT family_link_epoch INTO link_epoch FROM public.stewardship_credential_deployment FOR SHARE;
    SELECT * INTO campaign_row FROM public.stewardship_campaign WHERE id=runtime_row.current_campaign_id;
    SELECT * INTO config_row FROM public.stewardship_campaign_configuration
        WHERE id=campaign_row.active_configuration_id;
    SELECT * INTO credential_row FROM public.stewardship_campaign_credentials
        WHERE campaign_id=campaign_row.id FOR SHARE;
    -- The same admission as accounts/family_authentication.py _scope with
    -- campaigns/lifecycle.py staff_entry_admitted, checked at this statement.
    IF runtime_row.id IS NULL OR runtime_row.restore_review_required OR link_epoch IS NULL
       OR runtime_row.mode<>'production'
       OR campaign_row.id IS NULL OR config_row.id IS NULL OR credential_row.id IS NULL
       OR credential_row.go_live_gate OR credential_row.population_dirty
       OR public.stewardship_campaign_now_v1()<config_row.starts_at
       OR campaign_row.state NOT IN ('scheduled','active','closed')
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign
                      WHERE id=family_uuid AND campaign_id=campaign_row.id AND portal_eligible) THEN
        RETURN NULL;
    END IF;
    -- One MAC per accepted key, at most MAX_RING_KEYS (32,
    -- accounts/cryptography.py); the presented set must identify exactly
    -- this one eligible Family, as the Python lookup requires.
    IF jsonb_typeof(code_digests)<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(code_digests)) NOT BETWEEN 1 AND 32 THEN
        RETURN NULL;
    END IF;
    SELECT array_agg(DISTINCT mac.family_id) INTO matched
    FROM jsonb_each_text(code_digests) presented
    JOIN public.stewardship_family_code_mac mac
        ON mac.key_id=presented.key AND mac.digest=presented.value
        AND mac.campaign_id=campaign_row.id
    JOIN public.stewardship_family_campaign eligible
        ON eligible.id=mac.family_id AND eligible.portal_eligible;
    IF matched IS DISTINCT FROM ARRAY[family_uuid] THEN
        RETURN NULL;
    END IF;
    -- FAMILY_ABSOLUTE is 4 hours (accounts/session_policy.py).
    INSERT INTO public.stewardship_family_session(id, actor_id, correlation_id, version, mode,
        authenticated_at, last_activity_at, expires_at, family_id, session_id,
        rehearsal_epoch_id, credential_epoch, presence_section, assisted_by_id)
    VALUES (session_uuid, NULL, correlation, 1, runtime_row.mode, instant, instant,
        instant + interval '4 hours', family_uuid, session_key, NULL, link_epoch, '',
        admin_row.principal_id);
    RETURN session_uuid;
END $$;
ALTER FUNCTION public.stewardship_family_assisted_login_v1(character varying, uuid, uuid, jsonb, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_family_assisted_login_v1(character varying, uuid, uuid, jsonb, uuid) FROM PUBLIC;

-- stewardship_family_baseline_guard_v1: the baseline body plus #2294's admission.
CREATE OR REPLACE FUNCTION public.stewardship_family_baseline_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    session_row public.stewardship_family_session;
    runtime_row public.stewardship_system_configuration;
    family_row public.stewardship_family_campaign;
    campaign_row public.stewardship_campaign;
    campaign_config public.stewardship_campaign_configuration;
    credential_row public.stewardship_campaign_credentials;
    instant timestamptz := clock_timestamp();
    previous_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2
          AND mode='ExclusiveLock' AND granted
    ) THEN
        RAISE EXCEPTION 'Form metadata requires ordered admission' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF public.stewardship_test_response_cleanup_v1(OLD.mode,OLD.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Form metadata deletion requires its retention owner' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.state <> 'open' OR NEW.state = 'open'
           OR NEW.version <> OLD.version + 1
           OR (to_jsonb(NEW) - ARRAY['state','ended_at','version','updated_at'])
                IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['state','ended_at','version','updated_at'])
           OR NEW.ended_at > instant THEN
            RAISE EXCEPTION 'Form metadata bindings are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.state = 'expired' AND NEW.expires_at > instant THEN
            RAISE EXCEPTION 'The form has not expired' USING ERRCODE='23514';
        END IF;
        NEW.updated_at := instant;
        RETURN NEW;
    END IF;
    SELECT * INTO runtime_row FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO session_row FROM public.stewardship_family_session
        WHERE id=NEW.family_session_id FOR UPDATE;
    IF NOT FOUND OR session_row.family_id <> NEW.family_id
       OR session_row.revoked_at IS NOT NULL
       OR instant >= session_row.expires_at
       OR instant >= session_row.last_activity_at + interval '60 minutes'
       OR session_row.mode <> runtime_row.mode
       OR session_row.rehearsal_epoch_id IS DISTINCT FROM NEW.rehearsal_epoch_id
       OR NEW.mode <> (CASE session_row.mode WHEN 'testing' THEN 'test' ELSE 'live' END)
       OR NEW.expires_at > session_row.expires_at
       OR NEW.expires_at <= instant OR NEW.state <> 'open'
       OR NEW.version <> 1 OR NEW.actor_id IS DISTINCT FROM NEW.family_id
       OR NEW.configuration_id IS DISTINCT FROM runtime_row.active_configuration_id
       OR runtime_row.restore_review_required
       OR session_row.credential_epoch IS DISTINCT FROM
           (SELECT family_link_epoch FROM public.stewardship_credential_deployment)
    THEN
        RAISE EXCEPTION 'Form session admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO family_row FROM public.stewardship_family_campaign
        WHERE id=NEW.family_id FOR UPDATE;
    SELECT * INTO campaign_row FROM public.stewardship_campaign
        WHERE id=family_row.campaign_id;
    SELECT * INTO campaign_config FROM public.stewardship_campaign_configuration
        WHERE id=campaign_row.active_configuration_id;
    SELECT * INTO credential_row FROM public.stewardship_campaign_credentials
        WHERE campaign_id=campaign_row.id;
    IF family_row.id IS NULL OR NOT family_row.portal_eligible
       OR campaign_row.id IS DISTINCT FROM runtime_row.current_campaign_id
       OR campaign_config.id IS NULL OR credential_row.id IS NULL
       OR credential_row.go_live_gate OR credential_row.population_dirty
       -- A live session Staff opened through Open form (assisted_by_id,
       -- which only stewardship_family_assisted_login_v1 sets) may also
       -- open the form after the campaign ends or closes (#2294); a
       -- Family's own session may not.
       OR NOT (campaign_config.starts_at <= public.stewardship_campaign_now_v1()
               AND (public.stewardship_campaign_now_v1() < campaign_config.ends_at
                    OR (NEW.mode='live' AND session_row.assisted_by_id IS NOT NULL)))
       OR (NEW.mode='live' AND campaign_row.state NOT IN ('scheduled','active')
           AND (session_row.assisted_by_id IS NULL OR campaign_row.state<>'closed'))
       OR (NEW.mode='test' AND (campaign_row.state <> 'draft'
           OR credential_row.rehearsal_epoch_id IS DISTINCT FROM NEW.rehearsal_epoch_id
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch
                          WHERE id=NEW.rehearsal_epoch_id AND state='active')))
       OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_source_current current_source
             JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=current_source.snapshot_id
           WHERE snapshot.id=NEW.source_id AND snapshot.state='promoted'
             AND snapshot.compacted_at IS NULL
       )
    THEN
        RAISE EXCEPTION 'Form campaign or source admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT id INTO previous_id FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode
          AND rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
        ORDER BY family_version DESC LIMIT 1;
    IF previous_id IS DISTINCT FROM NEW.prior_submission_id THEN
        RAISE EXCEPTION 'The effective Family response changed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- stewardship_submission_guard_v1: the baseline body plus #2294's admission.
CREATE OR REPLACE FUNCTION public.stewardship_submission_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    baseline public.stewardship_family_form_baseline;
    session_row public.stewardship_family_session;
    runtime_row public.stewardship_system_configuration;
    campaign_row public.stewardship_campaign;
    config_row public.stewardship_campaign_configuration;
    previous_id uuid;
    previous_version bigint;
    last_sequence bigint;
    member_key text;
    member_answer jsonb;
    member_proposed boolean;
    birth_value jsonb;
    field_name text;
    field_value jsonb;
    source_value jsonb;
    display_value text;
    allowed_fields text[] := ARRAY['prefix','first_name','middle_name','last_name','suffix','nickname',
        'maiden_name','birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language'];
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF TG_OP='DELETE' AND public.stewardship_test_response_cleanup_v1(OLD.mode,OLD.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Submission history is immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Submission requires ordered admission' USING ERRCODE='23514';
    END IF;
    SELECT * INTO baseline FROM public.stewardship_family_form_baseline WHERE id=NEW.baseline_id FOR UPDATE;
    SELECT * INTO runtime_row FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO session_row FROM public.stewardship_family_session WHERE id=baseline.family_session_id FOR UPDATE;
    SELECT * INTO campaign_row FROM public.stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO config_row FROM public.stewardship_campaign_configuration WHERE id=campaign_row.active_configuration_id;
    -- The insertion statement owns the business instant; a Python preflight
    -- timestamp is not equal to the later INSERT's statement timestamp.
    NEW.submitted_at := public.stewardship_campaign_now_v1();
    NEW.submitted_on := (NEW.submitted_at AT TIME ZONE public.stewardship_timezone_name_v1(config_row.timezone))::date;
    IF baseline.id IS NULL OR baseline.state <> 'open' OR baseline.expires_at <= clock_timestamp()
        OR session_row.id IS NULL OR session_row.revoked_at IS NOT NULL
        OR session_row.expires_at <= clock_timestamp()
        OR session_row.last_activity_at + interval '60 minutes' <= clock_timestamp()
        OR NEW.family_id IS DISTINCT FROM baseline.family_id
        OR NEW.family_id IS DISTINCT FROM session_row.family_id
        OR NEW.actor_id IS DISTINCT FROM NEW.family_id
        OR NEW.mode IS DISTINCT FROM baseline.mode
        OR NEW.rehearsal_epoch_id IS DISTINCT FROM baseline.rehearsal_epoch_id
        OR NEW.rehearsal_epoch_id IS DISTINCT FROM session_row.rehearsal_epoch_id
        OR NEW.prior_submission_id IS DISTINCT FROM baseline.prior_submission_id
        OR NEW.reviewed_source_id IS DISTINCT FROM baseline.source_id
        OR NEW.form_schema IS DISTINCT FROM baseline.form_schema
        OR NEW.form_schema <> 'family-response-v1'
        OR NEW.configuration_id IS DISTINCT FROM runtime_row.active_configuration_id
        OR runtime_row.restore_review_required OR session_row.mode <> runtime_row.mode
        OR NEW.campaign_id IS DISTINCT FROM runtime_row.current_campaign_id
        OR config_row.id IS NULL OR campaign_row.id IS NULL
        OR NOT (config_row.values->'modules' OPERATOR(pg_catalog.<@) '["census","ministry","financial"]'::jsonb)
        OR NEW.submitted_on IS DISTINCT FROM (NEW.submitted_at AT TIME ZONE public.stewardship_timezone_name_v1(config_row.timezone))::date
        OR NEW.submitted_at < config_row.starts_at
        -- Open form's session (assisted_by_id) may submit a live response
        -- after the campaign ends or closes (#2294); a Family's own may not.
        OR (NEW.submitted_at >= config_row.ends_at
            AND (NEW.mode<>'live' OR session_row.assisted_by_id IS NULL))
        OR (NOT (config_row.values->'modules' OPERATOR(pg_catalog.?) 'financial') AND NEW.annual_pledge IS NOT NULL)
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign
            WHERE id=NEW.family_id AND campaign_id=NEW.campaign_id AND portal_eligible)
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_current
            WHERE snapshot_id=NEW.validation_source_id)
        OR session_row.credential_epoch IS DISTINCT FROM
            (SELECT family_link_epoch FROM public.stewardship_credential_deployment)
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials credentials
            WHERE credentials.campaign_id=NEW.campaign_id AND NOT go_live_gate AND NOT population_dirty
              AND ((NEW.mode='live' AND runtime_row.mode='production'
                    AND (campaign_row.state IN ('scheduled','active')
                         OR (campaign_row.state='closed' AND session_row.assisted_by_id IS NOT NULL)))
               OR (NEW.mode='test' AND runtime_row.mode='testing' AND campaign_row.state='draft'
                  AND credentials.rehearsal_epoch_id=NEW.rehearsal_epoch_id
                  AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch
                      WHERE id=NEW.rehearsal_epoch_id AND state='active'))))
    THEN
        RAISE EXCEPTION 'Submission provenance or admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT id,family_version INTO previous_id,previous_version FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode
          AND rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
        ORDER BY family_version DESC LIMIT 1;
    SELECT coalesce(max(family_version),0) INTO previous_version FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode;
    SELECT coalesce(max(campaign_sequence),0) INTO last_sequence FROM public.stewardship_submission
        WHERE campaign_id=NEW.campaign_id AND mode=NEW.mode;
    IF NEW.prior_submission_id IS DISTINCT FROM previous_id
        OR NEW.family_version <> coalesce(previous_version,0)+1
        OR NEW.campaign_sequence <> last_sequence+1
        OR jsonb_typeof(NEW.answers) <> 'object'
        OR NOT (NEW.answers OPERATOR(pg_catalog.?&) ARRAY['schema','family','members','proposed_members','ministries','additional_information','cannot_attend','service'])
        OR NEW.answers - ARRAY['schema','family','members','proposed_members','ministries','additional_information','financial','cannot_attend','service'] <> '{}'::jsonb
        OR jsonb_typeof(NEW.answers->'cannot_attend') <> 'boolean'
        OR NEW.answers->>'schema' IS DISTINCT FROM NEW.form_schema
        OR jsonb_typeof(NEW.answers->'family') <> 'object'
        OR jsonb_typeof(NEW.answers->'members') <> 'object'
        OR jsonb_typeof(NEW.answers->'proposed_members') <> 'object'
        OR jsonb_typeof(NEW.answers->'additional_information') <> 'string'
        OR length(NEW.answers->>'additional_information') > 5000
    THEN
        RAISE EXCEPTION 'Submission aggregate or sequence is invalid' USING ERRCODE='23514';
    END IF;
    IF config_row.values->'modules' OPERATOR(pg_catalog.?) 'financial' THEN
        PERFORM public.stewardship_response_financial_guard_v1(
            NEW.answers->'financial',config_row.values->'share_options',NEW.annual_pledge);
    ELSIF NEW.answers OPERATOR(pg_catalog.?) 'financial' THEN
        RAISE EXCEPTION 'Disabled financial module must omit its answers' USING ERRCODE='23514';
    END IF;
    IF config_row.values->'modules' OPERATOR(pg_catalog.?) 'census' THEN
    IF NOT ((NEW.answers->'family') OPERATOR(pg_catalog.?&) ARRAY['home_address','mailing_address','email_opt_out','mailing_same_as_home'])
       OR (NEW.answers->'family')-ARRAY['home_address','mailing_address','email_opt_out','mailing_same_as_home'] <> '{}'::jsonb
       OR jsonb_typeof(NEW.answers#>'{family,mailing_same_as_home}') IS DISTINCT FROM 'boolean'
    THEN
        RAISE EXCEPTION 'Submission household aggregate is invalid' USING ERRCODE='23514';
    END IF;
    PERFORM public.stewardship_response_address_guard_v1(NEW.answers#>'{family,home_address}');
    PERFORM public.stewardship_response_address_guard_v1(NEW.answers#>'{family,mailing_address}');
    PERFORM public.stewardship_response_comparison_v1('email_opt_out',NEW.answers#>'{family,email_opt_out}');
    ELSIF NEW.answers->'family' <> '{}'::jsonb OR NEW.answers->'proposed_members' <> '{}'::jsonb THEN
        RAISE EXCEPTION 'Disabled census must not contain editable answers' USING ERRCODE='23514';
    END IF;
    -- A response is a complete active household, not a patch or a caller-chosen
    -- list. These checks also protect direct exact-role writes outside Python.
    IF (SELECT coalesce(array_agg(key ORDER BY key),'{}'::text[]) FROM jsonb_object_keys(NEW.answers->'members') key)
       IS DISTINCT FROM (
           SELECT coalesce(array_agg(membership.source_key::text ORDER BY membership.source_key),'{}'::text[])
           FROM public.stewardship_snapshot_member membership
           JOIN public.stewardship_source_member member ON member.id=membership.payload_id
           JOIN public.stewardship_family_campaign family ON member.family_key=family.family_duid::text
           WHERE membership.snapshot_id=NEW.validation_source_id AND family.id=NEW.family_id
             AND member.canonical::jsonb->'active'='true'::jsonb
             AND member.canonical::jsonb->'deceased'='false'::jsonb) THEN
        RAISE EXCEPTION 'Submission must include the current active household' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(NEW.answers->'proposed_members')) > 100 THEN
        RAISE EXCEPTION 'Too many proposed household Members' USING ERRCODE='23514';
    END IF;
    FOR member_key,member_answer,member_proposed IN
        SELECT key,value,false FROM jsonb_each(NEW.answers->'members')
        UNION ALL SELECT key,value,true FROM jsonb_each(NEW.answers->'proposed_members') LOOP
        IF NOT (config_row.values->'modules' OPERATOR(pg_catalog.?) 'census') THEN
            IF member_answer <> '{}'::jsonb THEN
                RAISE EXCEPTION 'Disabled Member census must be empty' USING ERRCODE='23514';
            END IF;
            CONTINUE;
        END IF;
        IF member_proposed AND (member_key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR member_key='00000000-0000-0000-0000-000000000000') THEN
            RAISE EXCEPTION 'Proposed Member requires canonical local identity' USING ERRCODE='23514';
        END IF;
        IF NOT member_proposed AND jsonb_typeof(member_answer)='object'
           AND (member_answer OPERATOR(pg_catalog.?) 'moved_household' OR member_answer OPERATOR(pg_catalog.?) 'deceased_status') THEN
            IF member_answer->'confirmed' IS DISTINCT FROM 'true'::jsonb THEN
                RAISE EXCEPTION 'Terminal Member requires explicit confirmation' USING ERRCODE='23514';
            END IF;
            IF member_answer OPERATOR(pg_catalog.?) 'moved_household' THEN
                IF member_answer IS DISTINCT FROM '{"moved_household":true,"confirmed":true}'::jsonb THEN
                    RAISE EXCEPTION 'Moved household request has unexpected fields' USING ERRCODE='23514';
                END IF;
            ELSE
                IF NOT (member_answer OPERATOR(pg_catalog.?&) ARRAY['deceased_status','confirmed','death_date'])
                   OR member_answer-ARRAY['deceased_status','confirmed','death_date'] <> '{}'::jsonb
                   OR member_answer->'deceased_status' IS DISTINCT FROM 'true'::jsonb THEN
                    RAISE EXCEPTION 'Deceased request has unexpected fields' USING ERRCODE='23514';
                END IF;
                PERFORM public.stewardship_response_comparison_v1('death_date',member_answer->'death_date');
                birth_value := public.stewardship_response_field_source_v1(
                    NEW.validation_source_id,NEW.family_id,member_key,'birth_date');
                IF member_answer->'death_date'<>'null'::jsonb AND (
                    (member_answer->>'death_date')::date>NEW.submitted_on
                    OR (birth_value->'available'='true'::jsonb AND birth_value->'value'<>'null'::jsonb
                        AND (member_answer->>'death_date')::date<(birth_value->>'value')::date)) THEN
                    RAISE EXCEPTION 'Death date must respect recorded birth and parish day' USING ERRCODE='23514';
                END IF;
            END IF;
            CONTINUE;
        END IF;
        IF jsonb_typeof(member_answer)<>'object' OR NOT (member_answer OPERATOR(pg_catalog.?&) allowed_fields)
           OR member_answer-allowed_fields<>'{}'::jsonb THEN
            RAISE EXCEPTION 'Submission member fields are incomplete' USING ERRCODE='23514';
        END IF;
        FOR field_name,field_value IN SELECT * FROM jsonb_each(member_answer) LOOP
            source_value := CASE WHEN member_proposed THEN jsonb_build_object('available',false,'value',NULL)
                ELSE public.stewardship_response_field_source_v1(
                    NEW.validation_source_id,NEW.family_id,member_key,field_name) END;
            PERFORM public.stewardship_response_comparison_v1(field_name,field_value);
            IF field_name='birth_date' THEN
                IF field_value<>'null'::jsonb AND (field_value#>>'{}')::date>NEW.submitted_on THEN
                    RAISE EXCEPTION 'Member birth date cannot be in the future' USING ERRCODE='23514';
                END IF;
                CONTINUE;
            END IF;
            IF field_value='null'::jsonb THEN
                IF field_name IN ('first_name','last_name','gender','language')
                   OR (source_value->'available'='true'::jsonb AND source_value->'value'<>'null'::jsonb) THEN
                    RAISE EXCEPTION 'Member nullable field has no supported blank source' USING ERRCODE='23514';
                END IF;
                CONTINUE;
            END IF;
            display_value := CASE WHEN field_name IN ('home_phone','mobile_phone','work_phone')
                                  AND jsonb_typeof(field_value)='object'
                THEN field_value->>'display' ELSE field_value#>>'{}' END;
            IF length(display_value)>(CASE WHEN field_name='email' THEN 254 ELSE 100 END)
               OR display_value IS DISTINCT FROM public.stewardship_response_comparison_v1('first_name',to_jsonb(display_value))
               OR display_value ~ U&'[\0001-\001F\007F\0085\2028\2029]'
               OR (field_name IN ('first_name','last_name','gender','language') AND display_value='') THEN
                RAISE EXCEPTION 'Member text field is not normalized or complete' USING ERRCODE='23514';
            END IF;
            IF field_name='gender' AND display_value NOT IN ('Male','Female','Unspecified')
               AND public.stewardship_response_comparison_v1(field_name,field_value)
                   IS DISTINCT FROM public.stewardship_response_comparison_v1(field_name,source_value->'value') THEN
                RAISE EXCEPTION 'Member gender choice is invalid' USING ERRCODE='23514';
            END IF;
            IF field_name='marital_status' AND display_value NOT IN ('','Annulled','Divorced','Married','Single','Separated','Widowed')
               AND public.stewardship_response_comparison_v1(field_name,field_value)
                   IS DISTINCT FROM public.stewardship_response_comparison_v1(field_name,source_value->'value') THEN
                RAISE EXCEPTION 'Member marital choice is invalid' USING ERRCODE='23514';
            END IF;
            IF field_name IN ('home_phone','mobile_phone','work_phone') AND display_value<>''
               AND field_value IS DISTINCT FROM source_value->'value'
               AND coalesce(field_value->>'normalized','') !~ '^\+[1-9][0-9]{1,14}(;ext=[0-9]{1,12})?$' THEN
                RAISE EXCEPTION 'Member new phone must have an international comparison number' USING ERRCODE='23514';
            END IF;
        END LOOP;
    END LOOP;
    PERFORM public.stewardship_ministry_answers_guard_v1(NEW,config_row);
    PERFORM public.stewardship_service_answers_guard_v1(NEW,config_row);
    IF (NEW.answers#>>'{family,mailing_same_as_home}')::boolean
       AND (NEW.answers#>'{family,home_address}'='null'::jsonb
            OR public.stewardship_response_comparison_v1('home_address',NEW.answers#>'{family,home_address}')
               IS DISTINCT FROM public.stewardship_response_comparison_v1('mailing_address',NEW.answers#>'{family,mailing_address}'))
    THEN
        RAISE EXCEPTION 'Submission mailing choice requires matching addresses' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- stewardship_submission_receipt_command_v1: the baseline body plus #2294's receipt.
CREATE OR REPLACE FUNCTION public.stewardship_submission_receipt_command_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE s public.stewardship_submission%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE;
    task public.stewardship_task_run%ROWTYPE;
    expected jsonb; content jsonb;
    rendering uuid:=gen_random_uuid(); hold uuid; hold_version bigint;
    mode text; routing text; proof text;
BEGIN
    -- The table owner already has unrestricted DDL/data access (also used by
    -- disposable test setup). No other runtime login inherits this Web port.
    IF session_user<>'pk_stewardship_web' AND session_user<>(
        SELECT pg_get_userbyid(relowner) FROM pg_class
        WHERE oid='public.stewardship_submission_receipt'::regclass)
    THEN RAISE EXCEPTION 'Receipt creation requires the submission owner' USING ERRCODE='42501'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND pg_locks.mode='ExclusiveLock' AND granted)
    THEN RAISE EXCEPTION 'Receipt creation requires ordered submission admission' USING ERRCODE='23514'; END IF;
    SELECT * INTO s FROM public.stewardship_submission WHERE id=NEW.submission_id;
    SELECT * INTO r FROM public.stewardship_system_configuration;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=s.campaign_id;
    mode:=CASE s.mode WHEN 'live' THEN 'production' ELSE 'testing' END;
    routing:=CASE s.mode WHEN 'live' THEN 'production' ELSE 'testing_override' END;
    IF s.id IS NULL OR NEW.actor_id IS DISTINCT FROM s.family_id
       OR NEW.correlation_id IS DISTINCT FROM s.correlation_id
       OR r.current_campaign_id IS DISTINCT FROM s.campaign_id OR r.mode<>mode
       OR r.active_configuration_id IS DISTINCT FROM s.configuration_id
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_family_form_baseline
           WHERE id=s.baseline_id AND state='open')
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_credentials k
           JOIN public.stewardship_source_current cur ON cur.snapshot_id=k.source_snapshot_id
             AND cur.generation=k.source_generation
           JOIN public.stewardship_family_campaign f ON f.campaign_id=k.campaign_id
           WHERE k.campaign_id=c.id AND f.id=s.family_id AND f.source_generation=cur.generation
             AND cur.snapshot_id=s.validation_source_id AND NOT k.population_dirty
             AND ((s.mode='live' AND (c.state IN ('scheduled','active')
                   -- A response Open form entered after close (#2294) gets
                   -- the ordinary receipt, as every Open form response does.
                   OR (c.state='closed' AND EXISTS(
                       SELECT 1 FROM public.stewardship_family_form_baseline b
                       JOIN public.stewardship_family_session fs ON fs.id=b.family_session_id
                       WHERE b.id=s.baseline_id AND fs.assisted_by_id IS NOT NULL))))
               OR (s.mode='test' AND c.state='draft' AND k.rehearsal_epoch_id=s.rehearsal_epoch_id
                 AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e
                     WHERE e.id=s.rehearsal_epoch_id AND e.state='active'))))
    THEN RAISE EXCEPTION 'Receipt lacks its current admitted submission' USING ERRCODE='23514'; END IF;
    expected:=public.stewardship_family_mail_recipients_v1(s.family_id);
    IF jsonb_array_length(expected)=0 THEN
        IF NEW.disposition<>'no_deliverable_recipient' OR NEW.outbox_id IS NOT NULL OR NEW.preparation IS NOT NULL
        THEN RAISE EXCEPTION 'Receipt has no deliverable source recipients' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO task FROM public.stewardship_task_run WHERE id=(NEW.preparation->>'task_id')::uuid;
    IF NEW.disposition<>'queued' OR NEW.outbox_id IS NULL
       OR jsonb_typeof(NEW.preparation) IS DISTINCT FROM 'object'
       OR NEW.preparation-ARRAY['task_id']<>'{}'::jsonb
       OR task.id IS NULL OR task.root_id<>task.id OR task.state<>'queued' OR task.version<>1
       OR task.task_type<>'outbox_delivery' OR task.domain_request_id IS DISTINCT FROM NEW.outbox_id
       OR task.initiated_by_id IS DISTINCT FROM s.family_id OR task.correlation_id<>s.correlation_id
       OR task.idempotency_key IS DISTINCT FROM NEW.outbox_id::text
    THEN RAISE EXCEPTION 'Receipt requires exact local preparation' USING ERRCODE='23514'; END IF;
    content:=public.stewardship_receipt_seed_v1(s.family_id,s.configuration_id,mode);
    IF mode='production' AND c.delivery_paused THEN
        INSERT INTO public.stewardship_delivery_pause_hold(id,actor_id,correlation_id,campaign_id,pause_version)
            VALUES(gen_random_uuid(),s.family_id,s.correlation_id,c.id,c.pause_version)
            ON CONFLICT(campaign_id,pause_version) DO NOTHING;
        SELECT id,pause_version INTO hold,hold_version FROM public.stewardship_delivery_pause_hold
            WHERE campaign_id=c.id AND pause_version=c.pause_version;
    END IF;
    proof:=encode(sha256(convert_to(NEW.id::text||':'||content::text,'UTF8')),'hex');
    INSERT INTO public.stewardship_outbox_message
        (id,actor_id,correlation_id,version,command_id,command_digest,action,
         provider_key_digest,provider_message_digest,evidence_digest,evidence_note,reason,
         scope_id,semantic_key,campaign_id,family_id,mode,routing,purpose,task_id,render_id,
         credential_namespace,pause_hold_id,pause_version)
    VALUES(NEW.outbox_id,s.family_id,s.correlation_id,1,NEW.id,proof,'created',
        '','','','','',c.id,s.id,c.id,s.family_id,mode,routing,'receipt',task.id,rendering,'none',hold,hold_version);
    INSERT INTO public.stewardship_outbox_render
        (id,actor_id,correlation_id,message_id,configuration_id,template_id,sender,reply_to,
         intended_recipients,routed_recipients,subject,html,text,payload_digest)
    VALUES(rendering,s.family_id,s.correlation_id,NEW.outbox_id,s.configuration_id,(content->>'template_id')::uuid,
        content->>'sender',content->>'reply_to',content->'intended_recipients',content->'routed_recipients',
        content->>'subject',content->>'html',content->>'text',content->>'payload_digest');
    NEW.preparation:=NULL;
    RETURN NEW;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_submission_receipt_command_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_submission_receipt_command_v1() FROM PUBLIC;

-- stewardship_token_campaign_effects_v1: the baseline body, except that the
-- close no longer revokes a session Open form started, so Staff mid-entry
-- at the close keep their unsaved work (SECURITY DEFINER, as before).
CREATE OR REPLACE FUNCTION public.stewardship_token_campaign_effects_v1() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NEW.active_token_generation_id IS DISTINCT FROM OLD.active_token_generation_id AND NEW.active_token_generation_id IS NOT NULL THEN
        UPDATE stewardship_family_token_generation SET state='active',version=version+1
        WHERE id=NEW.active_token_generation_id AND state='ready';
    END IF;
    IF (NEW.state='closed' AND OLD.state<>'closed') OR (NEW.state='draft' AND OLD.state='scheduled') THEN
        UPDATE stewardship_family_token SET ciphertext=NULL,digest=NULL,destroyed_at=stewardship_campaign_now_v1(),version=version+1
        WHERE campaign_id=NEW.id AND destroyed_at IS NULL;
        UPDATE stewardship_family_token_generation SET state='superseded',version=version+1
        WHERE campaign_id=NEW.id AND state NOT IN ('superseded','cancelled');
        -- At the close, a session Open form's hand-off started (#2294:
        -- assisted_by_id, set only by stewardship_family_assisted_login_v1)
        -- stays live, so Staff mid-entry keep their unsaved work; every
        -- Family's own session ends here, and every session ends when a
        -- scheduled campaign returns to draft.
        UPDATE stewardship_family_session SET revoked_at=greatest(stewardship_campaign_now_v1(),last_activity_at),version=version+1
        WHERE family_id IN(SELECT id FROM stewardship_family_campaign WHERE campaign_id=NEW.id) AND revoked_at IS NULL
          AND (NEW.state<>'closed' OR assisted_by_id IS NULL);
    END IF;
    RETURN NEW;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_token_campaign_effects_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_token_campaign_effects_v1() FROM PUBLIC;

-- Refuse to commit unless everything is installed as declared. It reads
-- only the catalog. The new function is checked first, on its own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure casts below
-- would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_family_assisted_login_v1(character varying, uuid, uuid, jsonb, uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0188: the Open form sign-in is not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
           WHERE attrelid='public.stewardship_family_session'::regclass
             AND attname='assisted_by_id' AND atttypid='uuid'::regtype
             AND NOT attnotnull AND NOT attisdropped) THEN
        RAISE EXCEPTION 'Migration 0188: the Family session has no assisted_by_id';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_family_assisted_login_v1(character varying, uuid, uuid, jsonb, uuid)'::regprocedure
             AND p.prosecdef AND p.provolatile='v' AND p.prorettype='uuid'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_web''%'
             AND p.prosrc LIKE '%stewardship_export_authorized_v1(admin_row.principal_id)%'
             AND p.prosrc LIKE '%runtime_row.mode<>''production''%'
             AND p.prosrc LIKE '%campaign_row.state NOT IN (''scheduled'',''active'',''closed'')%'
             AND p.prosrc LIKE '%admin_row.principal_id);%')
       OR has_function_privilege('public',
           'public.stewardship_family_assisted_login_v1(character varying, uuid, uuid, jsonb, uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0188: the Open form sign-in is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_family_session_mutable_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.prosrc LIKE '%NEW."assisted_by_id" IS DISTINCT FROM OLD."assisted_by_id"%') THEN
        RAISE EXCEPTION 'Migration 0188: assisted_by_id is not immutable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_family_baseline_guard_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%(session_row.assisted_by_id IS NULL OR campaign_row.state<>''closed'')%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_submission_guard_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%(NEW.mode<>''live'' OR session_row.assisted_by_id IS NULL)%'
             AND p.prosrc LIKE '%(campaign_row.state=''closed'' AND session_row.assisted_by_id IS NOT NULL)%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_submission_receipt_command_v1()'::regprocedure
             AND p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%WHERE b.id=s.baseline_id AND fs.assisted_by_id IS NOT NULL%')
       OR has_function_privilege('public',
           'public.stewardship_submission_receipt_command_v1()', 'EXECUTE')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_token_campaign_effects_v1()'::regprocedure
             AND p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%AND (NEW.state<>''closed'' OR assisted_by_id IS NULL);%')
       OR has_function_privilege('public',
           'public.stewardship_token_campaign_effects_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0188: the closed-campaign Open form admission is not installed as declared';
    END IF;
END
$check$;
