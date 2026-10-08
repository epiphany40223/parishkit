-- Frozen forward migration file 0015 (the repository-wide file sequence):
-- a live campaign's Reminder WorkGroup setting stays editable (#861). This
-- file is installed by its Django migration and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that this copy of the function still equals the fresh-install
-- baseline's (schema/functions.sql). A later change to the function gets its
-- own numbered file. A fresh install runs the baseline and the earlier files
-- and then this; functions.sql already carries this body, so the install
-- ends in the same catalog as an upgraded database.
--
-- A campaign's optional reminder_workgroup value names the ParishSoft Family
-- WorkGroup whose Families get no Reminders. Staff mark those Families while
-- the campaign runs, so the Administrator must be able to set, change or
-- clear the name after go-live. The configuration pointer guard refuses any
-- change to a structurally locked (live) campaign's values except its listed
-- exempt keys; this replaces the guard with the same body plus one more
-- exempt key, reminder_workgroup, in both of its exempt-key arrays. Nothing
-- else in the guard changes: every other structural value of a live campaign
-- stays locked, history stays fixed and the Ministry exemption (#342) is
-- unchanged. No credential, code, link token or token lookup changes.
-- Reversing needs its own forward migration that restores the old arrays.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
        IF OLD.current_campaign_id IS NULL AND candidate.timezone<>(SELECT timezone FROM stewardship_parish WHERE configuration_id=NEW.active_configuration_id) THEN
            RAISE EXCEPTION 'New draft must copy the parish timezone' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- The one reviewed live structural exemption (#342): an Administrator
        -- may change a locked campaign's Ministry selections while it is still
        -- open. The value must stay canonical: a strictly ascending array of
        -- whole numbers, as the YAML schema writes it. Removing is always
        -- allowed. Every added DUID must be visible now: in the promoted
        -- catalog, not inactive in this candidate's Ministry activity, in a
        -- campaign with the Ministry module. Answers are never touched here.
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
                RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
            END IF;
            -- The value is now a canonical array of whole numbers.
            SELECT coalesce(array_agg((added.duid#>>'{}')::numeric),'{}') INTO live_added
            FROM jsonb_array_elements(candidate.values->'ministry_duids') added(duid)
            WHERE NOT coalesce(live_before @> added.duid,false);
            IF cardinality(live_added)>0 THEN
                IF NOT coalesce(candidate.values->'modules' ? 'ministry',false)
                    OR EXISTS (SELECT 1 FROM unnest(live_added) d WHERE d NOT BETWEEN 1 AND 2147483647) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
                -- A separate statement, reached only when a locked open campaign
                -- gains a Ministry: PostgreSQL checks EXECUTE on the definer
                -- stewardship_ministry_catalog_v1 whenever a statement naming it
                -- starts, whatever a CASE or AND would evaluate. Only the
                -- configuration installer, which holds no source grants, needs
                -- it; setup completion and other activations never get here.
                IF EXISTS (SELECT 1 FROM unnest(live_added) d
                    WHERE NOT EXISTS (SELECT 1 FROM stewardship_ministry_catalog_v1() present
                        WHERE present.ministry_duid=d::integer
                          AND NOT EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                              WHERE activity.configuration_id=NEW.active_configuration_id
                                AND activity.organization_id=present.organization_id
                                AND activity.ministry_duid=present.ministry_duid
                                AND NOT activity.active))) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

-- CREATE OR REPLACE keeps the owner and grants but resets every attribute
-- the command does not name. The baseline gives this guard only its fixed
-- search_path (named above) and does not make it SECURITY DEFINER, so there
-- is nothing else to restore; the check below proves both.
--
-- Refuse to commit unless the new body is installed: the new key in both
-- exempt-key arrays, the fixed search_path, and not SECURITY DEFINER, so an
-- upgrade cannot report success while the old guard is in place.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_campaign_pointer_v1'
                     AND regexp_count(p.prosrc,
                         '''artwork'',''reminder_workgroup'',''ministry_duids''')=2
                     AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND NOT p.prosecdef) THEN
        RAISE EXCEPTION 'stewardship_campaign_pointer_v1 was not replaced';
    END IF;
END
$check$;
