-- Frozen forward migration file 0211 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): Back to
-- review undoes a parish-added census change's Entered by hand (#1840). This
-- file is installed by its Django migration in the responses app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- No review pipeline stage is final (review pipeline spec, "Undoing a
-- terminal stage"). An addition's later step was one row per addition
-- (0129: a UNIQUE addition_id, Entered by hand or withdrawn, "either is
-- final"). Now an entered addition can be reopened, with a required note,
-- back to To enter by hand, and then entered again or withdrawn, keeping
-- every step. So stewardship_census_addition_resolution (0129's,
-- migration-owned) changes, every existing row kept as it is:
--   * its UNIQUE addition_id goes (an index on it stays, for the foreign
--     key), and a sequence column numbers each addition's steps from 1
--     (every existing row is its addition's first step, so 1), unique per
--     addition, so two steps at once cannot both be recorded;
--   * its identity check also admits the action 'reopened' and needs a
--     sequence of at least 1.
-- Replaced, each copied whole from its latest body (0129's; no later file
-- replaced them) with only these changes:
--   stewardship_census_addition_guard_v1            an open addition of the
--       same field is one whose latest step is none or 'reopened';
--   stewardship_census_addition_resolution_guard_v1 the step is the next
--       sequence, and follows its latest one: an open addition is entered
--       or withdrawn, an entered one only reopened, with a note that is not
--       blank, and a withdrawn one takes none;
--   stewardship_census_addition_resolution_effect_v1 the audit's versions
--       are the step's sequence and the next (1 and 2 for a first step, as
--       before).
-- None of them is SECURITY DEFINER, before or after, so there is no ALTER
-- FUNCTION to repeat. Every statement can run again (the users swap test
-- reinstalls 0129's tables and runs this file after it). The file ends with
-- a DO block that refuses to commit unless the column, the constraints and
-- the new bodies are installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_census_addition_resolution" DROP CONSTRAINT IF EXISTS "stewardship_census_addition_resolution_addition_id_key";
CREATE INDEX IF NOT EXISTS "stewardship_census_addition_resolution_addition_id_60dcd2ac" ON "stewardship_census_addition_resolution" ("addition_id");
ALTER TABLE "stewardship_census_addition_resolution" ADD COLUMN IF NOT EXISTS "sequence" integer DEFAULT 1 NOT NULL;
ALTER TABLE "stewardship_census_addition_resolution" ALTER COLUMN "sequence" DROP DEFAULT;
ALTER TABLE "stewardship_census_addition_resolution" DROP CONSTRAINT IF EXISTS "census_addition_resolution_sequence";
ALTER TABLE "stewardship_census_addition_resolution" ADD CONSTRAINT "census_addition_resolution_sequence" UNIQUE ("addition_id", "sequence");
ALTER TABLE "stewardship_census_addition_resolution" DROP CONSTRAINT IF EXISTS "census_addition_resolution_identity";
ALTER TABLE "stewardship_census_addition_resolution" ADD CONSTRAINT "census_addition_resolution_identity" CHECK (("actor_id" IS NOT NULL AND ((action)::text = ANY ((ARRAY['entered'::character varying, 'withdrawn'::character varying, 'reopened'::character varying])::text[])) AND "sequence" >= 1));

CREATE OR REPLACE FUNCTION public.stewardship_census_addition_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family public.stewardship_family_campaign%ROWTYPE;
    replaced public.stewardship_proposed_change%ROWTYPE;
    edit_type text; edit_key text; edit_text text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Census additions are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO family FROM public.stewardship_family_campaign WHERE id=NEW.family_id;
    IF family.id IS NULL
       -- A current Staff member or Administrator (review pipeline, "Role-
       -- specific actions"), in a campaign that admits changes.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,false)
       OR NOT public.stewardship_export_admitted_v1(family.campaign_id,true)
       -- A Family that responded: a change is added beside its answers.
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_submission s
           WHERE s.family_id=family.id AND s.campaign_id=family.campaign_id
             AND s.mode='live')
       OR length(NEW.note)>2000
       -- The Family's address, or a field of a Member ParishSoft's current
       -- data lists as current in that Family: the fields a value can be
       -- typed for, as the worklist's Edit takes them.
       OR NOT ((NEW.entity_kind='family' AND NEW.entity_key=family.family_duid::text
                AND NEW.field IN ('home_address','mailing_address'))
               OR (NEW.entity_kind='member' AND NEW.entity_key ~ '^[1-9][0-9]{0,9}$'
                AND NEW.field IN ('prefix','first_name','middle_name','last_name',
                    'suffix','nickname','maiden_name','birth_date','gender','email',
                    'home_phone','mobile_phone','work_phone','marital_status',
                    'language','death_date')
                AND public.stewardship_member_source_status_v1(
                    (SELECT c.snapshot_id FROM public.stewardship_source_current c
                     WHERE c.singleton),NEW.entity_key,family.family_duid)='current'))
    THEN RAISE EXCEPTION 'Census addition lacks current authority, Family or field'
        USING ERRCODE='23514'; END IF;
    -- A phone is a phone record, an address an address of its parts, any
    -- other value text, at most 1,024 bytes. (The type is worked out first:
    -- an IF condition ends at its first THEN.)
    edit_type := CASE WHEN NEW.field IN ('home_phone','mobile_phone','work_phone',
            'home_address','mailing_address')
        THEN 'object' ELSE 'string' END;
    IF NEW.value='null'::jsonb OR jsonb_typeof(NEW.value) IS DISTINCT FROM edit_type
       OR octet_length(NEW.value::text)>1024
    THEN RAISE EXCEPTION 'Census addition has no value of its field''s shape'
        USING ERRCODE='23514'; END IF;
    -- The comparison validates the shape and raises on a bad one; a blank
    -- value has an empty key (a phone's display and an address's street
    -- line are read the same way), as 0128's edit check reads one.
    edit_key := public.stewardship_response_comparison_v1(NEW.field,NEW.value);
    edit_text := CASE
        WHEN NEW.field IN ('home_phone','mobile_phone','work_phone')
        THEN public.stewardship_response_comparison_v1('nickname',NEW.value->'display')
        WHEN NEW.field IN ('home_address','mailing_address')
        THEN edit_key::jsonb->>'line1'
        ELSE edit_key END;
    IF edit_key IS NULL OR coalesce(edit_text,'')=''
    THEN RAISE EXCEPTION 'Census addition must not be blank' USING ERRCODE='23514'; END IF;
    -- Added in place of the same Family's open ignored census change.
    IF NEW.in_place_of_id IS NOT NULL THEN
        SELECT p.* INTO replaced FROM public.stewardship_proposed_change p
            JOIN public.stewardship_submission s ON s.id=p.submission_id
            WHERE p.id=NEW.in_place_of_id AND s.family_id=family.id AND s.mode='live';
        IF replaced.id IS NULL OR replaced.decision<>'ignored'
           OR replaced.handling NOT IN ('api','manual')
           OR replaced.execution NOT IN ('pending','conflict','failed')
        THEN RAISE EXCEPTION 'Census addition replaces no ignored change'
            USING ERRCODE='23514'; END IF;
    END IF;
    -- One open change per field: an open addition, or the Family's own
    -- change that is not ignored, is decided with its own icons.
    IF EXISTS (SELECT 1 FROM public.stewardship_census_addition a
           WHERE a.family_id=NEW.family_id AND a.entity_kind=NEW.entity_kind
             AND a.entity_key=NEW.entity_key AND a.field=NEW.field
             -- Open: no later step yet, or one Back to review reopened
             -- (#1840).
             AND coalesce((SELECT r.action FROM public.stewardship_census_addition_resolution r
                 WHERE r.addition_id=a.id ORDER BY r.sequence DESC LIMIT 1),'reopened')='reopened')
       OR EXISTS (SELECT 1 FROM public.stewardship_proposed_change p
           JOIN public.stewardship_submission s ON s.id=p.submission_id
           WHERE s.family_id=NEW.family_id AND s.mode='live'
             AND p.entity_kind=NEW.entity_kind AND p.entity_key=NEW.entity_key
             AND p.field=NEW.field AND p.decision<>'ignored'
             AND p.execution IN ('pending','conflict','queued','failed'))
    THEN RAISE EXCEPTION 'Census addition duplicates an open change'
        USING ERRCODE='23514'; END IF;
    -- ParishSoft's value of the field now, from the current snapshot, as
    -- the census source reads it: the baseline a refresh re-evaluates the
    -- addition against. Database-owned, as attribution time is.
    SELECT c.snapshot_id INTO NEW.baseline_source_id
        FROM public.stewardship_source_current c WHERE c.singleton;
    NEW.baseline := CASE NEW.entity_kind
        WHEN 'family' THEN public.stewardship_response_household_source_v1(
            NEW.baseline_source_id,NEW.family_id,NEW.entity_key,NEW.field)
        ELSE public.stewardship_response_field_source_v1(
            NEW.baseline_source_id,NEW.family_id,NEW.entity_key,NEW.field) END;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_census_addition_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid; latest public.stewardship_census_addition_resolution%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Census addition resolutions are immutable' USING ERRCODE='23514';
    END IF;
    SELECT f.campaign_id INTO campaign FROM public.stewardship_census_addition a
        JOIN public.stewardship_family_campaign f ON f.id=a.family_id
        WHERE a.id=NEW.addition_id;
    IF campaign IS NULL
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,false)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR length(NEW.note)>2000
    THEN RAISE EXCEPTION 'Census addition resolution lacks current authority or state'
        USING ERRCODE='23514'; END IF;
    -- Which step may follow which (#1840): an open addition (no step yet,
    -- or reopened) is entered or withdrawn; an entered one is only
    -- reopened, by Back to review, with a note; a withdrawn one is history.
    -- The next sequence, unique per addition, refuses a second step made
    -- at the same time. (No CASE here: an IF condition ends at its first
    -- THEN.)
    SELECT * INTO latest FROM public.stewardship_census_addition_resolution r
        WHERE r.addition_id=NEW.addition_id ORDER BY r.sequence DESC LIMIT 1;
    IF NEW.sequence IS DISTINCT FROM coalesce(latest.sequence,0)+1
       OR (coalesce(latest.action,'reopened')='reopened'
           AND NEW.action NOT IN ('entered','withdrawn'))
       OR (latest.action='entered' AND (NEW.action<>'reopened' OR btrim(NEW.note)=''))
       OR latest.action='withdrawn'
    THEN RAISE EXCEPTION 'Census addition step does not follow its latest step'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_census_addition_resolution_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
BEGIN
    SELECT f.campaign_id INTO campaign FROM public.stewardship_census_addition a
        JOIN public.stewardship_family_campaign f ON f.id=a.family_id
        WHERE a.id=NEW.addition_id;
    -- The addition's version before and after this step (#1840).
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='census_change_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.sequence,'after_version',NEW.sequence+1))
    THEN RAISE EXCEPTION 'Census addition resolution requires its audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='stewardship_census_addition_resolution'
              AND column_name='sequence' AND data_type='integer' AND is_nullable='NO'
              AND column_default IS NULL)
       OR EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_census_addition_resolution'::regclass
              AND contype='u' AND conkey=ARRAY[(SELECT attnum FROM pg_attribute
                  WHERE attrelid='public.stewardship_census_addition_resolution'::regclass
                    AND attname='addition_id')]::int2[])
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_census_addition_resolution'::regclass
              AND conname='census_addition_resolution_sequence' AND contype='u')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_census_addition_resolution'::regclass
              AND conname='census_addition_resolution_identity'
              AND pg_get_constraintdef(oid) LIKE '%''reopened''%'
              AND pg_get_constraintdef(oid) LIKE '%sequence >= 1%')
       OR NOT EXISTS (SELECT 1 FROM pg_index
            WHERE indexrelid='public.stewardship_census_addition_resolution_addition_id_60dcd2ac'::regclass)
    THEN
        RAISE EXCEPTION 'Migration 0211 did not change the census addition resolutions';
    END IF;
    IF (SELECT count(*) FROM pg_proc
            WHERE NOT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND ((oid='public.stewardship_census_addition_guard_v1()'::regprocedure
                    AND prosrc LIKE '%ORDER BY r.sequence DESC LIMIT 1),''reopened'')=''reopened'')%')
                OR (oid='public.stewardship_census_addition_resolution_guard_v1()'::regprocedure
                    AND prosrc LIKE '%Census addition step does not follow its latest step%'
                    AND prosrc LIKE '%(latest.action=''entered'' AND (NEW.action<>''reopened'' OR btrim(NEW.note)=''''))%')
                OR (oid='public.stewardship_census_addition_resolution_effect_v1()'::regprocedure
                    AND prosrc LIKE '%''before_version'',NEW.sequence,''after_version'',NEW.sequence+1%')))<>3
    THEN
        RAISE EXCEPTION 'Migration 0211 did not replace the census addition guards';
    END IF;
END $check$;
