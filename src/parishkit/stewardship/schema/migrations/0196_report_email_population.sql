-- Frozen forward migration file 0196 (the repository-wide file sequence,
-- assigned by the coordinator): report emails count today's campaign
-- Families, and their open requests leave out Ignored ones (#2331;
-- report-emails spec, "Campaign totals block" and "Open additional
-- information requests block"). This file is installed by its Django
-- migration in the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- The Administrator (2026-10-31) chose that a report email's figures
-- follow "In this campaign today", as the report pages do by default
-- (#2324), rather than the historical cohort (Families that left
-- included). The open requests block counted a request Staff had Ignored,
-- which no page lists as open, and named its count "open" rather than by
-- the stages it adds up. This file creates one function and replaces four
-- in place:
--   - stewardship_report_email_requests_v2 (new): v1's open requests
--     without the Ignored ones (the latest Staff revision's decision is
--     Ignored, or, saved before 0126, follow-up was left not needed), each
--     with its stage, to_review or to_handle, last. v1 stays installed
--     for the sends captured before this file.
--   - stewardship_report_email_snapshot_guard_v1: a new snapshot's kept
--     requests are v2's, stage included, and its pinned generation is the
--     current one at the send's recorded inputs.
--   - stewardship_fact_runtime_binding_v1: a report email's capture may
--     build its recorded inputs in either scope: current for the figures
--     its snapshot pins, historical for its Compared with past campaigns
--     block, which stays like for like with the archives.
--   - stewardship_source_pin_guard and stewardship_fact_input_pin_commit_v1:
--     a current generation's source input pin may belong to a report
--     email's live capture of that source.
-- None of the replaced functions is SECURITY DEFINER or has grants of its
-- own; v2 takes v1's attributes (STABLE, invoker, the default grants).
-- reports/report_email_capture.py and report_email_preview.py build and
-- read the same.
--
-- It ends with a DO block that refuses to commit unless every function is
-- installed as declared, with the replaced functions' owners, grants and
-- settings unchanged.

SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0196_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_report_email_snapshot_guard_v1',
        'stewardship_fact_runtime_binding_v1','stewardship_source_pin_guard',
        'stewardship_fact_input_pin_commit_v1')), true);

-- The open additional information requests of a campaign at an as-of
-- moment (report-emails decisions 14 and 15): live requests submitted by
-- then that are still current, not followed up and not Ignored, oldest
-- first, each [id, Family DUID, Family name, submitted at, text, stage],
-- the Family named from the pinned source as v1 names it. The stage is the
-- one the Additional information page places the request at
-- (pipeline.information_placement): to_handle when follow-up is needed,
-- else to_review; a request whose latest Staff revision ignored it is left
-- out, as reports/information_worklist.py's OPEN_GROUPS counts it.
CREATE FUNCTION public.stewardship_report_email_requests_v2(
    campaign uuid, source uuid, as_of timestamptz
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH items AS (
        SELECT i.id,f.family_duid,s.submitted_at,i.text,
            CASE WHEN i.follow_up_needed THEN 'to_handle' ELSE 'to_review' END AS stage,
            COALESCE(NULLIF(btrim(p.canonical::jsonb->>'lastName'),''),
                NULLIF(btrim(p.canonical::jsonb->>'mailingName'),''),
                NULLIF(btrim(concat_ws(' ',
                    NULLIF(btrim(p.canonical::jsonb->>'firstName'),''),
                    NULLIF(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                CASE WHEN p.id IS NULL THEN 'Not in ParishSoft'
                    ELSE 'Name not recorded in ParishSoft' END) AS family_name
        FROM public.stewardship_submission s
        JOIN public.stewardship_additional_information i ON i.submission_id=s.id
        JOIN public.stewardship_family_campaign f ON f.id=s.family_id
        LEFT JOIN public.stewardship_snapshot_family m ON m.snapshot_id=$2
            AND m.source_key=f.family_duid::text
        LEFT JOIN public.stewardship_source_family p ON p.id=m.payload_id
        WHERE s.campaign_id=$1 AND s.mode='live' AND s.submitted_at<=$3
          AND i.disposition='current_actionable' AND i.followed_up_at IS NULL
          -- Not Ignored: follow-up is needed, or no Staff revision ignored
          -- it (its latest has a decision other than Ignored, or there is
          -- none, which is To review).
          AND (i.follow_up_needed OR NOT coalesce((
              SELECT coalesce(r.review_decision,'ignored')='ignored'
              FROM public.stewardship_information_revision r WHERE r.item_id=i.id
              ORDER BY r.expected_version DESC LIMIT 1),false))
    ) SELECT coalesce(jsonb_agg(jsonb_build_array(id,family_duid,family_name,
        submitted_at,text,stage) ORDER BY submitted_at,id),'[]'::jsonb) FROM items
$$;

-- The report snapshot guard: requests v2 with their stages, and the
-- current generation pinned.
CREATE OR REPLACE FUNCTION public.stewardship_report_email_snapshot_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; requests jsonb; captured jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=NEW.preparation_id;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=p.revision_id;
    -- Each kept request carries its stage (to_review or to_handle) since
    -- 0196 (#2331), as requests_v2 returns it.
    SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4,
            item->5)
        ORDER BY position) INTO captured
        FROM jsonb_array_elements(NEW.blocks) b
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(b->'data'->'items','[]'::jsonb))
            WITH ORDINALITY entry(item,position)
        WHERE b->>'block'='additional_information';
    -- The open requests are read only for a report with their block.
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(v.values->'blocks') b
        WHERE b->>'block'='additional_information') THEN
        SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4,
                item->5)
            ORDER BY position) INTO requests
            FROM jsonb_array_elements(public.stewardship_report_email_requests_v2(
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
              -- The pinned generation counts today's campaign Families
              -- (0196, #2331), as the report pages do by default.
              ROW(p.campaign_id,'current'::varchar,p.source_id,p.submission_watermark,
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

-- The fact runtime binding: a report email builds either scope at its
-- recorded inputs.
CREATE OR REPLACE FUNCTION public.stewardship_fact_runtime_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand stewardship_fact_demand%ROWTYPE;
        owned_task stewardship_task_run%ROWTYPE;
        exact_request record;
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN NEW; END IF;
    SELECT * INTO owned_task FROM stewardship_task_run WHERE id=NEW.task_id;
    -- A report email's capture (#1786) builds exactly its recorded inputs:
    -- the generation its snapshot pins counts today's campaign Families
    -- (current), and its Compared with past campaigns block reads a
    -- historical one at the same inputs (0196, #2331).
    IF owned_task.task_type='report_email_prepare' THEN
        SELECT p.* INTO exact_request FROM stewardship_report_email_preparation p
            WHERE p.id=owned_task.domain_request_id AND p.task_id=owned_task.root_id
              AND p.phase='capture';
        -- A send whose inputs were recorded before 0185 (#2202) has no
        -- pledge review watermark; it pinned a generation of watermark 0.
        IF NOT FOUND OR NEW.population_scope NOT IN ('current','historical')
            OR ROW(NEW.campaign_id,NEW.source_id,
            NEW.submission_watermark,NEW.review_watermark,
            NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,
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

-- The source pin guard: a report email capture may pin its current
-- generation's source.
CREATE OR REPLACE FUNCTION public.stewardship_source_pin_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE snapshot record;
BEGIN
    -- Separate role branches before planning their private table queries.
    -- A boolean AND does not prevent PostgreSQL privilege checks on subqueries.
    IF current_user='pk_stewardship_worker' THEN
      IF TG_OP='DELETE' THEN
        -- Source retention deletes a superseded, disposable fact generation
        -- and then releases its now-orphaned source input pin, only while it
        -- owns the live compaction lease. The fact rows' own guards admit
        -- only stewardship_fact_disposable generations.
        IF OLD.parent_kind='facts'
           AND NOT EXISTS (SELECT 1 FROM public.stewardship_daily_fact_set
               WHERE id=OLD.parent_id)
           AND EXISTS (SELECT 1 FROM public.stewardship_source_lease l
               JOIN public.stewardship_task_run t ON t.id=l.owner_id
               WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                 AND t.state='running' AND t.fence=l.task_fence
                 AND t.worker_id=l.worker_id
                 AND t.lease_expires_at > clock_timestamp())
        THEN
            -- Same snapshot row lock as every other pin release.
            PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id
                FOR UPDATE;
            RETURN OLD;
        END IF;
        IF OLD.parent_kind <> 'submission'
           OR NOT public.stewardship_response_source_owner_v1(
               (SELECT snapshot_id FROM public.stewardship_source_current))
           OR public.stewardship_response_pin_required_v1(OLD.parent_id,OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker may release only unused response comparison inputs'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='digest' THEN
        -- A report email's inputs (#1786) pin the current promoted source to
        -- its preparation, by its live claim, as it records them.
        IF NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_report_email_preparation p
            JOIN stewardship_task_run t ON t.root_id=p.task_id AND t.state='running'
            JOIN stewardship_source_current current ON current.snapshot_id=NEW.snapshot_id
            WHERE p.id=NEW.parent_id AND p.phase='inputs' AND p.source_id IS NULL
              AND stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
        ) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Report source protection requires its live preparation'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='facts' THEN
        IF NEW.expires_at IS NOT NULL OR NOT (EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_fact_demand d
                ON d.id=t.domain_request_id
            WHERE t.task_type='report_facts' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp()
                AND d.population_scope='current'
                AND d.requested_source_id=NEW.snapshot_id
                AND d.claimed_generation_id IS NULL
        ) OR EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_exact_export_request r
                ON r.id=t.domain_request_id AND r.task_id=t.root_id
            WHERE t.task_type='report_exact_export' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp() AND r.population_scope='current'
                AND r.source_id=NEW.snapshot_id
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
                AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=r.id)
        ) OR EXISTS (
            -- A report email's capture builds its current generation from
            -- the source its inputs recorded, by its live claim (0196,
            -- #2331).
            SELECT 1 FROM stewardship_report_email_preparation p
            JOIN stewardship_task_run t ON t.root_id=p.task_id AND t.state='running'
            WHERE t.task_type='report_email_prepare' AND p.phase='capture'
                AND p.source_id=NEW.snapshot_id
                AND stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
        )) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Worker fact input protection requires its live demand'
                USING ERRCODE='23514';
        END IF;
      ELSE
      IF (
        TG_OP <> 'INSERT' OR NEW.parent_kind <> 'submission' OR NEW.expires_at IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT (EXISTS (
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_submission response ON response.id=change.submission_id
            WHERE response.id=NEW.parent_id
              AND change.execution IN ('pending','conflict','queued','failed')
        ) OR EXISTS (
            SELECT 1 FROM public.stewardship_ministry_request request
            WHERE request.submission_id=NEW.parent_id AND request.entity_kind='member'
              AND request.state IN ('new','assigned','in_progress')
        ) OR EXISTS (
            -- An Updated in ParishSoft change this promotion found
            -- ParishSoft no longer holds (#1749): its conflict check, at
            -- its version and in this snapshot, comes first; the census
            -- guard then moves it to conflict at this pinned snapshot.
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_refresh_check k ON k.proposal_id=change.id
                AND k.item_version=change.version AND k.outcome='conflict'
                AND k.snapshot_id=NEW.snapshot_id
            WHERE change.submission_id=NEW.parent_id AND change.execution='published'
        ))
        OR NOT public.stewardship_response_source_owner_v1(NEW.snapshot_id)
    ) THEN
        RAISE EXCEPTION 'Worker source protection requires current response reconciliation'
            USING ERRCODE='23514';
      END IF;
      END IF;
    END IF;
    IF current_user='pk_stewardship_web' THEN
      IF (
        (TG_OP <> 'INSERT' AND OLD.parent_kind <> 'form_baseline') OR
        (TG_OP <> 'DELETE' AND NEW.parent_kind NOT IN ('form_baseline','submission','report'))
    ) THEN
        RAISE EXCEPTION 'Web source protection is limited to Family forms'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='report' AND (
        NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_exact_export_request r
            WHERE r.id=NEW.parent_id AND r.source_id=NEW.snapshot_id
                AND r.population_scope='current'
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
        )) THEN
        RAISE EXCEPTION 'Web report protection requires its exact retained request'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='form_baseline' AND NOT EXISTS (
        SELECT 1 FROM stewardship_family_form_baseline
        WHERE id=NEW.parent_id AND source_id=NEW.snapshot_id AND state='open'
          AND expires_at=NEW.expires_at
    ) THEN
        RAISE EXCEPTION 'Web form protection requires its owned baseline'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='submission' AND NOT EXISTS (
        SELECT 1 FROM stewardship_submission response
        JOIN stewardship_family_form_baseline baseline ON baseline.id=response.baseline_id
        WHERE response.id=NEW.parent_id AND baseline.state='open'
          AND NEW.snapshot_id IN (response.reviewed_source_id,response.validation_source_id)
          AND NEW.expires_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Web response protection requires its final submission'
            USING ERRCODE='23514';
    END IF;
    END IF;
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id FOR UPDATE;
        RETURN OLD;
    END IF;
    SELECT state, compacted_at INTO snapshot FROM stewardship_source_snapshot WHERE id=NEW.snapshot_id
        FOR UPDATE;
    IF NOT FOUND OR snapshot.state <> 'promoted'
       OR snapshot.compacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Only reconstructable promoted snapshots can be protected'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- The fact input pin commit check: the same builder.
CREATE OR REPLACE FUNCTION public.stewardship_fact_input_pin_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND NEW.parent_kind='facts'
        AND NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
            JOIN stewardship_task_run t ON t.id=f.task_id
            WHERE f.id=NEW.parent_id AND f.source_id=NEW.snapshot_id
                AND f.population_scope='current' AND f.state='building'
                AND t.task_type IN ('report_facts','report_exact_export',
                    'report_email_prepare')
                AND stewardship_fact_live(f.task_id,f.task_fence,f.worker_id)) THEN
        RAISE EXCEPTION 'Worker fact input pin requires its exact retained builder'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_report_email_requests_v2(uuid,uuid,timestamp with time zone)'::regprocedure
            AND prosrc LIKE '%r.review_decision,''ignored'')=''ignored''%'
            AND prosrc LIKE '%CASE WHEN i.follow_up_needed THEN ''to_handle'' ELSE ''to_review'' END AS stage%'
            AND NOT prosecdef AND provolatile='s'
            AND proacl IS NOT DISTINCT FROM (SELECT v1.proacl FROM pg_proc v1
                WHERE v1.oid='public.stewardship_report_email_requests_v1(uuid,uuid,timestamp with time zone)'::regprocedure))
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_report_email_snapshot_guard_v1()'::regprocedure
            AND prosrc LIKE '%public.stewardship_report_email_requests_v2(%'
            AND prosrc NOT LIKE '%stewardship_report_email_requests_v1(%'
            AND prosrc LIKE '%ROW(p.campaign_id,''current''::varchar,p.source_id,p.submission_watermark,%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_runtime_binding_v1()'::regprocedure
            AND prosrc LIKE '%IF NOT FOUND OR NEW.population_scope NOT IN (''current'',''historical'')%'
            AND prosrc NOT LIKE '%exact_request.campaign_id,''historical''::varchar%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_source_pin_guard()'::regprocedure
            AND prosrc LIKE '%WHERE t.task_type=''report_email_prepare'' AND p.phase=''capture''%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_input_pin_commit_v1()'::regprocedure
            AND prosrc LIKE '%''report_facts'',''report_exact_export'',%''report_email_prepare'')%') THEN
        RAISE EXCEPTION 'Migration 0196 (report email population) did not install its functions';
    END IF;
    -- CREATE OR REPLACE keeps owners and grants and names the same
    -- settings; check that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_report_email_snapshot_guard_v1',
        'stewardship_fact_runtime_binding_v1','stewardship_source_pin_guard',
        'stewardship_fact_input_pin_commit_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0196_acl') THEN
        RAISE EXCEPTION 'Migration 0196 changed the replaced functions'' owners, grants or settings';
    END IF;
END
$check$;
