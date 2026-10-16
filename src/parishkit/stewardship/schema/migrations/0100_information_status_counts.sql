-- Frozen forward migration file 0100 (the repository-wide file sequence):
-- the Additional information page counts its matching requests by
-- follow-up state (#720). This file is installed by the Django migration
-- that names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- The page draws a Follow-up status chart for every request its filters
-- match, not only the table's page. Counting them in Python would read
-- every matching row; the installed selection already filters them all to
-- report the total, so it now returns the counts beside the total, from the
-- same rows in the same statement.
--
-- This file replaces stewardship_information_report_v2 (migration-owned;
-- created by 0064, last replaced by 0094), copied whole from 0094's body
-- with one addition: a 'counts' object (completed, needed_open,
-- not_needed_open). The parameters, every filter, the order, the page rows
-- and every other returned key are unchanged; an older caller ignores the
-- new key. A complete export captured after this also stores the counts in
-- its document, which the renderer does not read. The function is not
-- SECURITY DEFINER, before or after, so there is no ALTER FUNCTION to
-- repeat; it keeps its settings and grants.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants and settings before the replacement (with how many
-- definitions there are), compared in the DO block. Settings are compared
-- in sorted order: an installed database may hold them in the order of a
-- later ALTER FUNCTION ... SET. A transaction-local setting holds them, not
-- a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0100_acl', (
    SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_information_report_v2'), true);

-- stewardship_information_report_v2: copied from 0094; only the counts added.
CREATE OR REPLACE FUNCTION public.stewardship_information_report_v2(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','disposition','needed','completed','start','end','sort','zone']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort','zone']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc','duid','duid_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
       -- Days are the viewer's browser zone (#558): required with a date,
       -- and always one the server's time zone catalog knows.
       OR ((f->>'start'<>'' OR f->>'end'<>'') AND f->>'zone'='')
       OR (f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(f->>'zone')))
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                -- No name (NULL) for a Family the data no longer has: the
                -- page shows its ParishSoft status badge alone, and it sorts
                -- after the names (#1216). A present Family without a usable
                -- name reads so.
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END)
                AS family_name,
            -- The surname that leads the shown name, as Python names it
            -- (migration 0045's shared functions); family_name above keeps
            -- its own trim for the returned rows.
            stewardship_family_surname_v1(p.canonical::jsonb,
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether ParishSoft marks the Family active (NULL when it is not
            -- in the data), for its ParishSoft status (#1216).
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
                THEN (p.canonical::jsonb->'active')::boolean END AS active,
            h.value->'reported' ? i.id::text AS previously_reported,
            h.value->'corrected' @>
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          -- From local midnight of the first day to local midnight after the
          -- last, in the viewer's zone; a DST day is 23 or 25 hours long.
          -- Each bound is a scalar subquery, so it is computed once per call.
          AND (f->>'start'='' OR s.submitted_at>=(SELECT
              stewardship_browser_day_start_v1((f->>'start')::date,f->>'zone')))
          AND (f->>'end'='' OR s.submitted_at<(SELECT
              stewardship_browser_day_start_v1((f->>'end')::date+1,f->>'zone')))
    ), head_names AS MATERIALIZED (
        -- Each Family's active heads as the shown name lists them, in DUID
        -- order (stewardship_family_head_part_v1), so search and sort match
        -- what the page shows (#960). One set-based join over each Family's
        -- heads, skipped when neither a search nor a name sort reads the name.
        SELECT r.family_duid,array_agg(
                stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
                ORDER BY h.head::bigint) AS parts
        FROM (SELECT DISTINCT family_duid,surname,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "Surname, A and B" (family_names.family_heads_name).
        SELECT r.*,stewardship_family_heads_name_v1(r.surname,n.parts) AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(surname) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
            CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
            CASE WHEN f->>'sort'='duid' THEN family_duid END,
            CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        -- The shown name and heads only order and search; the page and an
        -- export capture keep the row they always had.
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname','display_name']
            || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        -- Every matching request by its follow-up state, for the page's
        -- Follow-up status chart (#720): completed, or not yet completed
        -- with follow-up marked needed or not. The three add up to total.
        'counts',(SELECT jsonb_build_object(
            'completed',count(*) FILTER (WHERE followed_up_at IS NOT NULL),
            'needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND follow_up_needed),
            'not_needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND NOT follow_up_needed))
            FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

DO $check$
BEGIN
    -- The one definition, invoker, stable, returning jsonb, with the counts
    -- and 0094's name fallback and active flag still in place.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_information_report_v2'
          AND p.oid='public.stewardship_information_report_v2(uuid,jsonb,integer,uuid,integer)'::regprocedure
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.prosrc LIKE '%''counts'',(SELECT jsonb_build_object(%'
          AND p.prosrc LIKE '%''completed'',count(*) FILTER (WHERE followed_up_at IS NOT NULL),%'
          AND p.prosrc LIKE '%''needed_open'',count(*) FILTER (WHERE followed_up_at IS NULL AND follow_up_needed),%'
          AND p.prosrc LIKE '%''not_needed_open'',count(*) FILTER (WHERE followed_up_at IS NULL AND NOT follow_up_needed))%'
          AND p.prosrc LIKE '%''total'',(SELECT count(*) FROM filtered),%'
          AND p.prosrc LIKE '%IS NOT NULL THEN ''Name not recorded in ParishSoft'' END)%'
          AND p.prosrc LIKE '%THEN (p.canonical::jsonb->''active'')::boolean END AS active,%')<>1 THEN
        RAISE EXCEPTION 'Migration 0100 (Additional information status counts) did not replace the selection';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_information_report_v2')
        IS DISTINCT FROM current_setting('stewardship.migration_0100_acl') THEN
        RAISE EXCEPTION 'Migration 0100 changed the selection''s grants or settings';
    END IF;
END
$check$;
