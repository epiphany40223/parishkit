-- Frozen forward migration file 0019 (the repository-wide file sequence;
-- Django's stewardship_campaigns.0004): the restore review (#537). This file
-- is installed by campaigns/migrations/0004_restore_review.py and must never
-- change once released; tests/stewardship/test_schema_migration_files.py pins
-- its digest and checks that its copies of the replaced functions still equal
-- the fresh-install baseline's (functions.sql). A fresh install runs the
-- baseline, 0002 to 0018 and then this, and ends in the same catalog as an
-- upgraded database.
--
-- Before this file nothing could start or end a restore review: the runtime
-- row's restore fields changed only through a runtime transition, and the
-- transition guard admitted only return_testing and campaign. This adds:
--
--   * Two runtime transitions. restore_begin is the operator's restore
--     command (the admin-recovery login, or the schema owner), run after a
--     restore and before web starts: it closes the site for review with a new
--     restore id and the backup's time, and changes no mode, campaign or
--     Family credential. restore_release is a freshly signed-in
--     Administrator's release: it is refused while any email still needs a
--     hold (the list must be found first, and every hold it makes can be
--     decided during the review), then clears the gate and stamps the
--     release; mode and campaign stay as restored. Each writes its own audit event
--     (restore_review_started, restore_review_released). The runtime guard
--     now lets the restore fields move only with these transitions.
--   * stewardship_restore_hold_candidates_v1 and
--     stewardship_restore_hold_inventory_v1: during a review in Production,
--     the emails due at the restore that may have gone out after the backup
--     (the rules are at the function), and the listing that holds them. A
--     hold only suppresses; nothing is ever sent by it. Invoker's rights.
--   * stewardship_restore_decision_admitted_v1: true for the schema owner,
--     or for the web login acting for an enabled Administrator whose live
--     session signed in within the last five minutes (the check the backup
--     request guard makes). Release and every held-email decision need it.
--   * session_id and authenticated_at on stewardship_runtime_transition and
--     stewardship_restore_hold_resolution, which record that sign-in.
--   * Held-email decisions: "assumed sent" or "send again" only (not
--     "not applicable", which released the email silently). Each is final
--     (an assumption may already have cancelled the unsent copy). "Send
--     again" may come before its occurrence exists (the ordinary planner
--     then sends it once the site is released), and is refused while another
--     restore's hold still keeps the same email back. No decision is taken
--     once the review is released (settling later is #757). A named
--     recovery occurrence must still be the slot's pending one.
--   * The recovery login may only start a review; the pointer-change branch
--     of the runtime guard also keeps every restore field unchanged.
--   * stewardship_system_configuration_mutable_v1 no longer freezes the
--     restore fields (they were frozen until a guarded owner existed); the
--     runtime guard above now lets them move only with a restore transition.
--
-- No Family code or link is created, replaced or cancelled here. No function
-- here is SECURITY DEFINER, before or after. The DO block at the end refuses
-- to commit unless all of it is installed. No temporary objects: the
-- migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_runtime_transition" ADD COLUMN "session_id" uuid NULL;
ALTER TABLE "stewardship_runtime_transition" ADD COLUMN "authenticated_at" timestamp with time zone NULL;
ALTER TABLE "stewardship_restore_hold_resolution" ADD COLUMN "session_id" uuid NULL;
ALTER TABLE "stewardship_restore_hold_resolution" ADD COLUMN "authenticated_at" timestamp with time zone NULL;

-- True for the schema owner (tests, the migration), or for the web login
-- acting for an enabled Administrator whose live session has exactly the
-- sign-in instant `signed_in`, within the last five minutes. VOLATILE: it reads
-- clock_timestamp().
CREATE FUNCTION public.stewardship_restore_decision_admitted_v1(actor uuid, session uuid, signed_in timestamptz) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
        OR (current_user='pk_stewardship_web' AND actor IS NOT NULL
            AND public.stewardship_export_authorized_v1(actor,true) IS TRUE
            AND EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=session AND login.principal_id=actor
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=signed_in
                  AND login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                      AND clock_timestamp()))
$$;

-- The emails a restore review must hold and does not yet: every Production
-- invitation and reminder of the current campaign that was due when the site
-- was restored (restore_activated_at; nothing falls due during the review,
-- because the gate keeps everything from being sent) and may have gone out
-- after the backup without the restored data knowing. Work is read for the
-- schedule's current revision and the campaign's current Production cycle,
-- as the planner reads it; rows of a replaced revision or an old cycle are
-- not this email's history. A slot is left out when it is already decided
-- (fulfilled, kept back by a hold not settled as "send again" from any
-- restore, or held by this restore however decided) or was being handed to the
-- provider at the backup (delivery resolution owns that outcome). Otherwise
-- it is a candidate when the restored data still has live work for it
-- (pending or running); when it has no work yet, for every Family whatever
-- its restored eligibility (one that became eligible after the backup may
-- already have been sent it; the hold is inert otherwise); or when it is an
-- invitation whose latest attempt failed, or was skipped as undeliverable or
-- ineligible, which a later deliverability change revives
-- (deliverability_recovery.py), so the lost history may already have resent
-- it. Other ended work (skipped for other reasons, coalesced) is not held:
-- nothing revives it. Empty outside a Production restore review.
CREATE FUNCTION public.stewardship_restore_hold_candidates_v1() RETURNS TABLE(definition_id uuid, target text)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT slot.definition_id,slot.target FROM (
        SELECT d.id AS definition_id,d.kind,v.id AS revision_id,c.production_cycle,
            'family:'||f.id::text AS target,r.restore_id
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
        JOIN public.stewardship_schedule_definition d ON d.campaign_id=c.id
        JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
        JOIN public.stewardship_family_campaign f ON f.campaign_id=c.id
        WHERE r.restore_review_required AND r.mode='production' AND r.restore_activated_at IS NOT NULL
          AND d.kind IN ('initial','reminder') AND d.removed_at IS NULL
          AND v.due_at<=r.restore_activated_at
    ) slot
    WHERE NOT public.stewardship_schedule_slot_excluded_v1(slot.definition_id,'production',slot.target,'once')
      -- Held by this restore already, however it was decided.
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold mine
          WHERE mine.restore_id=slot.restore_id AND mine.definition_id=slot.definition_id
            AND mine.mode='production' AND mine.target=slot.target AND mine.slot='once')
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
          LEFT JOIN public.stewardship_outbox_message m ON m.id=o.outbox_id
          WHERE o.definition_id=slot.definition_id AND o.mode='production' AND o.target=slot.target
            AND o.slot='once' AND (o.state='delivery_unknown' OR m.state IN ('submitting','delivery_unknown')))
      AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
              WHERE o.definition_id=slot.definition_id AND o.revision_id=slot.revision_id
                AND o.production_cycle=slot.production_cycle AND o.mode='production'
                AND o.target=slot.target AND o.slot='once' AND o.state IN ('pending','running'))
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
              WHERE o.definition_id=slot.definition_id AND o.revision_id=slot.revision_id
                AND o.production_cycle=slot.production_cycle AND o.mode='production'
                AND o.target=slot.target AND o.slot='once')
          OR (slot.kind='initial' AND EXISTS(
              SELECT 1 FROM (SELECT o.state,o.reason FROM public.stewardship_schedule_occurrence o
                  WHERE o.definition_id=slot.definition_id AND o.revision_id=slot.revision_id
                    AND o.production_cycle=slot.production_cycle AND o.mode='production'
                    AND o.target=slot.target AND o.slot='once'
                  ORDER BY o.recovery_generation DESC,o.created_at DESC LIMIT 1) latest
              WHERE latest.state='failed' OR (latest.state='skipped'
                  AND latest.reason IN ('no_deliverable_recipient','family_ineligible')))))
$$;

-- Hold every candidate (above) for this restore. Safe to repeat: a slot
-- already held is no longer a candidate. Returns the number of new holds.
-- Only during a restore review; Testing holds none.
CREATE FUNCTION public.stewardship_restore_hold_inventory_v1(actor uuid, correlation uuid) RETURNS bigint
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE r public.stewardship_system_configuration%ROWTYPE;
    added bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM public.stewardship_system_configuration FOR UPDATE;
    IF r.id IS NULL OR NOT r.restore_review_required OR r.restore_backup_at IS NULL
       OR r.restore_activated_at IS NULL OR actor IS NULL OR correlation IS NULL THEN
        RAISE EXCEPTION 'Held emails are listed only during a restore review' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_restore_delivery_hold(id,actor_id,correlation_id,version,
        restore_id,mode,target,slot,backup_at,window_start,window_end,discovery,state,evidence,definition_id)
    SELECT gen_random_uuid(),actor,correlation,1,r.restore_id,'production',candidate.target,'once',
        r.restore_backup_at,r.restore_backup_at,greatest(r.restore_activated_at,r.restore_backup_at),
        'restore_inventory','unreviewed','',candidate.definition_id
    FROM public.stewardship_restore_hold_candidates_v1() candidate
    ON CONFLICT ON CONSTRAINT restore_hold_semantic DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;
    RETURN added;
END $$;

-- stewardship_restore_resolution_v1, from functions.sql: a resend may wait for the planner; a fresh Administrator decides.
CREATE OR REPLACE FUNCTION public.stewardship_restore_resolution_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    -- Two decisions (migration 0019, #537): assume it was sent, or send it
    -- again. Each is final: an assumption may already have cancelled the
    -- unsent copy, so turning it into a resend later would send nothing.
    -- (not_applicable is no longer decided: it would have released the
    -- email like "send again" without saying so.)
    IF h.id IS NULL OR NEW.version<>h.version+1 OR NEW.actor_id IS NULL OR btrim(NEW.evidence)=''
       OR NEW.state NOT IN ('assumed_delivered','resend_authorized')
       OR h.state<>'unreviewed'
       OR (NEW.state='resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM stewardship_schedule_occurrence o WHERE o.id=NEW.recovery_occurrence_id
           AND o.definition_id=h.definition_id AND o.mode=h.mode AND o.target=h.target AND o.slot=h.slot AND o.state='pending'
       )) OR (NEW.state<>'resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL)
       -- Decisions belong to the review: once it is released (gate off with a
       -- release stamped), nothing is decided until a later page (#757).
       OR EXISTS(SELECT 1 FROM stewardship_system_configuration released
           WHERE NOT released.restore_review_required AND released.restore_released_at IS NOT NULL)
       -- Another (earlier restore's) hold still keeps this email back, so a
       -- resend would silently do nothing: settle that one first.
       OR (NEW.state='resend_authorized' AND EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold other
           WHERE other.id<>h.id AND other.definition_id=h.definition_id AND other.mode=h.mode
             AND other.target=h.target AND other.slot=h.slot AND other.state IN ('unreviewed','assumed_delivered'))) THEN
        RAISE EXCEPTION 'Invalid restore hold review or resend binding' USING ERRCODE='23514'; END IF;
    -- Settling a held email is an Administrator's decision made within five
    -- minutes of a Google sign-in (migration 0019, #537).
    IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
        RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END $$;

-- stewardship_runtime_guard_v1, from functions.sql: the restore fields move only with a restore transition.
CREATE OR REPLACE FUNCTION public.stewardship_runtime_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Runtime configuration cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.configuration_sequence<>1 OR NEW.active_configuration_id IS NOT NULL OR NEW.mode<>'testing' OR NEW.restore_review_required
           OR NEW.restore_id IS NOT NULL OR NEW.restore_backup_at IS NOT NULL OR NEW.restore_activated_at IS NOT NULL OR NEW.restore_released_at IS NOT NULL THEN
            RAISE EXCEPTION 'Runtime must start unconfigured in Testing' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS DISTINCT FROM OLD.active_configuration_id THEN
        IF NEW.configuration_sequence<>OLD.configuration_sequence+1
           OR NEW.mode<>OLD.mode OR NEW.restore_review_required<>OLD.restore_review_required
           OR NEW.restore_id IS DISTINCT FROM OLD.restore_id OR NEW.restore_backup_at IS DISTINCT FROM OLD.restore_backup_at
           OR NEW.restore_activated_at IS DISTINCT FROM OLD.restore_activated_at
           OR NEW.restore_released_at IS DISTINCT FROM OLD.restore_released_at
           OR NOT EXISTS (SELECT 1 FROM stewardship_config_activation
               WHERE configuration_id=NEW.active_configuration_id AND predecessor_id IS NOT DISTINCT FROM OLD.active_configuration_id
                 AND sequence=OLD.configuration_sequence AND actor_id IS NOT DISTINCT FROM NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Runtime pointer requires exact activation evidence' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.configuration_sequence<>OLD.configuration_sequence OR NOT EXISTS (
            SELECT 1 FROM stewardship_runtime_transition t
            WHERE t.expected_version=OLD.version AND t.before_mode=OLD.mode AND t.after_mode=NEW.mode
              AND t.before_campaign_id IS NOT DISTINCT FROM OLD.current_campaign_id
              AND t.after_campaign_id IS NOT DISTINCT FROM NEW.current_campaign_id
              AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
              -- The restore review fields move only with their own transition
              -- (migration 0019, #537): a start sets every one from it, a
              -- release clears only the gate and stamps the release.
              AND CASE t.action
                  WHEN 'restore_begin' THEN NEW.restore_review_required AND NEW.restore_id=t.restore_id
                      AND NEW.restore_backup_at=t.backup_at AND NEW.restore_activated_at IS NOT NULL
                      AND NEW.restore_released_at IS NULL
                  WHEN 'restore_release' THEN OLD.restore_review_required AND NOT NEW.restore_review_required
                      AND NEW.restore_id IS NOT DISTINCT FROM OLD.restore_id
                      AND NEW.restore_backup_at IS NOT DISTINCT FROM OLD.restore_backup_at
                      AND NEW.restore_activated_at IS NOT DISTINCT FROM OLD.restore_activated_at
                      AND NEW.restore_released_at IS NOT NULL
                  ELSE NEW.restore_review_required=OLD.restore_review_required
                      AND NEW.restore_id IS NOT DISTINCT FROM OLD.restore_id
                      AND NEW.restore_backup_at IS NOT DISTINCT FROM OLD.restore_backup_at
                      AND NEW.restore_activated_at IS NOT DISTINCT FROM OLD.restore_activated_at
                      AND NEW.restore_released_at IS NOT DISTINCT FROM OLD.restore_released_at END
        ) THEN RAISE EXCEPTION 'Runtime mutation requires transition evidence' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_runtime_transition_effect_v1, from functions.sql: a restore transition sets or clears the gate.
CREATE OR REPLACE FUNCTION public.stewardship_runtime_transition_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
-- A restore review's start and release have their own audit names.
DECLARE kind text := CASE NEW.action WHEN 'restore_begin' THEN 'restore_review_started'
    WHEN 'restore_release' THEN 'restore_review_released' ELSE 'runtime_transition' END;
BEGIN
    UPDATE stewardship_system_configuration SET mode=NEW.after_mode, current_campaign_id=NEW.after_campaign_id,
        -- A restore review's start and release (migration 0019, #537).
        restore_review_required=CASE NEW.action WHEN 'restore_begin' THEN true
            WHEN 'restore_release' THEN false ELSE restore_review_required END,
        restore_id=CASE WHEN NEW.action='restore_begin' THEN NEW.restore_id ELSE restore_id END,
        restore_backup_at=CASE WHEN NEW.action='restore_begin' THEN NEW.backup_at ELSE restore_backup_at END,
        restore_activated_at=CASE WHEN NEW.action='restore_begin' THEN stewardship_campaign_now_v1()
            ELSE restore_activated_at END,
        restore_released_at=CASE NEW.action WHEN 'restore_begin' THEN NULL
            WHEN 'restore_release' THEN stewardship_campaign_now_v1() ELSE restore_released_at END,
        version=version+1, actor_id=NEW.actor_id, correlation_id=NEW.correlation_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,kind,NEW.id,NEW.before_campaign_id);
    RETURN NEW;
END $$;

-- stewardship_runtime_transition_v1, from functions.sql: admit restore_begin and restore_release.
CREATE OR REPLACE FUNCTION public.stewardship_runtime_transition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    IF r.version<>NEW.expected_version OR r.mode<>NEW.before_mode OR r.current_campaign_id IS DISTINCT FROM NEW.before_campaign_id THEN
        RAISE EXCEPTION 'Runtime transition is stale' USING ERRCODE='23514'; END IF;
    -- The recovery login only starts a restore review (migration 0019, #537).
    IF current_user='pk_stewardship_admin_recovery' AND NEW.action<>'restore_begin' THEN
        RAISE EXCEPTION 'The recovery login only starts a restore review' USING ERRCODE='23514'; END IF;
    IF NEW.action='return_testing' THEN
        IF NEW.actor_id IS NULL OR NEW.after_mode<>'testing' OR NEW.after_campaign_id IS NOT NULL
           OR r.restore_review_required OR NOT stewardship_campaign_quiet_v1(r.current_campaign_id)
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=r.current_campaign_id AND state='archived')
           OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'Return to Testing requires current archived quiescence' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='campaign' THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.campaign_transition_id
            AND t.expected_runtime_version=r.version AND t.before_mode=NEW.before_mode AND t.after_mode=NEW.after_mode
            AND t.campaign_id=NEW.before_campaign_id AND NEW.after_campaign_id=NEW.before_campaign_id
            AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Mode transition requires campaign evidence' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='restore_begin' THEN
        -- The operator's restore command (admin-recovery login), run after a
        -- restore and before web starts (migration 0019, #537). It changes
        -- no mode, campaign or Family credential: it only closes the site
        -- for review. A second restore starts a new review.
        IF NOT (pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
                OR current_user='pk_stewardship_admin_recovery')
           OR r.active_configuration_id IS NULL
           OR NEW.after_mode<>NEW.before_mode OR NEW.after_campaign_id IS DISTINCT FROM NEW.before_campaign_id
           OR NEW.restore_id IS NULL OR NEW.restore_id IS NOT DISTINCT FROM r.restore_id
           OR NEW.backup_at IS NULL OR NEW.backup_at>stewardship_campaign_now_v1() OR btrim(NEW.reason)=''
           OR NEW.campaign_transition_id IS NOT NULL OR NEW.session_id IS NOT NULL OR NEW.authenticated_at IS NOT NULL THEN
            RAISE EXCEPTION 'A restore review starts only from the operator restore command' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='restore_release' THEN
        -- A freshly signed-in Administrator releases the site after review.
        -- Refused while any email still needs a hold: the list must be found
        -- first, during the review, so every hold can still be decided (none
        -- is decided after release). Mode and campaign stay as restored.
        IF NOT r.restore_review_required OR NEW.restore_id IS DISTINCT FROM r.restore_id
           OR NEW.backup_at IS DISTINCT FROM r.restore_backup_at
           OR NEW.after_mode<>NEW.before_mode OR NEW.after_campaign_id IS DISTINCT FROM NEW.before_campaign_id
           OR NEW.actor_id IS NULL OR NEW.campaign_transition_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=r.current_campaign_id
               AND state IN ('purging','purge_cleanup_failed'))
           OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'This restore review cannot be released' USING ERRCODE='23514'; END IF;
        IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
            RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_restore_hold_candidates_v1()) THEN
            RAISE EXCEPTION 'The held-email list is stale: find the held emails again before release'
                USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported runtime transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- stewardship_system_configuration_mutable_v1, from functions.sql: the restore fields leave the frozen list; the runtime guard owns them.
CREATE OR REPLACE FUNCTION public.stewardship_system_configuration_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR false /* initial setup recipient has its own guard */ THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
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

-- Refuse to commit unless the columns, both new functions (invoker's rights,
-- fixed search_path) and every replaced body are installed as declared.
DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_attribute
        WHERE attrelid IN ('public.stewardship_runtime_transition'::regclass,
                           'public.stewardship_restore_hold_resolution'::regclass)
          AND attname IN ('session_id','authenticated_at') AND NOT attisdropped)<>4 THEN
        RAISE EXCEPTION 'The restore sign-in columns were not added';
    END IF;
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.oid IN ('public.stewardship_restore_decision_admitted_v1(uuid,uuid,timestamptz)'::regprocedure,
                        'public.stewardship_restore_hold_inventory_v1(uuid,uuid)'::regprocedure,
                        'public.stewardship_restore_hold_candidates_v1()'::regprocedure)
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>3
       OR (SELECT provolatile FROM pg_proc
           WHERE oid='public.stewardship_restore_decision_admitted_v1(uuid,uuid,timestamptz)'::regprocedure)<>'v' THEN
        RAISE EXCEPTION 'The restore review functions have the wrong security or search_path';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_hold_inventory_v1(uuid,uuid)'::regprocedure
        AND prosrc LIKE '%restore_inventory%' AND prosrc LIKE '%ON CONFLICT ON CONSTRAINT restore_hold_semantic DO NOTHING%'
        AND prosrc LIKE '%stewardship_restore_hold_candidates_v1()%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_hold_candidates_v1()'::regprocedure
        AND prosrc LIKE '%stewardship_schedule_slot_excluded_v1(slot.definition_id%'
        AND prosrc LIKE '%o.revision_id=slot.revision_id%' AND prosrc LIKE '%v.due_at<=r.restore_activated_at%'
        AND prosrc LIKE '%family_ineligible%') THEN
        RAISE EXCEPTION 'The held-email inventory is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_runtime_transition_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%restore_begin%' AND prosrc LIKE '%stewardship_restore_hold_candidates_v1()%'
        AND prosrc LIKE '%The recovery login only starts a restore review%') THEN
        RAISE EXCEPTION 'The runtime transition guard does not admit the restore transitions';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_runtime_transition_effect_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%restore_review_released%' AND prosrc LIKE '%restore_released_at=CASE%') THEN
        RAISE EXCEPTION 'The runtime transition effect does not move the restore fields';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_runtime_guard_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%WHEN ''restore_release'' THEN OLD.restore_review_required%'
        AND prosrc LIKE '%NEW.restore_released_at IS DISTINCT FROM OLD.restore_released_at%') THEN
        RAISE EXCEPTION 'The runtime guard does not protect the restore fields';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_system_configuration_mutable_v1()'::regprocedure
        AND (prosecdef OR prosrc LIKE '%restore_review_required%' OR prosrc NOT LIKE '%initial setup recipient has its own guard%')) THEN
        RAISE EXCEPTION 'The runtime row still freezes the restore fields';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at)%'
        AND prosrc LIKE '%NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS%'
        AND prosrc LIKE '%other.id<>h.id%' AND prosrc LIKE '%h.state<>''unreviewed''%'
        AND prosrc LIKE '%released.restore_released_at IS NOT NULL%'
        AND prosrc NOT LIKE '%''not_applicable''%') THEN
        RAISE EXCEPTION 'The held-email decision guard is not the one this file installs';
    END IF;
END
$check$;
