-- The Member an Administrator selected when confirming a Chairperson
-- suggestion: one immutable row per seeded assignment record of one
-- confirmation request, written by the web tier beside the request and read
-- by the installer inside the activation transaction to record the seed's
-- retained identity evidence. The applied YAML never names a Member.
CREATE TABLE "stewardship_chair_seed_intent" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "request_id" uuid NOT NULL,
    "assignment_record_id" uuid NOT NULL UNIQUE,
    "organization_id" bigint NOT NULL CHECK ("organization_id" >= 0),
    "member_duid" bigint NOT NULL CHECK ("member_duid" >= 0),
    CONSTRAINT "chair_seed_intent_identity" CHECK ((("actor_id" IS NOT NULL) AND ("member_duid" > 0) AND ("member_duid" < '2147483648'::bigint) AND ("organization_id" > 0) AND ("organization_id" < '2147483648'::bigint)))
);
ALTER TABLE "stewardship_chair_seed_intent" ADD CONSTRAINT "stewardship_chair_se_request_id_aeb1a193_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_config_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_chair_seed_intent_correlation_id_e940a084" ON "stewardship_chair_seed_intent" ("correlation_id");
CREATE INDEX "stewardship_chair_seed_intent_request_id_aeb1a193" ON "stewardship_chair_seed_intent" ("request_id");

CREATE FUNCTION stewardship_chair_seed_intent_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_chair_seed_intent_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_chair_seed_intent
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_seed_intent_immutable_v1();

-- An intent binds to the confirmation request it was recorded with: the same
-- actor, the confirmation schema, and an added seeded assignment record with
-- that identity, bound to the request's own operation, in the request's own
-- patch. Nothing else may carry a selected Member into the installer.
CREATE FUNCTION stewardship_chair_seed_intent_binding_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_config_request%ROWTYPE;
BEGIN
    SELECT * INTO request FROM stewardship_config_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR request.request_schema<>'chair-seed-patch-v9'
       OR request.actor_id IS DISTINCT FROM NEW.actor_id
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(request.patch) item
            WHERE item->>'operation'='add' AND item->>'section'='login_rules'
              AND (item->>'id')::uuid=NEW.assignment_record_id
              AND item->'values'->>'kind'='assignment'
              AND item->'values'->>'source'='chair-seed'
              AND item->'values'->>'operation_id'=request.id::text) THEN
        RAISE EXCEPTION 'Chair seed intent requires its confirmation request'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_chair_seed_intent_binding_guard_v1
AFTER INSERT ON stewardship_chair_seed_intent DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_seed_intent_binding_v1();
