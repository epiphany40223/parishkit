-- Frozen forward migration file 0129 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): census
-- Add on the review worklist (#1710). This file is installed by its Django
-- migration in the responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- Staff and Administrators may Add a census change no Family answer asked
-- for (docs/specs/stewardship/review-pipeline/spec.md, "Reviewed stage").
-- Every census change so far is a stewardship_proposed_change row, which
-- only the Family submit path creates, from the Family's immutable answer;
-- widening that path's guard mid-campaign is the riskiest change the spec
-- names. So an addition has tables of its own (new, migration-owned), and
-- nothing that exists is replaced or touched:
--   * stewardship_census_addition: one parish-added change (who, when, the
--     note, the Family, a Member field or Family address, its value, and
--     optionally the Family's ignored change it was added in place of);
--   * stewardship_census_addition_resolution: its one later step, Entered
--     by hand or withdrawn (Back to review), at most one per addition.
-- Each guard admits INSERT only. An addition comes from a current Staff
-- member or Administrator, in a campaign that admits changes, for a Family
-- with a live response; it names a Member that the current ParishSoft data
-- lists as current in that Family, or the Family itself for an address; it
-- is for a field a value can be typed for (the fields the worklist's Edit
-- takes), with a value of that field's shape (as 0128's edit check reads
-- one), not blank; the change it is added in place of is that Family's
-- open ignored census change; and no open change for the same field is
-- waiting already (an open addition, or a Family's change not ignored),
-- since that one is decided with its own icons. The guard records
-- ParishSoft's value of the field from the current snapshot (baseline,
-- baseline_source_id), the baseline a refresh re-evaluates the addition
-- against (#1689). A resolution comes from a
-- current Staff member or Administrator, in a campaign that admits
-- changes. A deferred check on each requires its census_change_updated
-- audit event, as the review and resolution rows have theirs. Neither
-- function is SECURITY DEFINER. The file ends with a DO block that refuses
-- to commit unless the tables, their guards and triggers are installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_census_addition" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "entity_kind" varchar(20) NOT NULL, "entity_key" varchar(36) NOT NULL, "field" varchar(40) NOT NULL, "value" jsonb NOT NULL, "request_key" uuid NOT NULL, "note" text NOT NULL, "baseline" jsonb NULL, "baseline_source_id" uuid NULL, "family_id" uuid NOT NULL, "in_place_of_id" uuid NULL, CONSTRAINT "census_addition_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "census_addition_identity" CHECK (("actor_id" IS NOT NULL AND ((entity_kind)::text = ANY ((ARRAY['family'::character varying, 'member'::character varying])::text[])) AND NOT ("entity_key" = '') AND NOT ("field" = ''))));
CREATE TABLE "stewardship_census_addition_resolution" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "action" varchar(12) NOT NULL, "request_key" uuid NOT NULL, "note" text NOT NULL, "addition_id" uuid NOT NULL UNIQUE, CONSTRAINT "census_addition_resolution_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "census_addition_resolution_identity" CHECK (("actor_id" IS NOT NULL AND ((action)::text = ANY ((ARRAY['entered'::character varying, 'withdrawn'::character varying])::text[])))));
ALTER TABLE "stewardship_census_addition" ADD CONSTRAINT "stewardship_census_a_family_id_02c8104c_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_census_addition" ADD CONSTRAINT "stewardship_census_a_in_place_of_id_4bd55dee_fk_stewardsh" FOREIGN KEY ("in_place_of_id") REFERENCES "stewardship_proposed_change" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_census_addition_correlation_id_7badeaf4" ON "stewardship_census_addition" ("correlation_id");
CREATE INDEX "stewardship_census_addition_family_id_02c8104c" ON "stewardship_census_addition" ("family_id");
CREATE INDEX "stewardship_census_addition_in_place_of_id_4bd55dee" ON "stewardship_census_addition" ("in_place_of_id");
ALTER TABLE "stewardship_census_addition_resolution" ADD CONSTRAINT "stewardship_census_a_addition_id_60dcd2ac_fk_stewardsh" FOREIGN KEY ("addition_id") REFERENCES "stewardship_census_addition" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_census_addition_resolution_correlation_id_364f5897" ON "stewardship_census_addition_resolution" ("correlation_id");

CREATE FUNCTION public.stewardship_census_addition_guard_v1() RETURNS trigger
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
             AND NOT EXISTS (SELECT 1 FROM public.stewardship_census_addition_resolution r
                 WHERE r.addition_id=a.id))
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

CREATE FUNCTION public.stewardship_census_addition_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
BEGIN
    SELECT f.campaign_id INTO campaign FROM public.stewardship_family_campaign f
        WHERE f.id=NEW.family_id;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='census_change_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed','version',1))
    THEN RAISE EXCEPTION 'Census addition requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_census_addition_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
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
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_census_addition_resolution_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
BEGIN
    SELECT f.campaign_id INTO campaign FROM public.stewardship_census_addition a
        JOIN public.stewardship_family_campaign f ON f.id=a.family_id
        WHERE a.id=NEW.addition_id;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='census_change_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',1,'after_version',2))
    THEN RAISE EXCEPTION 'Census addition resolution requires its audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION public.stewardship_census_addition_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_census_addition_effect_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_census_addition_resolution_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_census_addition_resolution_effect_v1() FROM PUBLIC;

CREATE TRIGGER stewardship_census_addition_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_census_addition
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_census_addition_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_census_addition_effect
    AFTER INSERT ON public.stewardship_census_addition DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_census_addition_effect_v1();
CREATE TRIGGER stewardship_census_addition_resolution_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_census_addition_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_census_addition_resolution_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_census_addition_resolution_effect
    AFTER INSERT ON public.stewardship_census_addition_resolution
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_census_addition_resolution_effect_v1();

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_class
            WHERE oid IN ('public.stewardship_census_addition'::regclass,
                          'public.stewardship_census_addition_resolution'::regclass))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_census_addition_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%stewardship_member_source_status_v1(%'
              AND prosrc LIKE '%Census addition replaces no ignored change%'
              AND prosrc LIKE '%Census addition duplicates an open change%'
              AND prosrc LIKE '%NEW.baseline := CASE NEW.entity_kind%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_census_addition_resolution_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_admitted_v1(campaign,true)%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_proc
            WHERE oid IN ('public.stewardship_census_addition_effect_v1()'::regprocedure,
                    'public.stewardship_census_addition_resolution_effect_v1()'::regprocedure)
              AND prosrc LIKE '%census_change_updated%' AND NOT prosecdef)<>2
       OR (SELECT count(*) FROM pg_trigger t
            WHERE NOT t.tgisinternal AND t.tgenabled='O'
              AND ((t.tgrelid='public.stewardship_census_addition'::regclass
                    AND ((t.tgname='stewardship_census_addition_guard'
                          AND t.tgfoid='public.stewardship_census_addition_guard_v1()'::regprocedure)
                         OR (t.tgname='stewardship_census_addition_effect'
                          AND t.tgfoid='public.stewardship_census_addition_effect_v1()'::regprocedure
                          AND t.tgdeferrable AND t.tginitdeferred)))
                OR (t.tgrelid='public.stewardship_census_addition_resolution'::regclass
                    AND ((t.tgname='stewardship_census_addition_resolution_guard'
                          AND t.tgfoid='public.stewardship_census_addition_resolution_guard_v1()'::regprocedure)
                         OR (t.tgname='stewardship_census_addition_resolution_effect'
                          AND t.tgfoid='public.stewardship_census_addition_resolution_effect_v1()'::regprocedure
                          AND t.tgdeferrable AND t.tginitdeferred)))))<>4
    THEN
        RAISE EXCEPTION 'Census additions are not installed as declared';
    END IF;
END $check$;
