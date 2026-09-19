-- Fresh-install Staff workflow history. This is not an upgrade migration.
CREATE TABLE "stewardship_information_revision" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "item_id" uuid NOT NULL, "expected_version" bigint NOT NULL CHECK ("expected_version" >= 0), "request_key" uuid NOT NULL, "follow_up_needed" boolean NOT NULL, "followed_up" boolean NOT NULL, "confirm_clear" boolean NOT NULL, "notes" text NOT NULL, "followed_up_at" timestamp with time zone NULL, "followed_up_by_id" uuid NULL, CONSTRAINT "information_revision_version" UNIQUE ("item_id", "expected_version"), CONSTRAINT "information_revision_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "information_revision_identity" CHECK (("actor_id" IS NOT NULL AND "expected_version" >= 1)), CONSTRAINT "information_revision_completion" CHECK ((("followed_up" AND "followed_up_at" IS NOT NULL AND "followed_up_by_id" IS NOT NULL) OR (NOT "followed_up" AND "followed_up_at" IS NULL AND "followed_up_by_id" IS NULL))));
ALTER TABLE "stewardship_information_revision" ADD CONSTRAINT "stewardship_informat_item_id_bc0eee5e_fk_stewardsh" FOREIGN KEY ("item_id") REFERENCES "stewardship_additional_information" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_information_revision_correlation_id_1e5495e6" ON "stewardship_information_revision" ("correlation_id");
CREATE INDEX "stewardship_information_revision_item_id_bc0eee5e" ON "stewardship_information_revision" ("item_id");

CREATE FUNCTION stewardship_information_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE item stewardship_additional_information%ROWTYPE;
        prior stewardship_information_revision%ROWTYPE; campaign uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Staff follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO item FROM stewardship_additional_information WHERE id=NEW.item_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=item.submission_id AND mode='live';
    IF item.id IS NULL OR campaign IS NULL
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM item.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.notes)>5000
       OR (item.followed_up_at IS NOT NULL AND NOT NEW.followed_up AND NOT NEW.confirm_clear)
    THEN RAISE EXCEPTION 'Staff follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    SELECT * INTO prior FROM stewardship_information_revision
        WHERE item_id=item.id ORDER BY expected_version DESC LIMIT 1;
    -- Attribution is database-owned. Notes-only edits preserve who completed the
    -- work and when; clearing removes the current projection, never old history.
    NEW.created_at:=statement_timestamp();
    NEW.followed_up_at:=NULL;
    NEW.followed_up_by_id:=NULL;
    IF NEW.followed_up THEN
        IF item.followed_up_at IS NOT NULL THEN
            IF prior.followed_up_at IS DISTINCT FROM item.followed_up_at
               OR prior.followed_up_by_id IS NULL THEN
                RAISE EXCEPTION 'Follow-up attribution history is unavailable' USING ERRCODE='23514';
            END IF;
            NEW.followed_up_at:=prior.followed_up_at;
            NEW.followed_up_by_id:=prior.followed_up_by_id;
        ELSE
            NEW.followed_up_at:=NEW.created_at;
            NEW.followed_up_by_id:=NEW.actor_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_information_projection_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.follow_up_needed OR NEW.followed_up_at IS NOT NULL THEN
            RAISE EXCEPTION 'A Family submission cannot set Staff follow-up' USING ERRCODE='23514';
        END IF;
    ELSIF ROW(NEW.follow_up_needed,NEW.followed_up_at)
        IS DISTINCT FROM ROW(OLD.follow_up_needed,OLD.followed_up_at) THEN
        IF ROW(NEW.disposition,NEW.replacement_id) IS DISTINCT FROM ROW(OLD.disposition,OLD.replacement_id)
           OR NOT EXISTS(SELECT 1 FROM stewardship_information_revision r
               WHERE r.item_id=NEW.id AND r.expected_version=OLD.version
                 AND NEW.version=OLD.version+1
                 AND r.follow_up_needed=NEW.follow_up_needed
                 AND r.followed_up_at IS NOT DISTINCT FROM NEW.followed_up_at)
        THEN RAISE EXCEPTION 'Staff follow-up projection requires its paired history'
            USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_information_revision_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE item stewardship_additional_information%ROWTYPE;
        latest stewardship_information_revision%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO item FROM stewardship_additional_information WHERE id=NEW.item_id;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=item.submission_id;
    SELECT * INTO latest FROM stewardship_information_revision
        WHERE item_id=NEW.item_id ORDER BY expected_version DESC LIMIT 1;
    IF item.version<=NEW.expected_version
       OR latest.follow_up_needed IS DISTINCT FROM item.follow_up_needed
       OR latest.followed_up_at IS DISTINCT FROM item.followed_up_at
       OR NOT EXISTS(SELECT 1 FROM stewardship_audit_event a
           JOIN stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='information_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1))
    THEN RAISE EXCEPTION 'Staff follow-up requires its projection and audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_information_revision_guard
    BEFORE INSERT OR UPDATE OR DELETE ON stewardship_information_revision
    FOR EACH ROW EXECUTE FUNCTION stewardship_information_revision_guard_v1();
CREATE TRIGGER stewardship_information_projection_guard
    BEFORE INSERT OR UPDATE ON stewardship_additional_information
    FOR EACH ROW EXECUTE FUNCTION stewardship_information_projection_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_information_revision_effect
    AFTER INSERT ON stewardship_information_revision DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_information_revision_effect_v1();
