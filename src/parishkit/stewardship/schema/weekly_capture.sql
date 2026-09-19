CREATE FUNCTION stewardship_weekly_observation_v1(campaign uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
WITH selected AS MATERIALIZED (
    SELECT c.id, c.active_configuration_id AS configuration_id,
        sc.snapshot_id AS source_id,
        stewardship_campaign_now_v1() AS observed_at,
        COALESCE((SELECT max(s.campaign_sequence)
            FROM stewardship_submission s
            WHERE s.campaign_id=c.id AND s.mode='live'),0) AS watermark
    FROM stewardship_campaign c
    JOIN stewardship_source_current sc ON sc.singleton
    JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
        AND ss.state='promoted' AND ss.compacted_at IS NULL
    WHERE c.id=$1
), items AS MATERIALIZED (
    SELECT i.id,s.campaign_sequence,s.submitted_at,f.family_duid,i.disposition,
        CASE WHEN i.disposition='current_actionable' THEN i.text ELSE NULL END AS text,
        COALESCE(NULLIF(btrim(p.canonical::jsonb->>'mailingName'),''),
            NULLIF(btrim(concat_ws(' ',
                NULLIF(btrim(p.canonical::jsonb->>'firstName'),''),
                NULLIF(btrim(p.canonical::jsonb->>'lastName'),''))),''),'Family')
            AS family_name
    FROM selected x
    JOIN stewardship_submission s ON s.campaign_id=x.id
        AND s.mode='live' AND s.campaign_sequence<=x.watermark
    JOIN stewardship_additional_information i ON i.submission_id=s.id
    JOIN stewardship_family_campaign f ON f.id=s.family_id
    LEFT JOIN stewardship_snapshot_family m ON m.snapshot_id=x.source_id
        AND m.source_key=f.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
)
SELECT jsonb_build_object(
    'campaign_id',x.id,'configuration_id',x.configuration_id,
    'source_id',x.source_id,'observed_at',x.observed_at,'watermark',x.watermark,
    'items',COALESCE((SELECT jsonb_agg(jsonb_build_array(
        i.id,i.campaign_sequence,i.family_duid,i.family_name,i.submitted_at,
        i.disposition,i.text) ORDER BY i.campaign_sequence) FROM items i),'[]'::jsonb)
) FROM selected x
$$;

-- Delivery, not allocation or a recipient withdrawal, establishes that an
-- actionable item was reported. A completed empty interval still advances the
-- observation boundary; a partial cohort cannot do so.
CREATE FUNCTION stewardship_weekly_history_v1(campaign uuid,mode text,epoch uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH snapshots AS MATERIALIZED (
        SELECT s.*,p.occurrence_id FROM stewardship_weekly_digest_snapshot s
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        WHERE p.campaign_id=$1 AND p.mode=$2 AND p.rehearsal_epoch_id IS NOT DISTINCT FROM $3
    ), completed AS MATERIALIZED (
        SELECT s.* FROM snapshots s
        WHERE NOT EXISTS(SELECT 1 FROM stewardship_weekly_manual_request WHERE id=s.preparation_id)
          AND (EXISTS(
            SELECT 1 FROM stewardship_schedule_fulfillment f
            WHERE f.occurrence_id=s.occurrence_id AND f.mode=$2 AND f.target='admins'
              AND f.disposition IN ('delivered','empty'))
            OR EXISTS(SELECT 1 FROM stewardship_postclose_resolution resolved
                JOIN public.stewardship_postclose_current covered ON covered.id=resolved.id
                WHERE resolved.occurrence_id=s.occurrence_id AND resolved.mode=$2))
    ), reported AS (
        SELECT DISTINCT item FROM snapshots s
        JOIN stewardship_weekly_digest_recipient r ON r.snapshot_id=s.id
        JOIN stewardship_outbox_message m ON m.id=r.outbox_id AND m.state='delivered'
        CROSS JOIN LATERAL jsonb_array_elements_text(r.information) item
    ), corrected AS (
        SELECT DISTINCT item FROM completed s
        CROSS JOIN LATERAL jsonb_array_elements(s.corrections) item
    ) SELECT jsonb_build_object('campaign_id',$1,
        'watermark',coalesce((SELECT max(submission_watermark) FROM completed),0),
        'reported',coalesce((SELECT jsonb_agg(item ORDER BY item) FROM reported),'[]'::jsonb),
        'corrected',coalesce((SELECT jsonb_agg(item ORDER BY item) FROM corrected),'[]'::jsonb))
$$;

CREATE FUNCTION stewardship_weekly_selection_v1(observation jsonb,history jsonb)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT jsonb_build_object(
        'information',coalesce((SELECT jsonb_agg(item->0 ORDER BY (item->>4)::timestamptz,(item->>0)::uuid)
            FROM jsonb_array_elements(observation->'items') item
            WHERE item->>5='current_actionable' AND (item->>1)::bigint>(history->>'watermark')::bigint),'[]'::jsonb),
        'corrections',coalesce((SELECT jsonb_agg(jsonb_build_array(item->0,item->5)
                ORDER BY (item->>4)::timestamptz,(item->>0)::uuid)
            FROM jsonb_array_elements(observation->'items') item
            WHERE item->>5 IN ('superseded','withdrawn') AND history->'reported' ? (item->>0)
              AND NOT history->'corrected' @> jsonb_build_array(jsonb_build_array(item->0,item->5))),'[]'::jsonb))
$$;

CREATE FUNCTION stewardship_weekly_snapshot_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_weekly_digest_preparation%ROWTYPE; expected jsonb;
        history jsonb; selected jsonb; cohort jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO p FROM stewardship_weekly_digest_preparation WHERE id=NEW.preparation_id;
    expected:=stewardship_weekly_observation_v1(NEW.campaign_id);
    history:=stewardship_weekly_preparation_history_v1(p.id);
    selected:=stewardship_weekly_selection_v1(expected,history);
    SELECT coalesce(jsonb_agg(email ORDER BY email),'[]'::jsonb) INTO cohort
        FROM stewardship_address_rule WHERE configuration_id=NEW.configuration_id AND roles ? 'administrator';
    IF p.phase IS DISTINCT FROM 'capture' OR NEW.campaign_id IS DISTINCT FROM p.campaign_id
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT stewardship_weekly_digest_live_v1(p.id,NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration WHERE active_configuration_id=NEW.configuration_id)
      OR expected IS NULL OR NEW.observation-'observed_at' IS DISTINCT FROM expected-'observed_at'
      OR NEW.source_id::text IS DISTINCT FROM expected->>'source_id'
      OR NEW.timezone_configuration_id::text IS DISTINCT FROM expected->>'configuration_id'
      OR NEW.submission_watermark IS DISTINCT FROM (expected->>'watermark')::bigint
      OR NEW.after_watermark IS DISTINCT FROM (history->>'watermark')::bigint
      OR NEW.information IS DISTINCT FROM selected->'information'
      OR NEW.corrections IS DISTINCT FROM selected->'corrections'
      OR NEW.item_versions IS DISTINCT FROM (
          SELECT coalesce(jsonb_object_agg(i.id::text,i.version),'{}'::jsonb)
          FROM public.stewardship_additional_information i
          WHERE selected->'information' ? i.id::text OR EXISTS(
              SELECT 1 FROM jsonb_array_elements(selected->'corrections') correction
              WHERE correction->>0=i.id::text))
      OR NEW.recipients IS DISTINCT FROM cohort
      OR (NEW.observation->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at
      OR NEW.observed_at>stewardship_campaign_now_v1()
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.observation->'items') item
          WHERE (item->>4)::timestamptz>NEW.observed_at)
    THEN RAISE EXCEPTION 'Weekly capture requires exact live inputs and resolved history' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER weekly_snapshot_insert BEFORE INSERT ON stewardship_weekly_digest_snapshot
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_snapshot_guard_v1();

CREATE FUNCTION stewardship_weekly_snapshot_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation
        WHERE id=NEW.preparation_id AND phase IN ('fanout','complete')) THEN
        RAISE EXCEPTION 'Weekly capture and fanout release must commit together' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER weekly_snapshot_complete AFTER INSERT ON stewardship_weekly_digest_snapshot
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_snapshot_commit_v1();

CREATE FUNCTION stewardship_weekly_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' AND stewardship_cleanup_effect_v1(TG_ARGV[0],OLD.id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Weekly report records are immutable outside owned cleanup' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_immutable_v1() FROM PUBLIC;
CREATE TRIGGER weekly_snapshot_immutable BEFORE UPDATE OR DELETE ON stewardship_weekly_digest_snapshot
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_immutable_v1('weekly_digest_snapshots');
CREATE TRIGGER weekly_recipient_immutable BEFORE UPDATE OR DELETE ON stewardship_weekly_digest_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_weekly_immutable_v1('weekly_digest_recipients');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON stewardship_weekly_digest_snapshot
FOR EACH ROW EXECUTE FUNCTION stewardship_cleanup_protect_v1('weekly_digest_snapshots');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON stewardship_weekly_digest_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_cleanup_protect_v1('weekly_digest_recipients');
