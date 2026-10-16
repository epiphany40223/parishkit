-- Frozen forward migration file 0103 (the repository-wide file sequence):
-- a new campaign draft may start in any time zone (#1330). This file is
-- installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- stewardship_campaign_pointer_v1 refused the configuration change that
-- creates the deployment's campaign unless the new draft's time zone was the
-- parish zone ('New draft must copy the parish timezone'). That only fixed
-- the draft's starting value: a draft's zone may be changed to any catalog
-- zone right afterwards, and every campaign date, boundary and schedule is
-- resolved from the campaign's own zone, never the parish's. The
-- Administrator decided on 2026-10-15 that Create the campaign defaults to
-- the browser's zone and accepts any valid zone (America/Kentucky/Louisville
-- with a parish zone of America/New_York was refused).
--
-- This file replaces the trigger function with 0091's body (the latest; no
-- later file replaces it), dropping only that one check. Configuration
-- validation still admits only a catalog zone before any change is
-- recorded. The function is not SECURITY DEFINER, keeps its pinned
-- search_path, and keeps its grants; no stored row changes.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants before the replacement, compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0103_acl', (
    SELECT coalesce(p.proacl::text,'default')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1'), true);

CREATE OR REPLACE FUNCTION public.stewardship_campaign_pointer_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
    live_state text; live_before jsonb; live_added numeric[];
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- The one reviewed live structural exemption (#342): an Administrator
        -- may change a locked campaign's Ministry selections while it is still
        -- open. The value must stay canonical: a strictly ascending array of
        -- whole numbers, as the YAML schema writes it. Removing is always
        -- allowed. Every added DUID must be visible now: in the promoted
        -- catalog, in a campaign with the Ministry module. Answers are never
        -- touched here.
        -- Nested CASEs fix the evaluation order, so a malformed value is
        -- refused, not miscast.
        SELECT c.state, old_c.values->'ministry_duids' INTO live_state, live_before
        FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
        WHERE c.id=target AND c.structural_locked
          AND old_c.values->'ministry_duids' IS DISTINCT FROM candidate.values->'ministry_duids';
        IF FOUND THEN
            IF live_state NOT IN ('scheduled','active')
                OR NOT coalesce(CASE WHEN jsonb_typeof(candidate.values->'ministry_duids')='array'
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)
                        WHERE jsonb_typeof(e.duid)<>'number' OR e.duid#>>'{}'!~'^[0-9]{1,19}$') THEN
                    candidate.values->'ministry_duids'=(SELECT coalesce(jsonb_agg(to_jsonb(d.duid) ORDER BY d.duid),'[]'::jsonb)
                        FROM (SELECT DISTINCT (e.duid#>>'{}')::numeric AS duid
                              FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)) d)
                    END,false) THEN
                RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
            END IF;
            -- The value is now a canonical array of whole numbers.
            SELECT coalesce(array_agg((added.duid#>>'{}')::numeric),'{}') INTO live_added
            FROM jsonb_array_elements(candidate.values->'ministry_duids') added(duid)
            WHERE NOT coalesce(live_before @> added.duid,false);
            IF cardinality(live_added)>0 THEN
                IF NOT coalesce(candidate.values->'modules' ? 'ministry',false)
                    OR EXISTS (SELECT 1 FROM unnest(live_added) d WHERE d NOT BETWEEN 1 AND 2147483647) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
                -- A separate statement, reached only when a locked open campaign
                -- gains a Ministry: PostgreSQL checks EXECUTE on the definer
                -- stewardship_ministry_catalog_v1 whenever a statement naming it
                -- starts, whatever a CASE or AND would evaluate. Only the
                -- configuration installer, which holds no source grants, needs
                -- it; setup completion and other activations never get here.
                IF EXISTS (SELECT 1 FROM unnest(live_added) d
                    WHERE NOT EXISTS (SELECT 1 FROM stewardship_ministry_catalog_v1() present
                        WHERE present.ministry_duid=d::integer)) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    -- Still the one invoker trigger function with its pinned search_path,
    -- holding 0091's live Ministry rule, without the parish-zone refusal.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1'
          AND p.oid='public.stewardship_campaign_pointer_v1()'::regprocedure
          AND NOT p.prosecdef AND p.prorettype='trigger'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc, 'can only add current Ministries to an open campaign')>0
          AND strpos(p.prosrc, 'Successor creation is not admitted')>0
          AND strpos(p.prosrc, 'New draft must copy the parish timezone')=0)<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0103: the campaign pointer function is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0103_acl', true) IS DISTINCT FROM (
           SELECT coalesce(p.proacl::text,'default')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1') THEN
        RAISE EXCEPTION 'Migration 0103: the campaign pointer function''s grants changed';
    END IF;
END
$check$;
