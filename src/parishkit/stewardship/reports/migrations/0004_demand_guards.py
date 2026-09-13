"""Keep pending events separate from frozen, fenced interactive build claims."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION stewardship_fact_demand_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE requested_changed boolean;
        claim_changed boolean;
        target stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Fact demand requires an owning retention workflow'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_source_snapshot
        WHERE id=NEW.requested_source_id AND state='promoted'
            AND generation=NEW.requested_source_generation)
        OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration
            WHERE id=NEW.requested_timezone_configuration_id
                AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Fact demand inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    IF NEW.pending_due_at IS NOT NULL AND NEW.pending_due_at IS DISTINCT FROM
        LEAST(NEW.pending_last_at+interval '5 seconds',
              NEW.pending_first_at+interval '30 seconds') THEN
        RAISE EXCEPTION 'Fact demand debounce window is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.pending_revision<>1 OR NEW.pending_first_at IS NULL
            OR NEW.pending_first_at<>NEW.pending_last_at
            OR NEW.claimed_revision<>0 OR NEW.claimed_generation_id IS NOT NULL THEN
            RAISE EXCEPTION 'Fact demand must begin pending' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    requested_changed := ROW(NEW.requested_source_id,
        NEW.requested_source_generation,NEW.requested_submission_watermark,
        NEW.requested_timezone_configuration_id,NEW.requested_through_date)
        IS DISTINCT FROM ROW(OLD.requested_source_id,
        OLD.requested_source_generation,OLD.requested_submission_watermark,
        OLD.requested_timezone_configuration_id,OLD.requested_through_date);
    claim_changed := ROW(NEW.claimed_revision,NEW.claimed_generation_id,
        NEW.claimed_task_id,NEW.claimed_task_fence,NEW.claimed_worker_id)
        IS DISTINCT FROM ROW(OLD.claimed_revision,OLD.claimed_generation_id,
        OLD.claimed_task_id,OLD.claimed_task_fence,OLD.claimed_worker_id);
    IF requested_changed THEN
        IF claim_changed OR NEW.pending_revision<>OLD.pending_revision+1
            OR NEW.requested_source_generation<OLD.requested_source_generation
            OR NEW.requested_submission_watermark<OLD.requested_submission_watermark
            OR NEW.requested_through_date<OLD.requested_through_date
            OR NEW.pending_first_at IS DISTINCT FROM
                COALESCE(OLD.pending_first_at,NEW.pending_last_at)
            OR NEW.pending_last_at IS NULL
            OR NEW.pending_last_at<OLD.pending_last_at THEN
            RAISE EXCEPTION 'Fact event must preserve independent claimed work'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.pending_revision<>OLD.pending_revision OR NOT claim_changed THEN
        RAISE EXCEPTION 'Fact demand change requires a new event or claim transition'
            USING ERRCODE='23514';
    ELSIF OLD.claimed_generation_id IS NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_revision<>OLD.pending_revision
            OR OLD.pending_due_at IS NULL OR OLD.pending_due_at>clock_timestamp()
            OR NEW.pending_first_at IS NOT NULL
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR ROW(target.campaign_id,target.population_scope,target.source_id,
                target.submission_watermark,target.timezone_configuration_id,
                target.through_date) IS DISTINCT FROM ROW(NEW.campaign_id,
                NEW.population_scope,NEW.requested_source_id,
                NEW.requested_submission_watermark,
                NEW.requested_timezone_configuration_id,NEW.requested_through_date)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact claim must freeze exactly its due input window'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.claimed_generation_id IS NOT NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_generation_id<>OLD.claimed_generation_id
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact recovery must transfer only abandoned exact work'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.claimed_generation_id IS NOT NULL
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR NOT stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set
                WHERE id=OLD.claimed_generation_id AND state='ready') THEN
            RAISE EXCEPTION 'Fact completion must preserve newer pending demand'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_demand_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_fact_demand FOR EACH ROW
EXECUTE FUNCTION stewardship_fact_demand_guard();
"""

REVERSE = """
DROP TRIGGER fact_demand_guard ON stewardship_fact_demand;
DROP FUNCTION stewardship_fact_demand_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_reports",
            "0003_remove_campaignfactrebuilddemand_fact_demand_claim_shape_and_more",
        )
    ]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
