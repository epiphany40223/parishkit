"""Pair every derived-generation deletion with immutable, live-task evidence."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_fact_compaction_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target stewardship_daily_fact_set%ROWTYPE;
BEGIN
    SELECT * INTO target FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF NOT FOUND OR NOT stewardship_fact_disposable(target.id)
        OR NOT stewardship_fact_live(NEW.task_id,NEW.task_fence,NEW.worker_id)
        OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_generation,
            NEW.submission_watermark,NEW.timezone_configuration_id,
            NEW.through_date,NEW.row_count) IS DISTINCT FROM
            ROW(target.campaign_id,target.population_scope,target.source_generation,
            target.submission_watermark,target.timezone_configuration_id,
            target.through_date,target.expected_count) THEN
        RAISE EXCEPTION 'Fact cleanup evidence requires exact disposable inputs'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_compaction_guard BEFORE INSERT
ON stewardship_fact_compaction FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_compaction_guard();

CREATE FUNCTION stewardship_fact_compaction_pair_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='stewardship_daily_fact_set' THEN
        IF NOT EXISTS(SELECT 1 FROM stewardship_fact_compaction
            WHERE fact_set_id=OLD.id) THEN
            RAISE EXCEPTION 'Fact deletion requires retained cleanup evidence'
                USING ERRCODE='23514';
        END IF;
    ELSIF EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id) THEN
        RAISE EXCEPTION 'Fact cleanup evidence requires complete deletion'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER fact_compaction_pair_guard AFTER INSERT
ON stewardship_fact_compaction DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_compaction_pair_guard();

CREATE FUNCTION stewardship_fact_source_pin_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.parent_kind='facts' AND EXISTS(SELECT 1 FROM stewardship_daily_fact_set
        WHERE id=OLD.parent_id AND source_id=OLD.snapshot_id) THEN
        RAISE EXCEPTION 'Retained facts still require their exact source input'
            USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER fact_source_pin_guard BEFORE DELETE ON stewardship_source_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_fact_source_pin_guard();
CREATE CONSTRAINT TRIGGER fact_deletion_pair_guard AFTER DELETE
ON stewardship_daily_fact_set DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_compaction_pair_guard();
"""

REVERSE = """
DROP TRIGGER fact_source_pin_guard ON stewardship_source_pin;
DROP FUNCTION stewardship_fact_source_pin_guard();
DROP TRIGGER fact_deletion_pair_guard ON stewardship_daily_fact_set;
DROP TRIGGER fact_compaction_pair_guard ON stewardship_fact_compaction;
DROP FUNCTION stewardship_fact_compaction_pair_guard();
DROP TRIGGER fact_compaction_guard ON stewardship_fact_compaction;
DROP FUNCTION stewardship_fact_compaction_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_reports", "0005_factcompactionrecord")]
    operations = [
        immutable_guard_v1("stewardship_fact_compaction"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
