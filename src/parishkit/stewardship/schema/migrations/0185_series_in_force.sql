-- Frozen forward migration file 0185 (the repository-wide file sequence,
-- assigned by the coordinator): the participation facts count the pledge
-- values in force (#2202; reports spec, "Pledge values in the
-- participation facts"). This file is installed by its Django migration in
-- the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- The Administrator chose option B (2026-10-26): the day-by-day pledge
-- figures (the Response dashboard's Effective annual pledges line and daily
-- table, the campaign comparisons and the campaign archive export) count a
-- reviewer's pledge Edit in force on every day from the response it
-- reviewed onward, so they agree with Financial stewardship (0182) and the
-- campaign statistics (0184). A fact generation's exact inputs had no place
-- for an Edit, so this file:
--   - adds review_watermark, the campaign's pledge review row count when
--     the inputs were taken, to the fact set (and to its unique exact-input
--     key daily_fact_exact_inputs), to the rebuild demand
--     (requested_review_watermark), to the exact export request and to the
--     report email preparation (NULL until its inputs are recorded, and on
--     every send recorded before this file, which pinned a generation of
--     watermark 0). Review rows are never changed or removed (0127's
--     guard), so the count only grows, and every review action advances it;
--   - adds pledge_edits to the fact set: each Family's Edit in force when
--     the generation is allocated, written by the database on INSERT
--     (stewardship_fact_pledge_edits_record_v1, a trigger) and immutable like
--     the rest of the generation's identity, so verification recalculates
--     exactly what was built however later reviews change. They hold at
--     least the reviews the watermark counts (a review between a request
--     and its build is in them too, and its newer watermark makes the next
--     generation); a watermark ahead of the campaign's is refused;
--   - creates two narrow SECURITY DEFINER functions, since the worker and
--     the scheduler read no pledge reviews and no answers:
--     stewardship_pledge_review_watermark_v1(campaign), the count only (web,
--     worker and scheduler), and stewardship_fact_pledge_edits_v1(campaign),
--     the Edits as [{family_id, from_sequence, annual_pledge, frequency}]
--     (worker only). The latter reads 0184's stewardship_pledge_edits_in_force_v1
--     (0182's selection of the Edit in force, which places Pledges' stages)
--     and the Family's latest Edit row for the response it reviewed: rows
--     after an Edit only carry its decision (Entered by hand, the undo of a
--     terminal step), and while it is in force every later response repeats
--     the reviewed answer, so the Edit applies from that response on. Each
--     has a fixed search_path, refuses any other session login not a
--     member of the schema owner, and has no EXECUTE for PUBLIC;
--     database-grants grants it (runtime_grants.runtime_functions);
--   - replaces, each body copied whole with the watermark added to every
--     exact-input comparison: the fact set's immutable identity
--     (stewardship_daily_fact_set_mutable_v1), the rebuild demand guard (the
--     watermark never regresses and a claim freezes it), the pointer guard
--     and stewardship_fact_disposable (a newer generation needs a watermark
--     at least as high), the runtime binding of a generation to its demand,
--     exact export or report email, stewardship_exact_matches_v1 and
--     stewardship_exact_claimable_v1, the exact export request guard and
--     the report email preparation guard (the watermark is the campaign's
--     current one when the inputs are recorded, as the submission watermark
--     is) and the report email snapshot guard;
--   - replaces stewardship_campaign_comparison_v1 (0095's body, copied
--     whole) so day N's annual pledges, pledging Families, quartiles and
--     bands count the generation's own Edits, and equal the fact row they
--     are checked against.
-- The retired daily digest's guards, the weekly digest, delivery
-- close-out and the compaction evidence are unchanged: they hold no
-- pledge figure. Imported archives and retained captures keep what they
-- recorded.
--
-- Existing generations, demand and requests read watermark 0 and no Edits,
-- exactly what they were built from, so their verification stays clean.
-- After the upgrade the scheduler finds the current watermark above the
-- demand's (when the campaign has any pledge review) and builds one new
-- generation per population scope. No row other than those columns'
-- defaults changes; no Family code, link, session or mail is touched. The
-- jsonb operators of the new and replaced bodies are written
-- OPERATOR(pg_catalog.x) (#2166) where they occur. Only
-- stewardship_campaign_comparison_v1 among the replaced functions is a
-- SECURITY DEFINER, so its ALTER is repeated after the replacement.
-- The DO block at the end refuses to commit unless the columns, key,
-- check, functions, trigger, settings and owners are as declared, the
-- replaced functions keep the grants and settings they had, and the new
-- definers hold no grant yet. No temporary objects.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0185_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_daily_fact_set_mutable_v1',
        'stewardship_fact_demand_guard','stewardship_fact_reference_guard',
        'stewardship_fact_disposable','stewardship_fact_runtime_binding_v1',
        'stewardship_exact_matches_v1','stewardship_exact_claimable_v1',
        'stewardship_exact_request_guard_v1','stewardship_report_email_preparation_guard_v1',
        'stewardship_report_email_snapshot_guard_v1','stewardship_campaign_comparison_v1')), true);

-- The columns and keys. Each statement may run again over its own result
-- (IF NOT EXISTS, and the key and check are dropped first), as the users
-- swap's tests install a closure file's routines again (#952).
ALTER TABLE "stewardship_daily_fact_set" DROP CONSTRAINT "daily_fact_exact_inputs";
ALTER TABLE "stewardship_daily_fact_set" ADD COLUMN IF NOT EXISTS "pledge_edits" jsonb DEFAULT '[]'::jsonb NOT NULL;
ALTER TABLE "stewardship_daily_fact_set" ALTER COLUMN "pledge_edits" DROP DEFAULT;
ALTER TABLE "stewardship_daily_fact_set" ADD COLUMN IF NOT EXISTS "review_watermark" bigint DEFAULT 0 NOT NULL CHECK ("review_watermark" >= 0);
ALTER TABLE "stewardship_daily_fact_set" ALTER COLUMN "review_watermark" DROP DEFAULT;
ALTER TABLE "stewardship_fact_demand" ADD COLUMN IF NOT EXISTS "requested_review_watermark" bigint DEFAULT 0 NOT NULL CHECK ("requested_review_watermark" >= 0);
ALTER TABLE "stewardship_fact_demand" ALTER COLUMN "requested_review_watermark" DROP DEFAULT;
ALTER TABLE "stewardship_exact_export_request" ADD COLUMN IF NOT EXISTS "review_watermark" bigint DEFAULT 0 NOT NULL CHECK ("review_watermark" >= 0);
ALTER TABLE "stewardship_exact_export_request" ALTER COLUMN "review_watermark" DROP DEFAULT;
ALTER TABLE "stewardship_report_email_preparation" ADD COLUMN IF NOT EXISTS "review_watermark" bigint NULL CHECK ("review_watermark" >= 0);
ALTER TABLE "stewardship_daily_fact_set" ADD CONSTRAINT "daily_fact_exact_inputs" UNIQUE ("campaign_id", "population_scope", "source_generation", "submission_watermark", "review_watermark", "timezone_configuration_id", "through_date");
ALTER TABLE "stewardship_report_email_preparation" DROP CONSTRAINT IF EXISTS "report_email_review_inputs";
ALTER TABLE "stewardship_report_email_preparation" ADD CONSTRAINT "report_email_review_inputs" CHECK (("review_watermark" IS NULL OR "submission_watermark" IS NOT NULL));

CREATE FUNCTION public.stewardship_pledge_review_watermark_v1(campaign_uuid uuid)
RETURNS bigint
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user NOT IN ('pk_stewardship_web','pk_stewardship_worker','pk_stewardship_scheduler')
       AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web, worker and scheduler logins read the pledge review watermark'
            USING ERRCODE='42501';
    END IF;
    -- Every pledge review row of the campaign's Families: rows are never
    -- changed or removed, so the count only grows (#2202).
    RETURN (SELECT count(*) FROM public.stewardship_pledge_review r
        JOIN public.stewardship_family_campaign f ON f.id=r.family_id
        WHERE f.campaign_id=campaign_uuid);
END $$;
ALTER FUNCTION public.stewardship_pledge_review_watermark_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_pledge_review_watermark_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_fact_pledge_edits_v1(campaign_uuid uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE result jsonb; missing boolean;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the worker login reads the pledge Edits of a fact generation'
            USING ERRCODE='42501';
    END IF;
    -- Each Family's Edit in force (0184's definer: 0182's selection), from
    -- the response its latest Edit row reviewed. A later row only carries
    -- that decision with its values, and while the Edit is in force every
    -- later response repeats the reviewed answer (0181's rule), so the Edit
    -- applies from that response on. An edited zero pledge has no
    -- frequency, as the reviewed Family form shows it. An Edit in force
    -- with no Edit row behind it cannot occur; it is refused rather than
    -- left out, so these Edits never silently differ from 0184's.
    SELECT coalesce(jsonb_agg(jsonb_build_object(
            'family_id',e.family_id,
            'from_sequence',d.campaign_sequence,
            'annual_pledge',e.annual_pledge::numeric(14,2)::text,
            'frequency',CASE WHEN e.annual_pledge>0 THEN v.reviewed_frequency END)
            ORDER BY e.family_id),'[]'::jsonb),
        bool_or(d.id IS NULL)
        INTO result, missing
        FROM public.stewardship_pledge_edits_in_force_v1(campaign_uuid) e
        LEFT JOIN LATERAL (SELECT r.submission_id,r.reviewed_frequency
            FROM public.stewardship_pledge_review r
            WHERE r.family_id=e.family_id AND r.action='edit'
            ORDER BY r.sequence DESC LIMIT 1) v ON true
        LEFT JOIN public.stewardship_submission d ON d.id=v.submission_id;
    IF coalesce(missing,false) THEN
        RAISE EXCEPTION 'A pledge Edit in force has no Edit row' USING ERRCODE='23514';
    END IF;
    RETURN result;
END $$;
ALTER FUNCTION public.stewardship_fact_pledge_edits_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_fact_pledge_edits_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_fact_pledge_edits_record_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- A new generation records the Edits in force as it is allocated
    -- (#2202), whatever the INSERT named; the identity guard keeps them.
    -- They hold at least the reviews its watermark counts (rows only grow),
    -- so a watermark ahead of the campaign's is refused.
    IF NEW.review_watermark>public.stewardship_pledge_review_watermark_v1(NEW.campaign_id) THEN
        RAISE EXCEPTION 'Fact review watermark is ahead of the campaign''s' USING ERRCODE='23514';
    END IF;
    NEW.pledge_edits:=public.stewardship_fact_pledge_edits_v1(NEW.campaign_id);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_fact_pledge_edits_record_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_fact_pledge_edits BEFORE INSERT ON public.stewardship_daily_fact_set
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_pledge_edits_record_v1();

CREATE OR REPLACE FUNCTION public.stewardship_daily_fact_set_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."population_scope" IS DISTINCT FROM OLD."population_scope" OR NEW."source_id" IS DISTINCT FROM OLD."source_id" OR NEW."source_generation" IS DISTINCT FROM OLD."source_generation" OR NEW."submission_watermark" IS DISTINCT FROM OLD."submission_watermark" OR NEW."review_watermark" IS DISTINCT FROM OLD."review_watermark" OR NEW."pledge_edits" IS DISTINCT FROM OLD."pledge_edits" OR NEW."timezone_configuration_id" IS DISTINCT FROM OLD."timezone_configuration_id" OR NEW."through_date" IS DISTINCT FROM OLD."through_date" OR NEW."first_date" IS DISTINCT FROM OLD."first_date" OR NEW."last_date" IS DISTINCT FROM OLD."last_date" OR NEW."expected_count" IS DISTINCT FROM OLD."expected_count" THEN
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

CREATE OR REPLACE FUNCTION public.stewardship_fact_demand_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE requested_changed boolean;
        claim_changed boolean;
        target stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Fact demand requires an owning retention workflow'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_source_snapshot
        WHERE id=NEW.requested_source_id AND state='promoted'
            AND generation=NEW.requested_source_generation)
        OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration
            WHERE id=NEW.requested_timezone_configuration_id
                AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Fact demand inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    IF NEW.pending_due_at IS NOT NULL AND NEW.pending_due_at IS DISTINCT FROM
        LEAST(NEW.pending_last_at+interval '5 seconds',
              NEW.pending_first_at+interval '30 seconds') THEN
        RAISE EXCEPTION 'Fact demand debounce window is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.pending_revision<>1 OR NEW.pending_first_at IS NULL
            OR NEW.pending_first_at<>NEW.pending_last_at
            OR NEW.claimed_revision<>0 OR NEW.claimed_generation_id IS NOT NULL THEN
            RAISE EXCEPTION 'Fact demand must begin pending' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    requested_changed := ROW(NEW.requested_source_id,
        NEW.requested_source_generation,NEW.requested_submission_watermark,
        NEW.requested_review_watermark,
        NEW.requested_timezone_configuration_id,NEW.requested_through_date)
        IS DISTINCT FROM ROW(OLD.requested_source_id,
        OLD.requested_source_generation,OLD.requested_submission_watermark,
        OLD.requested_review_watermark,
        OLD.requested_timezone_configuration_id,OLD.requested_through_date);
    claim_changed := ROW(NEW.claimed_revision,NEW.claimed_generation_id,
        NEW.claimed_task_id,NEW.claimed_task_fence,NEW.claimed_worker_id)
        IS DISTINCT FROM ROW(OLD.claimed_revision,OLD.claimed_generation_id,
        OLD.claimed_task_id,OLD.claimed_task_fence,OLD.claimed_worker_id);
    IF requested_changed AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_campaign WHERE id=NEW.campaign_id
                AND active_configuration_id=NEW.requested_timezone_configuration_id
        ) THEN
            RAISE EXCEPTION 'Fact demand requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
    IF requested_changed THEN
        IF claim_changed OR NEW.pending_revision<>OLD.pending_revision+1
            OR NEW.requested_source_generation<OLD.requested_source_generation
            OR NEW.requested_submission_watermark<OLD.requested_submission_watermark
            -- The pledge review watermark (0185, #2202) never regresses either.
            OR NEW.requested_review_watermark<OLD.requested_review_watermark
            OR (NEW.requested_timezone_configuration_id=OLD.requested_timezone_configuration_id
                AND NEW.requested_through_date<OLD.requested_through_date)
            OR NEW.pending_first_at IS DISTINCT FROM
                COALESCE(OLD.pending_first_at,NEW.pending_last_at)
            OR NEW.pending_last_at IS NULL
            OR NEW.pending_last_at<OLD.pending_last_at THEN
            RAISE EXCEPTION 'Fact event must preserve independent claimed work'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.pending_revision<>OLD.pending_revision OR NOT claim_changed THEN
        RAISE EXCEPTION 'Fact demand change requires a new event or claim transition'
            USING ERRCODE='23514';
    ELSIF OLD.claimed_generation_id IS NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_revision<>OLD.pending_revision
            OR OLD.pending_due_at IS NULL OR OLD.pending_due_at>clock_timestamp()
            OR NEW.pending_first_at IS NOT NULL
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR ROW(target.campaign_id,target.population_scope,target.source_id,
                target.submission_watermark,target.review_watermark,
                target.timezone_configuration_id,
                target.through_date) IS DISTINCT FROM ROW(NEW.campaign_id,
                NEW.population_scope,NEW.requested_source_id,
                NEW.requested_submission_watermark,NEW.requested_review_watermark,
                NEW.requested_timezone_configuration_id,NEW.requested_through_date)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact claim must freeze exactly its due input window'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.claimed_generation_id IS NOT NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_generation_id<>OLD.claimed_generation_id
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact recovery must transfer only abandoned exact work'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.claimed_generation_id IS NOT NULL
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR NOT stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set
                WHERE id=OLD.claimed_generation_id AND state='ready') THEN
            RAISE EXCEPTION 'Fact completion must preserve newer pending demand'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_reference_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE target stewardship_daily_fact_set%ROWTYPE;
        prior stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    SELECT * INTO target FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF target.state IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'Fact reference requires a retained ready generation'
            USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_fact_pointer' THEN
        IF target.campaign_id<>NEW.campaign_id
            OR target.population_scope<>NEW.population_scope
            OR NOT EXISTS(SELECT 1 FROM stewardship_campaign
                WHERE id=NEW.campaign_id
                    AND active_configuration_id=target.timezone_configuration_id) THEN
            RAISE EXCEPTION 'Fact pointer requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' THEN
            SELECT * INTO prior FROM stewardship_daily_fact_set
                WHERE id=OLD.fact_set_id;
            IF target.source_generation<prior.source_generation
                OR target.submission_watermark<prior.submission_watermark
                OR target.review_watermark<prior.review_watermark
                OR (target.timezone_configuration_id=prior.timezone_configuration_id
                    AND target.through_date<prior.through_date) THEN
                RAISE EXCEPTION 'Fact pointer cannot regress' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_disposable(identifier uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_fact_set old
        JOIN stewardship_daily_fact_set newer ON newer.campaign_id=old.campaign_id
            AND newer.population_scope=old.population_scope AND newer.state='ready'
            AND newer.id<>old.id AND newer.created_at>=old.created_at
            AND newer.source_generation>=old.source_generation
            AND newer.submission_watermark>=old.submission_watermark
            -- A newer pledge review watermark too (0185, #2202).
            AND newer.review_watermark>=old.review_watermark
            AND ((newer.timezone_configuration_id=old.timezone_configuration_id
                    AND newer.through_date>=old.through_date)
                OR (newer.timezone_configuration_id<>old.timezone_configuration_id
                    AND EXISTS (SELECT 1 FROM public.stewardship_campaign c
                        WHERE c.id=newer.campaign_id
                            AND c.active_configuration_id=newer.timezone_configuration_id)))
        WHERE old.id=identifier AND old.state='ready')
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pointer WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.fact_set_id=identifier
            AND t.state IN ('queued','running','retry_wait','abandoned'))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.review_watermark,r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.review_watermark,f.timezone_configuration_id,f.through_date)
            -- A latest-data export that has ended keeps nothing (#1398).
            AND NOT EXISTS(SELECT 1 FROM stewardship_export_discard d
                WHERE d.exact_request_id=r.id))
    -- An export still linked to the generation keeps it; an ended export's
    -- discard clears the link (#1398), so only those go.
    AND NOT EXISTS(SELECT 1 FROM stewardship_export_request WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_report_email_snapshot r WHERE r.fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand
        WHERE claimed_generation_id=identifier);
$$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_runtime_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand stewardship_fact_demand%ROWTYPE;
        owned_task stewardship_task_run%ROWTYPE;
        exact_request record;
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN NEW; END IF;
    SELECT * INTO owned_task FROM stewardship_task_run WHERE id=NEW.task_id;
    -- A report email's capture (#1786) builds exactly its recorded inputs.
    IF owned_task.task_type='report_email_prepare' THEN
        SELECT p.* INTO exact_request FROM stewardship_report_email_preparation p
            WHERE p.id=owned_task.domain_request_id AND p.task_id=owned_task.root_id
              AND p.phase='capture';
        -- A send whose inputs were recorded before 0185 (#2202) has no
        -- pledge review watermark; it pinned a generation of watermark 0.
        IF NOT FOUND OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,
            NEW.submission_watermark,NEW.review_watermark,
            NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,'historical'::varchar,
                exact_request.source_id,exact_request.submission_watermark,
                coalesce(exact_request.review_watermark,0),
                exact_request.timezone_configuration_id,exact_request.through_date)
            OR NOT stewardship_report_email_live_v1(exact_request.id,
                NEW.task_id,NEW.task_fence,NEW.worker_id) THEN
            RAISE EXCEPTION 'Report builder differs from its recorded inputs' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run
            WHERE id=OLD.task_id AND root_id=owned_task.root_id)
            AND NOT EXISTS(SELECT 1 FROM stewardship_task_run prior
                WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
                  AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                    WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
                  AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
            RAISE EXCEPTION 'Report recovery cannot replace a recoverable root' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF owned_task.task_type='report_exact_export' THEN
        SELECT * INTO exact_request FROM stewardship_exact_export_request
            WHERE id=owned_task.domain_request_id AND task_id=owned_task.root_id;
        IF NOT FOUND OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,
            NEW.submission_watermark,NEW.review_watermark,
            NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,exact_request.population_scope,
                exact_request.source_id,exact_request.submission_watermark,
                exact_request.review_watermark,
                exact_request.timezone_configuration_id,exact_request.through_date)
            OR NOT stewardship_export_authorized_v1(exact_request.requester_id)
            OR NOT stewardship_export_admitted_v1(exact_request.campaign_id,true)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=exact_request.id)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE request_id=exact_request.id) THEN
            RAISE EXCEPTION 'Exact builder differs from its admitted frozen request' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run
            WHERE id=OLD.task_id AND root_id=owned_task.root_id)
            AND NOT EXISTS(SELECT 1 FROM stewardship_task_run prior
                WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
                  AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                    WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
                  AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
            RAISE EXCEPTION 'Exact recovery cannot replace a recoverable root' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO owned_task FROM stewardship_task_run
        WHERE id=NEW.task_id AND task_type='report_facts';
    SELECT * INTO demand FROM stewardship_fact_demand WHERE id=owned_task.domain_request_id;
    IF NOT FOUND OR demand.campaign_id<>NEW.campaign_id
        OR demand.population_scope<>NEW.population_scope THEN
        RAISE EXCEPTION 'Fact builder is not bound to this demand' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF demand.claimed_generation_id IS NOT NULL
            OR ROW(NEW.source_id,NEW.submission_watermark,NEW.review_watermark,
                NEW.timezone_configuration_id,NEW.through_date)
                IS DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                    demand.requested_review_watermark,
                    demand.requested_timezone_configuration_id,demand.requested_through_date)
            OR EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
                JOIN stewardship_task_run t ON t.id=f.task_id WHERE t.root_id=owned_task.root_id) THEN
            RAISE EXCEPTION 'Fact allocation differs from its unclaimed window' USING ERRCODE='23514';
        END IF;
    ELSIF demand.claimed_generation_id IS NULL
        AND demand.pending_due_at<=clock_timestamp()
        AND ROW(NEW.source_id,NEW.submission_watermark,NEW.review_watermark,
                NEW.timezone_configuration_id,NEW.through_date)
            IS NOT DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                demand.requested_review_watermark,
                demand.requested_timezone_configuration_id,demand.requested_through_date)
        AND EXISTS(SELECT 1 FROM stewardship_task_run prior
            WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
              AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
        -- Only terminal exact work may be adopted into a new ordinary window.
        -- The normal demand guard then freezes this exact key and live fence.
        RETURN NEW;
    ELSIF demand.claimed_generation_id IS DISTINCT FROM NEW.id
        OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=OLD.task_id AND root_id=owned_task.root_id) THEN
        RAISE EXCEPTION 'Fact mutation differs from its frozen root' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_matches_v1(request_uuid uuid, fact_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=fact_uuid
        WHERE r.id=request_uuid AND
          ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
              r.review_watermark,r.timezone_configuration_id,r.through_date)=
          ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
              f.review_watermark,f.timezone_configuration_id,f.through_date))
$$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_claimable_v1(request_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r WHERE r.id=request_uuid
      -- Root claiming commits before generation allocation. Serialize identical
      -- exact keys even during that gap, without assuming broker FIFO ordering.
      AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request other
        JOIN stewardship_task_run t ON t.root_id=other.task_id
        WHERE other.task_id<>r.task_id AND t.state='running'
          AND ROW(other.campaign_id,other.population_scope,other.source_id,
              other.submission_watermark,other.review_watermark,
              other.timezone_configuration_id,other.through_date)=
            ROW(r.campaign_id,r.population_scope,r.source_id,
              r.submission_watermark,r.review_watermark,
              r.timezone_configuration_id,r.through_date))
      -- Do not overtake an ordinary owner that has claimed but not frozen yet.
      AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand d
        JOIN stewardship_task_run t ON t.domain_request_id=d.id AND t.task_type='report_facts'
        WHERE d.campaign_id=r.campaign_id AND d.population_scope=r.population_scope
          AND t.state='running' AND d.claimed_generation_id IS NULL)
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
        JOIN stewardship_task_run owner ON owner.id=f.task_id
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.review_watermark,r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.review_watermark,f.timezone_configuration_id,f.through_date) AND f.state<>'ready'
          AND owner.root_id<>r.task_id
          AND EXISTS(SELECT 1 FROM stewardship_task_run live
            WHERE live.root_id=owner.root_id AND live.state IN ('queued','running','retry_wait','abandoned'))))
$$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.actor_id IS DISTINCT FROM NEW.requester_id
      OR NOT stewardship_export_authorized_v1(NEW.requester_id)
      OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
        WHERE active_configuration_id=NEW.configuration_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN stewardship_source_current s ON s.snapshot_id=NEW.source_id
        WHERE c.id=NEW.campaign_id AND p.id=NEW.timezone_configuration_id
          AND NEW.through_date=least(p.end_date,
            (stewardship_campaign_now_v1() AT TIME ZONE stewardship_timezone_name_v1(p.timezone))::date))
      OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
        FROM stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
      -- The campaign's current pledge review watermark too (0185, #2202).
      OR NEW.review_watermark<>public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)
      OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
        WHERE name=stewardship_timezone_name_v1(NEW.browser_timezone))
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
        AND root_id=id AND task_type='report_exact_export' AND domain_request_id=NEW.id
        AND initiated_by_id=NEW.requester_id AND idempotency_key=NEW.id::text AND state='queued')
    THEN RAISE EXCEPTION 'Exact export lacks frozen authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_report_email_preparation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_campaign_configuration%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; expected jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Report preparation history is retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.cursor IS NOT NULL OR NEW.run_id IS NOT NULL
          OR NEW.task_fence IS NOT NULL OR NEW.worker_id IS NOT NULL
          OR NEW.covered_slots IS NOT NULL OR NEW.cutoff>public.stewardship_campaign_now_v1()
          OR NOT (
              (NEW.phase='dates' AND NEW.occurrence_id IS NULL)
              OR (NEW.phase='inputs' AND NEW.occurrence_id=NEW.id AND EXISTS(
                  SELECT 1 FROM public.stewardship_report_email_manual_request r
                  JOIN public.stewardship_schedule_occurrence o ON o.id=r.id
                  WHERE r.id=NEW.id AND r.task_id=NEW.task_id AND r.campaign_id=NEW.campaign_id
                    AND r.definition_id=NEW.definition_id AND r.revision_id=NEW.revision_id
                    AND o.definition_id=NEW.definition_id AND o.revision_id=NEW.revision_id
                    AND o.mode=NEW.mode AND o.target='admins' AND o.state='pending'
                    AND o.slot='manual:'||NEW.id::text)))
          OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
              JOIN public.stewardship_task_run t ON t.root_id=p.task_id
              WHERE p.definition_id=NEW.definition_id AND p.mode=NEW.mode
                AND p.phase NOT IN ('complete','cancelled')
                AND t.state IN ('queued','running','retry_wait','abandoned'))
          OR NOT public.stewardship_report_email_scope_v1(NEW.campaign_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition d
              WHERE d.id=NEW.definition_id AND d.campaign_id=NEW.campaign_id
                AND d.current_revision_id=NEW.revision_id AND d.kind='report')
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.task_id
              AND t.root_id=t.id AND t.task_type='report_email_prepare'
              AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
              AND t.state='queued' AND t.initiated_by_id IS NOT DISTINCT FROM NEW.actor_id)
        THEN RAISE EXCEPTION 'Report preparation lacks current schedule ownership' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.created_at,NEW.campaign_id,NEW.definition_id,NEW.revision_id,
          NEW.campaign_configuration_id,NEW.task_id,NEW.mode,NEW.rehearsal_epoch_id)
        IS DISTINCT FROM
       ROW(OLD.id,OLD.created_at,OLD.campaign_id,OLD.definition_id,OLD.revision_id,
          OLD.campaign_configuration_id,OLD.task_id,OLD.mode,OLD.rehearsal_epoch_id)
      OR (NEW.cutoff<>OLD.cutoff AND NOT (OLD.version=1 AND OLD.phase='dates'
          AND NEW.phase='dates' AND NEW.cursor IS NULL AND NEW.occurrence_id IS NULL
          AND NEW.cutoff>=OLD.cutoff AND NEW.cutoff<=public.stewardship_campaign_now_v1()))
      OR NEW.version<>OLD.version+1 OR OLD.phase IN ('complete','cancelled')
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.run_id
          AND t.root_id=OLD.task_id AND t.task_type='report_email_prepare'
          AND t.domain_request_id=OLD.id
          AND public.stewardship_fact_live(t.id,NEW.task_fence,NEW.worker_id))
      OR (NEW.phase<>'cancelled' AND NOT public.stewardship_report_email_scope_v1(
          NEW.campaign_id,NEW.revision_id,NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id))
      OR NOT (NEW.phase='cancelled' OR NEW.phase=OLD.phase
          OR (OLD.phase='dates' AND NEW.phase='cover')
          OR (OLD.phase='cover' AND NEW.phase IN ('inputs','complete'))
          OR (OLD.phase='inputs' AND NEW.phase='capture')
          OR (OLD.phase='capture' AND NEW.phase IN ('fanout','complete'))
          OR (OLD.phase='fanout' AND NEW.phase='complete'))
      OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))
      OR (OLD.phase<>'dates' AND NEW.cursor IS DISTINCT FROM OLD.cursor)
      OR NOT public.stewardship_report_email_page_v1(to_jsonb(OLD),to_jsonb(NEW))
      OR (OLD.occurrence_id IS NOT NULL AND NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id)
      OR (NEW.phase IN ('inputs','capture','fanout') AND NEW.occurrence_id IS NULL)
      OR (NEW.phase<>'cancelled' AND NEW.occurrence_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM public.stewardship_schedule_occurrence o
          WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
            AND o.revision_id=NEW.revision_id AND o.mode=NEW.mode
            AND o.target='admins' AND o.due_at<=NEW.cutoff))
      -- The inputs are recorded exactly once, entering capture.
      OR (NOT (OLD.phase='inputs' AND NEW.phase='capture')
          AND ROW(NEW.covered_slots,NEW.as_of_kind,NEW.as_of,NEW.captured_at,NEW.report_day,
              NEW.source_id,NEW.submission_watermark,NEW.review_watermark,
              NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM
              ROW(OLD.covered_slots,OLD.as_of_kind,OLD.as_of,OLD.captured_at,OLD.report_day,
              OLD.source_id,OLD.submission_watermark,OLD.review_watermark,
              OLD.timezone_configuration_id,OLD.through_date))
    THEN RAISE EXCEPTION 'Report preparation requires a fenced monotonic transition' USING ERRCODE='23514'; END IF;
    IF OLD.phase='cover' AND NEW.phase IN ('inputs','complete') AND (EXISTS(
        SELECT 1 FROM public.stewardship_report_email_predecessors_v1(
            NEW.definition_id,NEW.mode,NEW.rehearsal_epoch_id)) OR EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=NEW.revision_id AND o.mode=NEW.mode AND o.target='admins'
          AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
          AND o.due_at<=NEW.cutoff AND o.id IS DISTINCT FROM NEW.occurrence_id
          AND o.slot !~ '^manual:'
          AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
              WHERE other.id<>NEW.id AND other.occurrence_id=o.id))) THEN
        RAISE EXCEPTION 'Report coverage must finish before its inputs' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='inputs' AND NEW.phase='capture' THEN
        SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=NEW.revision_id;
        SELECT p.* INTO c FROM public.stewardship_campaign x
            JOIN public.stewardship_campaign_configuration p ON p.id=x.active_configuration_id
            WHERE x.id=NEW.campaign_id;
        expected:=public.stewardship_report_email_inputs_v1(v.values,NEW.covered_slots,
            NEW.captured_at,c.start_date,c.end_date,c.timezone);
        IF NEW.covered_slots IS DISTINCT FROM public.stewardship_report_email_slots_v1(NEW.occurrence_id,NEW.mode)
          OR NEW.as_of_kind IS DISTINCT FROM v.values->>'as_of'
          -- Captured in this transaction, on the campaign clock (#1786's
          -- conversion: the clock the capture reads, which a test may fix).
          OR NEW.captured_at NOT BETWEEN public.stewardship_campaign_now_v1()
              -(statement_timestamp()-transaction_timestamp()) AND public.stewardship_campaign_now_v1()
          OR NEW.timezone_configuration_id IS DISTINCT FROM c.id
          OR NEW.as_of IS DISTINCT FROM (expected->>'as_of')::timestamptz
          OR NEW.report_day IS DISTINCT FROM (expected->>'report_day')::date
          OR NEW.through_date IS DISTINCT FROM (expected->>'through_date')::date
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_source_current current
              JOIN public.stewardship_source_snapshot s ON s.id=current.snapshot_id
              WHERE s.id=NEW.source_id AND s.state='promoted' AND s.compacted_at IS NULL)
          OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
              FROM public.stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
          -- The campaign's current pledge review watermark too (0185, #2202).
          OR NEW.review_watermark IS DISTINCT FROM
              public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)
        THEN RAISE EXCEPTION 'Report inputs must be the exact current observation' USING ERRCODE='23514'; END IF;
    END IF;
    -- Fanout needs the snapshot, a recipient and something to send.
    IF NEW.phase='fanout' AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_snapshot s
        WHERE s.preparation_id=NEW.id AND s.recipients<>'[]'::jsonb
          AND NOT public.stewardship_report_email_skipped_v1(s.id)) THEN
        RAISE EXCEPTION 'Report fanout requires its committed snapshot with something to send' USING ERRCODE='23514';
    END IF;
    -- A capture completes without fanout only when nothing is to be sent:
    -- the send is skipped as empty, or it has no recipient.
    IF OLD.phase='capture' AND NEW.phase='complete' AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s WHERE s.preparation_id=NEW.id
          AND (s.recipients='[]'::jsonb OR public.stewardship_report_email_skipped_v1(s.id))) THEN
        RAISE EXCEPTION 'Report capture completes only with nothing to send' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='fanout' AND NEW.phase='complete' AND EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s
        CROSS JOIN LATERAL jsonb_array_elements(s.recipients) member
        WHERE s.preparation_id=NEW.id AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_report_email_recipient r
            WHERE r.snapshot_id=s.id AND r.address=member->>'address')) THEN
        RAISE EXCEPTION 'Report completion requires every cohort recipient' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_report_email_snapshot_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; requests jsonb; captured jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=NEW.preparation_id;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=p.revision_id;
    SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4)
        ORDER BY position) INTO captured
        FROM jsonb_array_elements(NEW.blocks) b
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(b->'data'->'items','[]'::jsonb))
            WITH ORDINALITY entry(item,position)
        WHERE b->>'block'='additional_information';
    -- The open requests are read only for a report with their block.
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(v.values->'blocks') b
        WHERE b->>'block'='additional_information') THEN
        SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4)
            ORDER BY position) INTO requests
            FROM jsonb_array_elements(public.stewardship_report_email_requests_v1(
                p.campaign_id,p.source_id,p.as_of)) WITH ORDINALITY entry(item,position);
    END IF;
    IF p.phase IS DISTINCT FROM 'capture' OR NEW.campaign_id<>p.campaign_id
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT public.stewardship_report_email_live_v1(p.id,NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration
          WHERE active_configuration_id=NEW.configuration_id)
      OR NEW.values IS DISTINCT FROM v.values
      OR jsonb_typeof(NEW.blocks) IS DISTINCT FROM 'array'
      OR (SELECT jsonb_agg(jsonb_build_object('block',b->'block','version',b->'version')
              ORDER BY position) FROM jsonb_array_elements(NEW.blocks) WITH ORDINALITY x(b,position))
         IS DISTINCT FROM
         (SELECT jsonb_agg(jsonb_build_object('block',b->'block','version',b->'version')
              ORDER BY position) FROM jsonb_array_elements(v.values->'blocks') WITH ORDINALITY x(b,position))
      OR (EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
              WHERE b->>'block'='additional_information' AND b->'data'<>'null'::jsonb)
          AND captured IS DISTINCT FROM requests)
      OR (EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
              WHERE b->>'block'='additional_information' AND b->'data'='null'::jsonb)
          AND requests IS NOT NULL)
      OR NEW.has_content IS DISTINCT FROM EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
          WHERE (b->>'block' NOT IN ('additional_information','compared_with_past_campaigns'))
             OR (b->>'block'='additional_information'
                 AND jsonb_array_length(coalesce(b->'data'->'items','[]'::jsonb))>0)
             OR (b->>'block'='compared_with_past_campaigns' AND b->'data'<>'null'::jsonb))
      OR NEW.recipients IS DISTINCT FROM public.stewardship_report_email_cohort_v1(v.values,NEW.configuration_id)
      OR (p.through_date IS NULL AND NEW.fact_set_id IS NOT NULL)
      OR (p.through_date IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.stewardship_daily_fact_set f
          WHERE f.id=NEW.fact_set_id AND f.state='ready'
            AND ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.review_watermark,f.timezone_configuration_id,f.through_date)=
              ROW(p.campaign_id,'historical'::varchar,p.source_id,p.submission_watermark,
                coalesce(p.review_watermark,0),p.timezone_configuration_id,p.through_date)))
      OR length(NEW.subject)=0 OR NEW.subject ~ '[\r\n]' OR length(NEW.subject)>247
      OR octet_length(NEW.html) NOT BETWEEN 1 AND 8388608
      OR octet_length(NEW.text) NOT BETWEEN 1 AND 8388608
      OR jsonb_typeof(NEW.images) IS DISTINCT FROM 'array'
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.images) image
          WHERE jsonb_typeof(image)<>'array' OR jsonb_array_length(image)<>2
            -- A content ID may hold '@', as the participation chart's does
            -- (participation@parishkit), which file 0156 refused (#1786).
            OR image->>0 !~ '^[A-Za-z0-9._@-]{1,64}$'
            OR substring(decode(image->>1,'base64') FROM 1 FOR 8)<>decode('89504e470d0a1a0a','hex')
            OR octet_length(decode(image->>1,'base64'))>1048576)
    THEN RAISE EXCEPTION 'Report snapshot lacks its exact capture' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_campaign_comparison_v1(
    fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    selected record;
    observed record;
    day_end timestamp with time zone;
    result jsonb;
BEGIN
    IF session_user NOT IN ('pk_stewardship_web','pk_stewardship_worker') AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web and worker logins read campaign comparison figures' USING ERRCODE='42501';
    END IF;
    IF report_date IS NULL OR band_edges IS NULL OR cardinality(band_edges) NOT BETWEEN 1 AND 20
       OR array_position(band_edges, NULL) IS NOT NULL OR band_edges[1]<=0
       OR EXISTS (SELECT 1 FROM generate_subscripts(band_edges, 1) i
                  WHERE i>1 AND band_edges[i]<=band_edges[i - 1]) THEN
        RAISE EXCEPTION 'Campaign comparison needs a report date and increasing band edges'
            USING ERRCODE='22023';
    END IF;
    -- Only a ready historical generation, and only a day it observed, with
    -- the pledge Edits it recorded (0185, #2202).
    SELECT s.campaign_id, s.submission_watermark, s.pledge_edits, c.timezone INTO selected
    FROM public.stewardship_daily_fact_set s
    JOIN public.stewardship_campaign_configuration c ON c.id=s.timezone_configuration_id
    WHERE s.id=fact_set AND s.state='ready' AND s.population_scope='historical';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT d.source_generation, d.cumulative_responses INTO observed
    FROM public.stewardship_daily_fact d
    WHERE d.fact_set_id=fact_set AND d.local_date=report_date AND d.population_available;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    -- The day's end: the next local midnight in the fact set's time zone,
    -- named as PostgreSQL knows it (a legacy alias maps to its zone, #1286).
    day_end=(report_date + 1)::timestamp AT TIME ZONE public.stewardship_timezone_name_v1(selected.timezone);
    WITH cohort AS (
        SELECT f.id, f.family_duid FROM public.stewardship_family_campaign f
        WHERE f.campaign_id=selected.campaign_id AND f.first_eligible_at IS NOT NULL
          AND f.first_eligible_source_generation<=observed.source_generation
          AND f.first_eligible_at<day_end
    ), edits AS (
        -- The pledge Edits in force that the generation recorded (0185,
        -- #2202): each applies from the response it reviewed onward.
        SELECT e.family_id, e.from_sequence, e.annual_pledge
        FROM jsonb_to_recordset(selected.pledge_edits)
            AS e(family_id uuid, from_sequence bigint, annual_pledge numeric)
    ), latest AS (
        -- Each responding Family's latest live version by the day's end,
        -- with the pledge value in force the fact row counted: an Edit's
        -- amount from the response it reviewed onward, else the Family's
        -- (a version without a pledge stays without one, as in the facts).
        SELECT DISTINCT ON (r.family_id) c.family_duid,
            CASE WHEN e.family_id IS NOT NULL AND r.annual_pledge IS NOT NULL
                THEN e.annual_pledge ELSE r.annual_pledge END AS annual_pledge
        FROM public.stewardship_submission r JOIN cohort c ON c.id=r.family_id
        LEFT JOIN edits e ON e.family_id=r.family_id
            AND r.campaign_sequence>=e.from_sequence
        WHERE r.campaign_id=selected.campaign_id AND r.mode='live'
          AND r.campaign_sequence<=selected.submission_watermark
          AND r.submitted_at<day_end
        ORDER BY r.family_id, r.campaign_sequence DESC
    ), pledges AS (
        SELECT annual_pledge AS amount FROM latest WHERE annual_pledge>0
    ), sorted AS (
        SELECT array_agg(amount ORDER BY amount) AS a FROM pledges
    )
    SELECT jsonb_build_object(
        'responded', (SELECT count(*) FROM latest),
        -- A missing pledge makes the day's pledge figures unavailable, as on
        -- the graph; a known zero is not missing.
        'pledge_complete', (SELECT coalesce(bool_and(annual_pledge IS NOT NULL), true) FROM latest),
        'pledge_total', (SELECT coalesce(sum(annual_pledge), 0)::text FROM latest),
        'pledging', (SELECT count(*) FROM pledges),
        -- h = (n - 1)p + 1; x[floor h] + (h - floor h)(x[floor h + 1] - x[floor h]).
        'quartiles', (
            SELECT jsonb_agg(round(s.a[k.k] + (h.h - k.k) * (s.a[least(k.k + 1, n.n)] - s.a[k.k]), 2)::text
                             ORDER BY p.p)
            FROM sorted s
            CROSS JOIN LATERAL (SELECT cardinality(s.a) AS n) n
            CROSS JOIN unnest(ARRAY[0.25, 0.5, 0.75]::numeric[]) p(p)
            CROSS JOIN LATERAL (SELECT (n.n - 1) * p.p + 1 AS h) h
            CROSS JOIN LATERAL (SELECT floor(h.h)::integer AS k) k
            WHERE n.n>0),
        -- Band b holds a pledge at or above edge b - 1 and below edge b.
        'bands', (
            SELECT jsonb_agg(jsonb_build_object('band', b.b, 'families', coalesce(g.families, 0),
                                                'total', coalesce(g.total, 0)::text) ORDER BY b.b)
            FROM generate_series(1, cardinality(band_edges) + 1) b(b)
            LEFT JOIN (SELECT width_bucket(amount, band_edges) + 1 AS band,
                              count(*) AS families, sum(amount) AS total
                       FROM pledges GROUP BY 1) g ON g.band=b.b),
        'renewed', CASE WHEN prior_archive IS NULL THEN NULL ELSE (
            SELECT count(*) FROM public.stewardship_campaign_archive_family a
            JOIN public.stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid'
              AND a.family_id IN (SELECT family_duid FROM latest)) END,
        'prior_families', CASE WHEN prior_archive IS NULL THEN NULL ELSE (
            SELECT count(*) FROM public.stewardship_campaign_archive_family a
            JOIN public.stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid') END
    ) INTO result;
    IF (result->>'responded')::bigint IS DISTINCT FROM observed.cumulative_responses THEN
        RAISE EXCEPTION 'Campaign comparison figures disagree with their fact row'
            USING ERRCODE='23514';
    END IF;
    RETURN result;
END $$;
ALTER FUNCTION public.stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid) SECURITY DEFINER;

DO $check$
DECLARE
    tables text[] := ARRAY['stewardship_daily_fact_set','stewardship_fact_demand',
        'stewardship_exact_export_request','stewardship_report_email_preparation'];
    columns text[] := ARRAY['review_watermark','requested_review_watermark',
        'review_watermark','review_watermark'];
    required boolean[] := ARRAY[true,true,true,false];
BEGIN
    -- The watermark columns: bigint, never negative, NOT NULL but on the
    -- report email preparation, with no default left.
    FOR i IN 1..4 LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_attribute a
                WHERE a.attrelid=('public.'||tables[i])::regclass AND a.attname=columns[i]
                  AND NOT a.attisdropped AND a.atttypid='bigint'::regtype
                  AND a.attnotnull=required[i] AND NOT a.atthasdef)
           OR (SELECT count(*) FROM pg_constraint c
                WHERE c.conrelid=('public.'||tables[i])::regclass AND c.contype='c'
                  AND pg_get_constraintdef(c.oid)='CHECK (('||columns[i]||' >= 0))')<>1 THEN
            RAISE EXCEPTION 'Migration 0185 (series in force) did not add %.%', tables[i], columns[i];
        END IF;
    END LOOP;
    IF NOT EXISTS(SELECT 1 FROM pg_attribute a
            WHERE a.attrelid='public.stewardship_daily_fact_set'::regclass
              AND a.attname='pledge_edits' AND NOT a.attisdropped
              AND a.atttypid='jsonb'::regtype AND a.attnotnull AND NOT a.atthasdef)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint c
            WHERE c.conrelid='public.stewardship_daily_fact_set'::regclass
              AND c.conname='daily_fact_exact_inputs' AND c.contype='u'
              AND pg_get_constraintdef(c.oid)='UNIQUE (campaign_id, population_scope, '
                  ||'source_generation, submission_watermark, review_watermark, '
                  ||'timezone_configuration_id, through_date)')
       OR NOT EXISTS(SELECT 1 FROM pg_constraint c
            WHERE c.conrelid='public.stewardship_report_email_preparation'::regclass
              AND c.conname='report_email_review_inputs' AND c.contype='c' AND c.convalidated
              AND pg_get_constraintdef(c.oid) LIKE '%review_watermark IS NULL%'
              AND pg_get_constraintdef(c.oid) LIKE '%submission_watermark IS NOT NULL%') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) did not add the fact set inputs';
    END IF;
    -- The two definers: stable, SECURITY DEFINER, the pinned search_path,
    -- the login check and their selections; owned as 0184's definer is,
    -- with no EXECUTE for PUBLIC and no grant yet (database-grants adds
    -- them afterwards).
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid='public.stewardship_pledge_review_watermark_v1(uuid)'::regprocedure
              AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
              AND p.prorettype='bigint'::regtype
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%''pk_stewardship_web'',''pk_stewardship_worker'',''pk_stewardship_scheduler''%'
              AND p.prosrc LIKE '%AND NOT pg_has_role(session_user,%'
              AND p.prosrc LIKE '%FROM public.stewardship_pledge_review r%'
              AND p.prosrc LIKE '%WHERE f.campaign_id=campaign_uuid);%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid='public.stewardship_fact_pledge_edits_v1(uuid)'::regprocedure
              AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
              AND p.prorettype='jsonb'::regtype
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker'' AND NOT pg_has_role(session_user,%'
              AND p.prosrc LIKE '%FROM public.stewardship_pledge_edits_in_force_v1(campaign_uuid) e%'
              AND p.prosrc LIKE '%WHERE r.family_id=e.family_id AND r.action=''edit''%'
              AND p.prosrc LIKE '%A pledge Edit in force has no Edit row%'
              AND p.prosrc LIKE '%''from_sequence'',d.campaign_sequence,%') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) did not install the definers as declared';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid IN ('public.stewardship_pledge_review_watermark_v1(uuid)'::regprocedure,
                'public.stewardship_fact_pledge_edits_v1(uuid)'::regprocedure,
                'public.stewardship_fact_pledge_edits_record_v1()'::regprocedure)
              AND (p.proowner IS DISTINCT FROM (SELECT q.proowner FROM pg_proc q
                      WHERE q.oid='public.stewardship_pledge_edits_in_force_v1(uuid)'::regprocedure)
                   OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner)))
       OR has_function_privilege('public','public.stewardship_pledge_review_watermark_v1(uuid)','EXECUTE')
       OR has_function_privilege('public','public.stewardship_fact_pledge_edits_v1(uuid)','EXECUTE')
       OR has_function_privilege('public','public.stewardship_fact_pledge_edits_record_v1()','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) has unexpected owners or grants';
    END IF;
    -- The recording trigger: an invoker trigger, BEFORE INSERT, enabled.
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_daily_fact_set'::regclass
              AND t.tgname='stewardship_fact_pledge_edits' AND NOT t.tgisinternal
              AND t.tgenabled='O' AND t.tgtype=7
              AND p.oid='public.stewardship_fact_pledge_edits_record_v1()'::regprocedure
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%NEW.pledge_edits:=public.stewardship_fact_pledge_edits_v1(NEW.campaign_id);%'
              AND p.prosrc LIKE '%IF NEW.review_watermark>public.stewardship_pledge_review_watermark_v1(NEW.campaign_id) THEN%') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) did not install the recording trigger';
    END IF;
    -- The replaced bodies, each with its watermark comparison.
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_daily_fact_set_mutable_v1()'::regprocedure
            AND prosrc LIKE '%NEW."review_watermark" IS DISTINCT FROM OLD."review_watermark" OR NEW."pledge_edits" IS DISTINCT FROM OLD."pledge_edits"%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_demand_guard()'::regprocedure
            AND prosrc LIKE '%OR NEW.requested_review_watermark<OLD.requested_review_watermark%'
            AND prosrc LIKE '%NEW.requested_submission_watermark,NEW.requested_review_watermark,%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_reference_guard()'::regprocedure
            AND prosrc LIKE '%OR target.review_watermark<prior.review_watermark%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_disposable(uuid)'::regprocedure
            AND prosrc LIKE '%AND newer.review_watermark>=old.review_watermark%'
            AND prosrc LIKE '%r.review_watermark,r.timezone_configuration_id,r.through_date)=%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_runtime_binding_v1()'::regprocedure
            AND prosrc LIKE '%coalesce(exact_request.review_watermark,0),%'
            AND prosrc LIKE '%exact_request.review_watermark,
                exact_request.timezone_configuration_id%'
            AND prosrc LIKE '%demand.requested_review_watermark,%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_exact_matches_v1(uuid,uuid)'::regprocedure
            AND prosrc LIKE '%f.review_watermark,f.timezone_configuration_id,f.through_date))%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_exact_claimable_v1(uuid)'::regprocedure
            AND prosrc LIKE '%other.submission_watermark,other.review_watermark,%'
            AND prosrc LIKE '%f.review_watermark,f.timezone_configuration_id,f.through_date) AND f.state<>''ready''%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_exact_request_guard_v1()'::regprocedure
            AND prosrc LIKE '%OR NEW.review_watermark<>public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_report_email_preparation_guard_v1()'::regprocedure
            AND prosrc LIKE '%NEW.source_id,NEW.submission_watermark,NEW.review_watermark,%'
            AND prosrc LIKE '%OR NEW.review_watermark IS DISTINCT FROM
              public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_report_email_snapshot_guard_v1()'::regprocedure
            AND prosrc LIKE '%coalesce(p.review_watermark,0),p.timezone_configuration_id,p.through_date)))%') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) did not replace the identity checks';
    END IF;
    -- The comparison: its Edits, still a stable definer reading 0095's
    -- figures (CREATE OR REPLACE would drop SECURITY DEFINER without the
    -- ALTER above).
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid='public.stewardship_campaign_comparison_v1(uuid,date,numeric[],uuid)'::regprocedure
              AND p.prosecdef AND p.provolatile='s'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%SELECT s.campaign_id, s.submission_watermark, s.pledge_edits, c.timezone INTO selected%'
              AND p.prosrc LIKE '%FROM jsonb_to_recordset(selected.pledge_edits)%'
              AND p.prosrc LIKE '%AND r.campaign_sequence>=e.from_sequence%'
              AND p.prosrc LIKE '%public.stewardship_timezone_name_v1(selected.timezone)%'
              AND p.prosrc LIKE '%Campaign comparison figures disagree with their fact row%') THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) did not replace the comparison';
    END IF;
    -- CREATE OR REPLACE keeps owners and grants and names the same
    -- settings; check that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
                ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_daily_fact_set_mutable_v1',
            'stewardship_fact_demand_guard','stewardship_fact_reference_guard',
            'stewardship_fact_disposable','stewardship_fact_runtime_binding_v1',
            'stewardship_exact_matches_v1','stewardship_exact_claimable_v1',
            'stewardship_exact_request_guard_v1','stewardship_report_email_preparation_guard_v1',
            'stewardship_report_email_snapshot_guard_v1','stewardship_campaign_comparison_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0185_acl') THEN
        RAISE EXCEPTION 'Migration 0185 changed the replaced functions'' owners, grants or settings';
    END IF;
    -- The selections plan and run (a column or function they name that is
    -- missing fails here): a campaign that does not exist has none.
    IF public.stewardship_pledge_review_watermark_v1('00000000-0000-0000-0000-000000000000')<>0
       OR public.stewardship_fact_pledge_edits_v1('00000000-0000-0000-0000-000000000000')<>'[]'::jsonb
       OR public.stewardship_campaign_comparison_v1('00000000-0000-0000-0000-000000000000',
            DATE '2026-01-01',ARRAY[1]::numeric[],NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0185 (series in force) cannot read the selections';
    END IF;
END
$check$;
