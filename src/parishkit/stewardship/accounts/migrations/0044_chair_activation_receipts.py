"""Replace the temporary activity barrier with exact atomic reconciliation proof."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION stewardship_require_chair_receipt_v1(configuration uuid, snapshot uuid)
RETURNS void LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE receipt stewardship_chair_reconciliation%ROWTYPE;
BEGIN
    SELECT * INTO receipt FROM stewardship_chair_reconciliation
        WHERE configuration_id=configuration AND snapshot_id=snapshot;
    IF receipt.id IS NULL OR receipt.decisions IS DISTINCT FROM
        stewardship_chair_decisions_v1(configuration) THEN
        RAISE EXCEPTION 'Seeded assignment reconciliation is incomplete'
            USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(receipt.decisions) decision
        LEFT JOIN stewardship_assignment_overlay overlay
          ON overlay.assignment_record_id=(decision->>'assignment_record_id')::uuid
        WHERE overlay.id IS NULL OR overlay.active IS DISTINCT FROM
                  (decision->>'active')::boolean
          OR overlay.source_snapshot_id IS DISTINCT FROM snapshot
          OR overlay.reason IS DISTINCT FROM decision->>'reason'
          OR (decision->>'active')::boolean = EXISTS (
              SELECT 1 FROM stewardship_chair_review review
              WHERE review.assignment_record_id=
                  (decision->>'assignment_record_id')::uuid
                AND review.closed_by_id IS NULL))
       OR EXISTS (SELECT 1 FROM stewardship_chair_review review
           WHERE review.closed_by_id IS NULL AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(receipt.decisions) decision
               WHERE (decision->>'assignment_record_id')::uuid=
                   review.assignment_record_id
                 AND decision->'active'='false'::jsonb)) THEN
        RAISE EXCEPTION 'Seeded assignment reconciliation effects are incomplete'
            USING ERRCODE='23514';
    END IF;
END;
$$;

DROP TRIGGER stewardship_ministry_activation_v1 ON stewardship_config_activation;
CREATE OR REPLACE FUNCTION stewardship_ministry_activation_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE before_records jsonb; after_records jsonb; snapshot uuid; seeded boolean;
BEGIN
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO before_records FROM stewardship_configuration_version
        WHERE id=NEW.predecessor_id;
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO after_records FROM stewardship_configuration_version
        WHERE id=NEW.configuration_id;
    SELECT snapshot_id INTO snapshot FROM stewardship_source_current;
    seeded := EXISTS (SELECT 1 FROM stewardship_ministry_assignment
        WHERE configuration_id=NEW.configuration_id AND source='chair-seed');
    IF snapshot IS NULL THEN
        IF seeded AND coalesce(before_records,'[]'::jsonb)
            IS DISTINCT FROM after_records THEN
            RAISE EXCEPTION 'Ministry activity requires seeded reconciliation'
                USING ERRCODE='23514';
        END IF;
    ELSIF seeded OR EXISTS (
        SELECT 1 FROM stewardship_chair_review WHERE closed_by_id IS NULL
    ) OR EXISTS (SELECT 1 FROM stewardship_chair_reconciliation
                 WHERE activation_id=NEW.id) THEN
        PERFORM stewardship_require_chair_receipt_v1(NEW.configuration_id,snapshot);
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER stewardship_ministry_activation_v1
AFTER INSERT ON stewardship_config_activation DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_ministry_activation_v1();

CREATE FUNCTION stewardship_source_chair_effects_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE configuration uuid;
BEGIN
    SELECT active_configuration_id INTO configuration
        FROM stewardship_system_configuration;
    IF EXISTS (SELECT 1 FROM stewardship_ministry_assignment
               WHERE configuration_id=configuration AND source='chair-seed')
       OR EXISTS (SELECT 1 FROM stewardship_chair_review
                  WHERE closed_by_id IS NULL) THEN
        PERFORM stewardship_require_chair_receipt_v1(configuration,NEW.snapshot_id);
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER stewardship_source_chair_effects
AFTER UPDATE ON stewardship_source_current DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_source_chair_effects_v1();
"""

REVERSE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_chair_reconciliation) THEN
        RAISE EXCEPTION 'Retained Chair reconciliation prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_source_chair_effects ON stewardship_source_current;
DROP FUNCTION stewardship_source_chair_effects_v1();
DROP TRIGGER stewardship_ministry_activation_v1 ON stewardship_config_activation;
CREATE OR REPLACE FUNCTION stewardship_ministry_activation_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE before_records jsonb; after_records jsonb;
BEGIN
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO before_records FROM stewardship_configuration_version
        WHERE id=NEW.predecessor_id;
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO after_records FROM stewardship_configuration_version
        WHERE id=NEW.configuration_id;
    IF coalesce(before_records,'[]'::jsonb) IS DISTINCT FROM after_records
       AND EXISTS (SELECT 1 FROM stewardship_ministry_assignment
                   WHERE configuration_id=NEW.configuration_id AND source='chair-seed')
    THEN
        RAISE EXCEPTION 'Ministry activity requires seeded assignment reconciliation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ministry_activation_v1
BEFORE INSERT ON stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION stewardship_ministry_activation_v1();
DROP FUNCTION stewardship_require_chair_receipt_v1(uuid,uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0043_chair_reconciliation_guards")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
