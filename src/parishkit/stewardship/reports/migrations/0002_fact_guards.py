"""Enforce complete immutable generations and atomic protection from compaction."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_fact_live(task uuid, fence bigint, worker uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=task
        AND t.state='running' AND t.fence=fence AND t.worker_id=worker
        AND t.lease_expires_at > clock_timestamp());
$$;

CREATE FUNCTION stewardship_fact_disposable(identifier uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_fact_set old
        JOIN stewardship_daily_fact_set newer ON newer.campaign_id=old.campaign_id
            AND newer.population_scope=old.population_scope AND newer.state='ready'
            AND newer.id<>old.id AND newer.created_at>=old.created_at
            AND newer.source_generation>=old.source_generation
            AND newer.submission_watermark>=old.submission_watermark
            AND newer.through_date>=old.through_date
        WHERE old.id=identifier AND old.state='ready')
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pointer WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand
        WHERE claimed_generation_id=identifier);
$$;

CREATE FUNCTION stewardship_fact_set_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE projection stewardship_campaign_configuration%ROWTYPE;
        input_source stewardship_source_snapshot%ROWTYPE;
        first_day date;
        last_day date;
        expected integer;
        actual bigint;
BEGIN
    IF TG_OP='DELETE' THEN
        IF NOT stewardship_fact_disposable(OLD.id) THEN
            RAISE EXCEPTION 'Fact generation is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT stewardship_fact_live(NEW.task_id,NEW.task_fence,NEW.worker_id) THEN
        RAISE EXCEPTION 'Fact generation requires its live fenced builder'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.state<>'building' THEN
        RAISE EXCEPTION 'Fact generation must begin building' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.state='ready' OR NOT (
            (OLD.state='building' AND NEW.state IN ('building','ready','failed'))
            OR (OLD.state='failed' AND NEW.state='building')) THEN
            RAISE EXCEPTION 'Fact generation transition is invalid'
                USING ERRCODE='23514';
        END IF;
        IF ROW(NEW.task_id,NEW.task_fence,NEW.worker_id)
            IS DISTINCT FROM ROW(OLD.task_id,OLD.task_fence,OLD.worker_id)
            AND stewardship_fact_live(OLD.task_id,OLD.task_fence,OLD.worker_id) THEN
            RAISE EXCEPTION 'Live fact ownership cannot be replaced'
                USING ERRCODE='23514';
        END IF;
    END IF;
    SELECT * INTO projection FROM stewardship_campaign_configuration
        WHERE id=NEW.timezone_configuration_id;
    SELECT * INTO input_source FROM stewardship_source_snapshot WHERE id=NEW.source_id;
    IF projection.record_id IS DISTINCT FROM NEW.campaign_id
        OR input_source.state IS DISTINCT FROM 'promoted'
        OR input_source.generation IS DISTINCT FROM NEW.source_generation THEN
        RAISE EXCEPTION 'Fact inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    last_day := LEAST(projection.end_date,NEW.through_date);
    first_day := projection.start_date;
    expected := GREATEST(0,last_day-first_day+1);
    IF expected=0 THEN first_day:=NULL; last_day:=NULL; END IF;
    IF NEW.expected_count<>expected OR NEW.first_date IS DISTINCT FROM first_day
        OR NEW.last_date IS DISTINCT FROM last_day THEN
        RAISE EXCEPTION 'Fact date bounds differ from frozen campaign inputs'
            USING ERRCODE='23514';
    END IF;
    IF NEW.population_scope='current' AND (
        input_source.compacted_at IS NOT NULL OR NOT EXISTS(
            SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=NEW.source_id
                AND parent_kind='facts' AND parent_id=NEW.id)) THEN
        RAISE EXCEPTION 'Current facts require a retained exact source input'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state='ready' THEN
        SELECT count(*) INTO actual FROM stewardship_daily_fact
            WHERE fact_set_id=NEW.id;
        IF actual<>NEW.expected_count OR NEW.ready_at > clock_timestamp()
            OR NEW.failure_code<>'' OR EXISTS (
                SELECT 1 FROM (
                    SELECT cumulative_responses, sum(first_responses) OVER (
                        ORDER BY local_date) AS expected_cumulative
                    FROM stewardship_daily_fact WHERE fact_set_id=NEW.id
                ) counts WHERE cumulative_responses<>expected_cumulative
            ) THEN
            RAISE EXCEPTION 'Fact generation is incomplete or inconsistent'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_set_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_daily_fact_set FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_set_guard();

CREATE FUNCTION stewardship_fact_day_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent stewardship_daily_fact_set%ROWTYPE;
        source stewardship_source_snapshot%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'Fact rows are immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN
        SELECT * INTO parent FROM stewardship_daily_fact_set
            WHERE id=OLD.fact_set_id FOR UPDATE;
        IF NOT stewardship_fact_disposable(parent.id) THEN
            RAISE EXCEPTION 'Fact rows are protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO parent FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF parent.state IS DISTINCT FROM 'building' OR NOT
        stewardship_fact_live(parent.task_id,parent.task_fence,parent.worker_id)
        OR parent.expected_count=0 OR NEW.local_date<parent.first_date
        OR NEW.local_date>parent.last_date THEN
        RAISE EXCEPTION 'Fact row is outside its owned building generation'
            USING ERRCODE='23514';
    END IF;
    IF NEW.population_available THEN
        SELECT * INTO source FROM stewardship_source_snapshot
            WHERE generation=NEW.source_generation AND state='promoted';
        IF NOT FOUND OR source.promoted_at IS DISTINCT FROM NEW.source_as_of
            OR source.generation>parent.source_generation
            OR (parent.population_scope='current' AND source.id<>parent.source_id) THEN
            RAISE EXCEPTION 'Fact row source cutoff is inconsistent'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_day_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_daily_fact FOR EACH ROW EXECUTE FUNCTION stewardship_fact_day_guard();

CREATE FUNCTION stewardship_fact_reference_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target stewardship_daily_fact_set%ROWTYPE;
        prior stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    SELECT * INTO target FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF target.state IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'Fact reference requires a retained ready generation'
            USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_fact_pointer' THEN
        IF target.campaign_id<>NEW.campaign_id
            OR target.population_scope<>NEW.population_scope
            OR NOT EXISTS(SELECT 1 FROM stewardship_campaign
                WHERE id=NEW.campaign_id
                    AND active_configuration_id=target.timezone_configuration_id) THEN
            RAISE EXCEPTION 'Fact pointer requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' THEN
            SELECT * INTO prior FROM stewardship_daily_fact_set
                WHERE id=OLD.fact_set_id;
            IF target.source_generation<prior.source_generation
                OR target.submission_watermark<prior.submission_watermark
                OR target.through_date<prior.through_date THEN
                RAISE EXCEPTION 'Fact pointer cannot regress' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_pointer_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_fact_pointer FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_reference_guard();
CREATE TRIGGER fact_pin_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_fact_pin FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_reference_guard();

CREATE FUNCTION stewardship_fact_delete_pair_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=OLD.fact_set_id) THEN
        RAISE EXCEPTION 'Fact compaction must remove a whole disposable generation'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER fact_delete_pair_guard AFTER DELETE
ON stewardship_daily_fact DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_delete_pair_guard();
"""

REVERSE = """
DROP TRIGGER fact_delete_pair_guard ON stewardship_daily_fact;
DROP FUNCTION stewardship_fact_delete_pair_guard();
DROP TRIGGER fact_pin_guard ON stewardship_fact_pin;
DROP TRIGGER fact_pointer_guard ON stewardship_fact_pointer;
DROP FUNCTION stewardship_fact_reference_guard();
DROP TRIGGER fact_day_guard ON stewardship_daily_fact;
DROP FUNCTION stewardship_fact_day_guard();
DROP TRIGGER fact_set_guard ON stewardship_daily_fact_set;
DROP FUNCTION stewardship_fact_set_guard();
DROP FUNCTION stewardship_fact_disposable(uuid);
DROP FUNCTION stewardship_fact_live(uuid,bigint,uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_reports", "0001_initial")]
    operations = [
        mutable_guard_v1(
            "stewardship_daily_fact_set",
            frozen_fields=(
                "campaign_id",
                "population_scope",
                "source_id",
                "source_generation",
                "submission_watermark",
                "timezone_configuration_id",
                "through_date",
                "first_date",
                "last_date",
                "expected_count",
            ),
        ),
        mutable_guard_v1(
            "stewardship_fact_pointer",
            frozen_fields=("campaign_id", "population_scope"),
        ),
        mutable_guard_v1(
            "stewardship_fact_pin",
            frozen_fields=("fact_set_id", "parent_kind", "parent_id"),
        ),
        mutable_guard_v1(
            "stewardship_fact_demand", frozen_fields=("campaign_id", "population_scope")
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
