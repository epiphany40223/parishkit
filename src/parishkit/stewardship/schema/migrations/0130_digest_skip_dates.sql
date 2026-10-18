-- Frozen forward migration file 0130 (the repository-wide file sequence; a
-- provisional number until it merges): skip one send of a Daily or Weekly
-- Admin digest (#1658). This file is installed by its Django migration in
-- the campaigns app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copies of the baseline functions equal the fresh-install
-- baseline's. A fresh install runs the baseline, the earlier files and then
-- this; the baseline already carries the replaced bodies, so the install ends
-- in the same catalog as an upgraded database.
--
-- A digest schedule's revision values may now carry 'skip_dates', the slot
-- dates it never sends (Edit scheduled email's Delete, "This email only").
-- The application's evaluator (SchedulePlan) leaves those slots out of every
-- page; this file makes the database agree, so no writer can create an
-- occurrence for a skipped send and no page or catch-up proof requires one.
--
-- Replaced in place, each copied whole from its newest definition:
--   stewardship_occurrence_guard_v1   (0005; the baseline carries it) refuses
--       to insert a Daily or Weekly digest occurrence whose slot is in its
--       revision's skip_dates, whichever writer plans it (the scheduler's
--       digest planner, a preparation, catch-up or recovery).
--   stewardship_catchup_due_v1        (the baseline) the canonical slot check
--       every digest preparation insert, page proof, catch-up proof and
--       paused-delivery recovery reads. It now answers NULL (no slot) for a
--       date in the current revision's skip_dates.
--   stewardship_weekly_digest_page_v1 (the baseline) the weekly page proof,
--       which treated every NULL slot in a page's week series as a malformed
--       page. A skipped week is now accepted without an occurrence; any other
--       NULL is still refused. The daily page proof already ignores NULL
--       slots, so it is unchanged.
-- Values without 'skip_dates' (every schedule saved before this file) mean
-- exactly what they meant before, so a release from before this file (a
-- rollback) reads every existing revision unchanged.
--
-- The occurrence guard is SECURITY DEFINER in the baseline (an ALTER
-- FUNCTION in schedule_reconciliation.sql, repeated by 0005), which CREATE
-- OR REPLACE resets, so this file repeats that ALTER after replacing it. The
-- other two are not SECURITY DEFINER, before or after. CREATE OR REPLACE
-- keeps every function's grants, owner and settings (search_path). The DO
-- block at the end refuses to commit unless all three new bodies are
-- installed exactly (each body's md5, so no weakened clause or misplaced
-- line passes) with their attributes and arguments, the guard is a definer
-- again, and the grants, owner and settings are unchanged. Reversing needs
-- its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The three functions' grants, settings and owner before the replacement
-- (with how many definitions there are), compared in the DO block. Settings
-- are compared in sorted order: an installed database may hold them in the
-- order of a later ALTER FUNCTION ... SET. A transaction-local setting holds
-- them, not a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0130_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'/'||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_occurrence_guard_v1',
        'stewardship_catchup_due_v1','stewardship_weekly_digest_page_v1')), true);

-- stewardship_occurrence_guard_v1: copied from its newest definition (0005);
-- only the skipped-send refusal on insert is new.
CREATE OR REPLACE FUNCTION public.stewardship_occurrence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
    digest_completed boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('occurrences',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514';
    END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' OR NEW.state IN ('pending','running'))
       AND NEW.production_cycle IS DISTINCT FROM (CASE WHEN NEW.mode='production' THEN c.production_cycle ELSE 0 END) THEN
        RAISE EXCEPTION 'Occurrence belongs to a retired Production cycle' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('succeeded','failed','skipped','coalesced') AND EXISTS (
        SELECT 1 FROM stewardship_outbox_message m WHERE m.id=NEW.outbox_id
          AND public.stewardship_occurrence_delivery_conflict_v1(NEW.state,m.state)
    ) THEN RAISE EXCEPTION 'Occurrence outcome contradicts terminal delivery'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NOT public.stewardship_initial_recovery_valid_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Initial recovery requires a new deliverability transition'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed'
                       AND (d.kind IN ('daily_digest','weekly_digest')
                           OR (NEW.due_at>=p.starts_at AND NEW.due_at<p.ends_at)))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
               AND NOT public.stewardship_catchup_materializing_v1(c.id,NEW.actor_id,NEW.correlation_id))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
           OR EXISTS (SELECT 1 FROM stewardship_postclose_resolution resolved
               JOIN public.stewardship_postclose_current covered ON covered.id=resolved.id
               WHERE resolved.campaign_id=c.id AND resolved.mode=NEW.mode
                   AND resolved.obligation_key='schedule:'||d.id::text||':'||NEW.slot)
           -- A digest's skipped send (#1658) is never planned, by any writer.
           OR (d.kind IN ('daily_digest','weekly_digest') AND EXISTS (
               SELECT 1 FROM stewardship_schedule_revision skipping WHERE skipping.id=NEW.revision_id
                   AND coalesce(skipping.values->'skip_dates' ? NEW.slot,false))) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF session_user='pk_stewardship_scheduler' AND (
            OLD.state<>'pending' OR NEW.state NOT IN ('skipped','coalesced')
            OR OLD.task_id IS NOT NULL OR OLD.outbox_id IS NOT NULL
            OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
            OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode
            OR r.restore_review_required
            OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate
                WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
            OR (NEW.state='coalesced' AND NOT EXISTS(
                SELECT 1 FROM stewardship_schedule_occurrence selected
                WHERE selected.id=NEW.replacement_id AND selected.target=NEW.target
                  AND selected.mode=NEW.mode AND selected.state='pending'
                  AND selected.task_id IS NULL AND selected.outbox_id IS NULL
            ))
        ) THEN RAISE EXCEPTION 'Scheduler may only reconcile unallocated pending work'
            USING ERRCODE='23514'; END IF;
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            -- Branch before planning private report reads: SQL boolean order is
            -- not a privilege boundary for unrelated Family/daily mail owners.
            IF d.kind='weekly_digest' THEN
                digest_completed:=public.stewardship_weekly_digest_completion_v1(to_jsonb(NEW));
            ELSE
                digest_completed:=public.stewardship_daily_digest_completion_v1(to_jsonb(NEW));
            END IF;
        END IF;
        IF NEW.state='running' AND NOT digest_completed THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR NOT ((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM NEW.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=NEW.id AND q.mode=NEW.mode))
                   OR (t.task_type='outbox_delivery' AND EXISTS (
                       SELECT 1 FROM public.stewardship_outbox_message mail
                       WHERE mail.id=t.domain_request_id AND mail.task_id=t.root_id
                         AND mail.id=NEW.outbox_id AND mail.semantic_key=NEW.id
                         AND mail.mode=NEW.mode AND mail.family_id::text=substring(NEW.target FROM 8))))
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind IN ('daily_digest','weekly_digest'))))
               -- Not yet due. Only Family mail preparation of a Production
               -- occurrence may claim it early, within the two-hour lead
               -- window (BG-12, #447). Every other claim, including a
               -- delivery claim, and the dispatch guard still wait for it.
               OR (NEW.due_at>instant AND NOT (t.task_type='family_mail_prepare'
                   AND NEW.mode='production' AND NEW.due_at<=instant+interval '2 hours'))
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT (
            (NEW.state='skipped' OR (NEW.state='coalesced' AND NEW.reason IN (
                'missed_family_recovery','missed_daily_recovery','missed_weekly_recovery')))
            AND public.stewardship_schedule_effect_v1(
                OLD.id,OLD.version,NEW.actor_id,NEW.correlation_id,NEW.reason)
        ) AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR (NOT public.stewardship_delivery_resolution_edge_v1(to_jsonb(OLD),to_jsonb(NEW))
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed')))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $_$;
ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER;

-- stewardship_catchup_due_v1: copied from the baseline; only the skip_dates
-- check is new.
CREATE OR REPLACE FUNCTION public.stewardship_catchup_due_v1(definition uuid,slot text)
RETURNS timestamptz LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE d public.stewardship_schedule_definition%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE; day date;
BEGIN
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=$1;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=d.current_revision_id;
    SELECT configuration.* INTO p FROM public.stewardship_campaign c
        JOIN public.stewardship_campaign_configuration configuration ON configuration.id=c.active_configuration_id
        WHERE c.id=d.campaign_id;
    IF v.id IS NULL OR p.id IS NULL THEN RETURN NULL; END IF;
    IF d.kind IN ('initial','reminder') THEN
        RETURN CASE WHEN slot='once' THEN v.due_at END;
    END IF;
    IF slot !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN NULL; END IF;
    day:=slot::date;
    IF day<p.start_date OR day>p.end_date OR day::text<>slot THEN RETURN NULL; END IF;
    -- A digest's skipped send (#1658) is no slot: nothing may plan it.
    IF coalesce(v.values->'skip_dates' ? slot,false) THEN RETURN NULL; END IF;
    IF d.kind='weekly_digest' THEN
        IF extract(isodow FROM day)::integer-1<>(v.values->>'weekday')::integer THEN RETURN NULL; END IF;
    ELSIF d.kind='daily_digest' THEN
        IF public.stewardship_resolve_local_v1(day::timestamp,p.timezone)
            =public.stewardship_resolve_local_v1((day+1)::timestamp,p.timezone) THEN RETURN NULL; END IF;
        day:=day+1;
    ELSE RETURN NULL;
    END IF;
    RETURN public.stewardship_resolve_local_v1(day+(v.values->>'time')::time,p.timezone);
END $$;

-- stewardship_weekly_digest_page_v1: copied from the baseline; only the
-- skipped-week case of the page series check is new.
CREATE OR REPLACE FUNCTION public.stewardship_weekly_digest_page_v1(prior jsonb, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_campaign_configuration%ROWTYPE;
        v stewardship_schedule_revision%ROWTYPE; first_day date; last_day date; next_day date;
BEGIN
    IF prior->>'phase'<>'dates' OR proposed->>'phase'='cancelled' THEN RETURN true; END IF;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=(proposed->>'campaign_configuration_id')::uuid;
    SELECT * INTO v FROM stewardship_schedule_revision WHERE id=(proposed->>'revision_id')::uuid;
    first_day:=coalesce((prior->>'cursor')::date+7,
        p.start_date+(((v.values->>'weekday')::int-extract(isodow FROM p.start_date)::int+8)%7));
    last_day:=(proposed->>'cursor')::date;
    IF proposed->>'cursor' IS DISTINCT FROM prior->>'cursor' THEN
        IF last_day IS NULL OR last_day<first_day OR last_day>first_day+99*7
          OR last_day>p.end_date OR (last_day-first_day)%7<>0 THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM generate_series(first_day::timestamp,last_day::timestamp,interval '7 days') day
            CROSS JOIN LATERAL (SELECT stewardship_catchup_due_v1(v.record_id,day::date::text) AS due) expected
            -- A skipped week (#1658) has no slot and needs no occurrence.
            WHERE (expected.due IS NULL AND NOT coalesce(v.values->'skip_dates' ? day::date::text,false))
              OR expected.due>(proposed->>'cutoff')::timestamptz
              OR (expected.due IS NOT NULL
                AND NOT stewardship_schedule_slot_excluded_v1(v.record_id,proposed->>'mode','admins',day::date::text)
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode=proposed->>'mode' AND o.target='admins'
                      AND o.slot=day::date::text AND o.due_at=expected.due))) THEN RETURN false; END IF;
    END IF;
    IF proposed->>'phase'='cover' THEN
        next_day:=coalesce(last_day+7,first_day);
        RETURN next_day>p.end_date OR stewardship_resolve_local_v1(
            next_day+(v.values->>'time')::time,p.timezone)>(proposed->>'cutoff')::timestamptz;
    END IF;
    RETURN true;
END $$;

DO $check$
BEGIN
    -- The occurrence guard: a definer again, plpgsql, a trigger function,
    -- with exactly this file's body (its md5; the LIKE spells out the
    -- skipped-send refusal on insert for a reader).
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_occurrence_guard_v1'
          AND p.prosecdef AND p.provolatile='v'
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.prorettype='trigger'::regtype
          AND pg_get_function_arguments(p.oid)=''
          AND md5(p.prosrc)='669e74dff167d9deab8c081677e6855a'
          AND p.prosrc LIKE '%AND coalesce(skipping.values->''skip_dates'' ? NEW.slot,false))) THEN%')<>1 THEN
        RAISE EXCEPTION 'Migration 0130 (digest skip dates) did not replace the occurrence guard as a definer';
    END IF;
    -- The slot check: invoker, stable, plpgsql, with its arguments and
    -- exactly this file's body (its md5; the LIKE spells out the skip_dates
    -- refusal before any slot is resolved).
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_catchup_due_v1'
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.prorettype='timestamptz'::regtype
          AND pg_get_function_arguments(p.oid)='definition uuid, slot text'
          AND md5(p.prosrc)='ec0c597468418d43ffcbc8d9aa351097'
          AND p.prosrc LIKE '%IF coalesce(v.values->''skip_dates'' ? slot,false) THEN RETURN NULL; END IF;%')<>1 THEN
        RAISE EXCEPTION 'Migration 0130 (digest skip dates) did not replace the slot check';
    END IF;
    -- The weekly page proof: invoker, stable, plpgsql, with its arguments
    -- and exactly this file's body (its md5; the LIKEs spell out the
    -- skipped-week case).
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_weekly_digest_page_v1'
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.prorettype='boolean'::regtype
          AND pg_get_function_arguments(p.oid)='prior jsonb, proposed jsonb'
          AND md5(p.prosrc)='076bc3352d235f7ba90da01a26ea705d'
          AND p.prosrc LIKE '%WHERE (expected.due IS NULL AND NOT coalesce(v.values->''skip_dates'' ? day::date::text,false))%'
          AND p.prosrc LIKE '%OR (expected.due IS NOT NULL%')<>1 THEN
        RAISE EXCEPTION 'Migration 0130 (digest skip dates) did not replace the weekly page proof';
    END IF;
    -- CREATE OR REPLACE keeps the grants, owner and settings; check that
    -- nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'/'||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
                ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_occurrence_guard_v1',
            'stewardship_catchup_due_v1','stewardship_weekly_digest_page_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0130_acl') THEN
        RAISE EXCEPTION 'Migration 0130 changed the grants, owner or settings of a replaced function';
    END IF;
    -- An unknown schedule still has no slot, as before.
    IF public.stewardship_catchup_due_v1('00000000-0000-0000-0000-000000000000','2054-10-01') IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0130 slot check answers a slot for an unknown schedule';
    END IF;
END
$check$;
