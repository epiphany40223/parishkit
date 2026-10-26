-- The bodies the fresh-install baseline held before frozen migration 0185
-- (#2202) replaced them, copied from src/parishkit/stewardship/schema
-- (functions.sql and exact_exports.sql) at that release. The users-swap
-- tests put them back while 0185 is pending (before_series_in_force in
-- test_users_swap_postgresql.py), since 0185 also removes the columns
-- these bodies' replacements read.

CREATE OR REPLACE FUNCTION public.stewardship_daily_fact_set_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."population_scope" IS DISTINCT FROM OLD."population_scope" OR NEW."source_id" IS DISTINCT FROM OLD."source_id" OR NEW."source_generation" IS DISTINCT FROM OLD."source_generation" OR NEW."submission_watermark" IS DISTINCT FROM OLD."submission_watermark" OR NEW."timezone_configuration_id" IS DISTINCT FROM OLD."timezone_configuration_id" OR NEW."through_date" IS DISTINCT FROM OLD."through_date" OR NEW."first_date" IS DISTINCT FROM OLD."first_date" OR NEW."last_date" IS DISTINCT FROM OLD."last_date" OR NEW."expected_count" IS DISTINCT FROM OLD."expected_count" THEN
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
        NEW.requested_timezone_configuration_id,NEW.requested_through_date)
        IS DISTINCT FROM ROW(OLD.requested_source_id,
        OLD.requested_source_generation,OLD.requested_submission_watermark,
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
                target.submission_watermark,target.timezone_configuration_id,
                target.through_date) IS DISTINCT FROM ROW(NEW.campaign_id,
                NEW.population_scope,NEW.requested_source_id,
                NEW.requested_submission_watermark,
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
                OR (target.timezone_configuration_id=prior.timezone_configuration_id
                    AND target.through_date<prior.through_date) THEN
                RAISE EXCEPTION 'Fact pointer cannot regress' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_matches_v1(request_uuid uuid, fact_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=fact_uuid
        WHERE r.id=request_uuid AND
          ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
              r.timezone_configuration_id,r.through_date)=
          ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
              f.timezone_configuration_id,f.through_date))
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
              other.submission_watermark,other.timezone_configuration_id,other.through_date)=
            ROW(r.campaign_id,r.population_scope,r.source_id,
              r.submission_watermark,r.timezone_configuration_id,r.through_date))
      -- Do not overtake an ordinary owner that has claimed but not frozen yet.
      AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand d
        JOIN stewardship_task_run t ON t.domain_request_id=d.id AND t.task_type='report_facts'
        WHERE d.campaign_id=r.campaign_id AND d.population_scope=r.population_scope
          AND t.state='running' AND d.claimed_generation_id IS NULL)
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
        JOIN stewardship_task_run owner ON owner.id=f.task_id
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date) AND f.state<>'ready'
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
      OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
        WHERE name=stewardship_timezone_name_v1(NEW.browser_timezone))
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
        AND root_id=id AND task_type='report_exact_export' AND domain_request_id=NEW.id
        AND initiated_by_id=NEW.requester_id AND idempotency_key=NEW.id::text AND state='queued')
    THEN RAISE EXCEPTION 'Exact export lacks frozen authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
