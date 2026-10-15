-- Frozen forward migration file 0105 (the repository-wide file sequence):
-- a Campaign setup step an Administrator kept as is (#1404).
-- This file is installed by its Django migration in the accounts app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The Campaign setup stepper read a step's completion only from changes
-- applied on its page, so a step whose defaults were acceptable could never
-- be completed. stewardship_campaign_setup_review (new, migration-owned)
-- records an Administrator's "Continue to <next step>" on an unchanged
-- step: one row per campaign, step and item (empty for the whole step, or
-- one page or email slot of Pages and emails), who and when. It is
-- insert-only. Its guard admits an INSERT only from a current
-- Administrator, for the current campaign while the system is in Testing
-- mode, the campaign is a draft and no go-live has started (the stepper's
-- own condition), and stamps the time. A deferred constraint trigger then
-- requires the campaign_setup_step_reviewed audit event naming the row,
-- its actor and its campaign. Rows are retained with their campaign: no
-- path deletes one (the campaign purge, when it lands, must inventory
-- them). Nothing else changes. The file ends with a DO block that refuses
-- to commit unless all of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_campaign_setup_review" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "step" varchar(16) NOT NULL, "item" varchar(64) NOT NULL, CONSTRAINT "campaign_setup_review_once" UNIQUE ("campaign_id", "step", "item"), CONSTRAINT "campaign_setup_review_actor" CHECK (("actor_id" IS NOT NULL)), CONSTRAINT "campaign_setup_review_step" CHECK (((step)::text = ANY ((ARRAY['settings'::character varying, 'share_options'::character varying, 'content'::character varying, 'schedules'::character varying, 'images'::character varying])::text[]))), CONSTRAINT "campaign_setup_review_item" CHECK ((((item)::text = ''::text) OR (((item)::text ~ '^(page|email):[a-z][a-z0-9_]{0,47}$'::text) AND ((step)::text = 'content'::text)))));
ALTER TABLE "stewardship_campaign_setup_review" ADD CONSTRAINT "stewardship_campaign_campaign_id_307212a7_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_campaign_setup_review_correlation_id_168c9957" ON "stewardship_campaign_setup_review" ("correlation_id");
CREATE INDEX "stewardship_campaign_setup_review_campaign_id_307212a7" ON "stewardship_campaign_setup_review" ("campaign_id");

CREATE FUNCTION public.stewardship_campaign_setup_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Campaign setup reviews are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.actor_id IS NULL
       -- An Administrator only, as every Campaign setup page requires.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
       -- The stepper's own condition (campaign_wizard.shown): the current
       -- campaign, a draft, in Testing mode, with no go-live started.
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration s
           JOIN public.stewardship_campaign c ON c.id=s.current_campaign_id
           WHERE c.id=NEW.campaign_id AND s.mode='testing' AND c.state='draft')
       OR EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials k
           WHERE k.campaign_id=NEW.campaign_id AND k.go_live_gate)
    THEN RAISE EXCEPTION 'Campaign setup review requires an Administrator and a Testing draft'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_campaign_setup_review_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           WHERE a.event_type='campaign_setup_step_reviewed' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=NEW.campaign_id)
    THEN RAISE EXCEPTION 'Campaign setup review requires its audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_campaign_setup_review_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_campaign_setup_review
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_setup_review_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_campaign_setup_review_effect
    AFTER INSERT ON public.stewardship_campaign_setup_review DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_setup_review_effect_v1();

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_campaign_setup_review'::regclass
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname IN ('stewardship_campaign_setup_review_guard_v1',
                                'stewardship_campaign_setup_review_effect_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_campaign_setup_review_effect'
              AND tgrelid='public.stewardship_campaign_setup_review'::regclass
              AND tgdeferrable AND tginitdeferred)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_campaign_setup_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,true)%'
              AND prosrc LIKE '%s.mode=''testing'' AND c.state=''draft''%'
              AND prosrc LIKE '%k.go_live_gate%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_campaign_setup_review_effect_v1()'::regprocedure
              AND prosrc LIKE '%event_type=''campaign_setup_step_reviewed''%')
    THEN
        RAISE EXCEPTION 'stewardship_campaign_setup_review is not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_campaign_setup_review'::regclass
              AND conname IN ('campaign_setup_review_once','campaign_setup_review_actor',
                              'campaign_setup_review_step','campaign_setup_review_item'))<>4
    THEN
        RAISE EXCEPTION 'stewardship_campaign_setup_review lacks its checks';
    END IF;
END $check$;
