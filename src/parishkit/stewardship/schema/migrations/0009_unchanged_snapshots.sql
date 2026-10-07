-- Frozen forward migration file 0009 (the repository-wide file sequence;
-- Django's stewardship_source.0004): a quick update whose corpus equals the
-- current one ends in a new terminal snapshot state, 'unchanged', instead of
-- staging a full copy and promoting it (#630). This file is installed by
-- source/migrations/0004_unchanged_snapshots.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copies of the replaced functions still equal the
-- fresh-install baseline's (schema/functions.sql).
-- It follows 0008 (#633). A fresh install runs the baseline, 0002 to 0008
-- and then this; the baseline files already carry the widened constraint and
-- the replaced bodies, so the install ends in the same catalog as an
-- upgraded database.
--
-- The snapshot state check admits 'unchanged'. The snapshot guard admits
-- staging -> unchanged only for a delta with its live fenced owner, no
-- membership rows, the current promoted snapshot as its base (the pointer is
-- read FOR SHARE, so a concurrent promotion waits), and that base's
-- organization, counts and content digest; no update may leave the state. The
-- completion trigger checks an unchanged snapshot's scope, credential and
-- cursor exactly as it checks a ready one. Neither function is SECURITY
-- DEFINER or sets a search_path in the baseline, so nothing is re-altered.
-- No existing row changes. Reversing needs its own forward migration, after
-- any 'unchanged' rows have been accounted for.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_source_snapshot DROP CONSTRAINT source_snapshot_state;
ALTER TABLE public.stewardship_source_snapshot ADD CONSTRAINT source_snapshot_state CHECK (((state)::text = ANY ((ARRAY['staging'::character varying, 'ready'::character varying, 'rejected'::character varying, 'promoted'::character varying, 'unchanged'::character varying])::text[])));

-- The guard, exactly as the fresh-install functions.sql defines it.
CREATE OR REPLACE FUNCTION public.stewardship_source_snapshot_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE current_source stewardship_source_current%ROWTYPE;
        base stewardship_source_snapshot%ROWTYPE;
        evidence jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Source manifests are permanent' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state <> 'staging' OR NEW.completed_at IS NOT NULL
           OR NEW.content_digest <> '' OR NEW.compacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Source snapshots must begin in staging'
                USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='promoted' THEN
        IF (to_jsonb(NEW)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) OR OLD.compacted_at IS NOT NULL
           OR NEW.compacted_at IS NULL THEN
            RAISE EXCEPTION 'Promoted source manifests are immutable'
                USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_source_current WHERE snapshot_id=OLD.id)
           OR EXISTS (SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=OLD.id
                AND (expires_at IS NULL OR expires_at > clock_timestamp()))
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
                JOIN stewardship_task_run t ON t.id=l.owner_id
                WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                  AND t.state='running' AND t.fence=l.task_fence
                  AND t.worker_id=l.worker_id
                  AND t.lease_expires_at > clock_timestamp())
        THEN
            RAISE EXCEPTION 'Source snapshot is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    ELSIF NOT ((OLD.state='staging'
            AND NEW.state IN ('staging','ready','rejected','unchanged'))
        OR (OLD.state='ready' AND NEW.state IN ('promoted','rejected'))) THEN
        RAISE EXCEPTION 'Source snapshot transition is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.state='ready' AND (
        NEW.counts IS DISTINCT FROM OLD.counts
        OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
        OR NEW.cursor IS DISTINCT FROM OLD.cursor
        OR NEW.validation IS DISTINCT FROM OLD.validation
    ) THEN
        RAISE EXCEPTION 'Validated source evidence is immutable' USING ERRCODE='23514';
    END IF;

    IF TG_OP='UPDATE' AND NEW.state='rejected' THEN
        IF (to_jsonb(NEW)-ARRAY[
                'version','updated_at','actor_id','correlation_id','state'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id','state'])
           OR NOT EXISTS (
            SELECT 1 FROM stewardship_source_lease l
            JOIN stewardship_task_run t ON t.id=l.owner_id
            WHERE l.phase IN ('full','delta') AND l.fence >= OLD.source_fence
              AND l.expires_at > clock_timestamp() AND t.state='running'
              AND t.fence=l.task_fence AND t.worker_id=l.worker_id
              AND t.lease_expires_at > clock_timestamp()
              AND NEW.actor_id=l.worker_id
              AND (l.fence > OLD.source_fence OR (
                   l.owner_id=OLD.task_id AND l.phase=OLD.kind))
           ) THEN
            RAISE EXCEPTION 'Source rejection requires unchanged evidence/live owner'
                USING ERRCODE='23514';
        END IF;
        -- A newer source fence is possible only after prior external drainage.
        -- Keep the old task/fence binding; never rebind its payloads to recovery.
        RETURN NEW;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
        JOIN stewardship_task_run t ON t.id=l.owner_id
        WHERE l.owner_id=NEW.task_id AND l.fence=NEW.source_fence AND l.phase=NEW.kind
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()) THEN
        RAISE EXCEPTION 'Source snapshot requires its live fenced owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('ready','promoted') AND (
        NEW.completed_at IS NULL OR NEW.completed_at < NEW.started_at
        OR NEW.completed_at > clock_timestamp() OR NEW.content_digest=''
        OR NEW.validation->>'schema' IS DISTINCT FROM 'source-corpus-v1'
        OR NEW.validation->'complete' IS DISTINCT FROM 'true'::jsonb
    ) THEN
        RAISE EXCEPTION 'Source snapshot is not validated' USING ERRCODE='23514';
    END IF;
    IF NEW.state='promoted' THEN
        SELECT * INTO current_source FROM stewardship_source_current
            WHERE singleton FOR UPDATE;
        IF NOT FOUND OR NEW.generation <> current_source.generation+1
           OR NEW.base_id IS DISTINCT FROM current_source.snapshot_id
           OR (current_source.organization_id IS NOT NULL AND
               NEW.organization_id <> current_source.organization_id)
           OR NEW.promoted_at < NEW.completed_at OR NEW.promoted_at > clock_timestamp()
        THEN
            RAISE EXCEPTION 'Source promotion has a stale or inconsistent base'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state='ready' THEN
        evidence := stewardship_source_corpus_evidence(NEW.id);
        IF NEW.counts IS DISTINCT FROM evidence->'counts'
           OR NEW.content_digest IS DISTINCT FROM evidence->>'digest' THEN
            RAISE EXCEPTION 'Source evidence differs from its complete corpus'
                USING ERRCODE='23514';
        END IF;
    END IF;
    -- A quick update whose corpus equals the current one (#630) ends here
    -- instead of staging a copy and promoting it: no membership rows, the
    -- current snapshot's counts and digest, and a terminal state that no
    -- later update may leave. It never becomes source truth.
    IF NEW.state='unchanged' THEN
        -- Hold the pointer still until commit, as promotion's FOR UPDATE
        -- does, so a concurrent promotion cannot make this base stale.
        SELECT * INTO current_source FROM stewardship_source_current
            WHERE singleton FOR SHARE;
        SELECT * INTO base FROM stewardship_source_snapshot WHERE id=NEW.base_id;
        evidence := stewardship_source_corpus_evidence(NEW.id);
        IF NEW.kind <> 'delta' OR base.id IS NULL OR base.state <> 'promoted'
           OR NEW.base_id IS DISTINCT FROM current_source.snapshot_id
           OR NEW.organization_id IS DISTINCT FROM base.organization_id
           OR NEW.completed_at IS NULL OR NEW.completed_at < NEW.started_at
           OR NEW.completed_at > clock_timestamp()
           OR NEW.validation IS DISTINCT FROM
              '{"schema":"source-unchanged-v1"}'::jsonb
           OR NEW.counts IS DISTINCT FROM base.counts
           OR NEW.content_digest IS DISTINCT FROM base.content_digest
           OR EXISTS (SELECT 1 FROM jsonb_each_text(evidence->'counts') c
                      WHERE c.value <> '0') THEN
            RAISE EXCEPTION 'An unchanged quick update must match the current corpus'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- The completion check, exactly as the fresh-install functions.sql defines it.
CREATE OR REPLACE FUNCTION public.stewardship_refresh_snapshot_completion_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE attempt stewardship_source_refresh_attempt%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        base_cursor jsonb;
BEGIN
    IF NEW.state NOT IN ('ready','promoted','unchanged') OR OLD.state='promoted'
       OR NOT EXISTS (SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
           AND task_type='source_refresh') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO attempt FROM stewardship_source_refresh_attempt
        WHERE snapshot_id=NEW.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Source completion requires its bound attempt'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO request FROM stewardship_source_refresh_request
        WHERE id=attempt.request_id;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration c
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        JOIN stewardship_task_run t ON t.id=attempt.task_id
        WHERE i.kind='parishsoft'
          AND i.credential_fingerprint=attempt.credential_fingerprint
          AND i.settings->>'organization_id'=request.organization_id::text
          AND c.current_campaign_id IS NOT DISTINCT FROM request.campaign_id
          AND NOT c.restore_review_required
          AND request.window_canonical=
              stewardship_source_current_window_v1(request.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.state='running' AND t.fence=attempt.task_fence
          AND t.lease_expires_at > clock_timestamp()
       ) OR NEW.cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
       OR NEW.cursor->>'window_digest' IS DISTINCT FROM request.window_digest
       OR NEW.cursor->>'watermark' IS NULL
       OR (NEW.cursor->>'watermark')::timestamptz IS DISTINCT FROM NEW.started_at
    THEN
        RAISE EXCEPTION 'Source completion scope/credential/cursor is stale'
            USING ERRCODE='23514';
    END IF;
    IF NEW.kind='full' THEN
        IF NEW.cursor->>'full_snapshot_id' IS DISTINCT FROM NEW.id::text
           OR NEW.cursor->>'full_started_at' IS NULL
           OR (NEW.cursor->>'full_started_at')::timestamptz
              IS DISTINCT FROM NEW.started_at THEN
            RAISE EXCEPTION 'Full source cursor is not its own observation'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT cursor INTO base_cursor FROM stewardship_source_snapshot
            WHERE id=NEW.base_id AND state='promoted';
        IF NOT FOUND OR base_cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
           OR base_cursor->>'window_digest' IS DISTINCT FROM request.window_digest
           OR NEW.cursor->>'full_snapshot_id'
              IS DISTINCT FROM base_cursor->>'full_snapshot_id'
           OR NEW.cursor->>'full_started_at'
              IS DISTINCT FROM base_cursor->>'full_started_at' THEN
            RAISE EXCEPTION 'Delta source cursor lacks coherent full coverage'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Refuse to commit unless every part above is installed.
DO $check$
BEGIN
    IF position('''unchanged''' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='source_snapshot_state'
              AND conrelid='public.stewardship_source_snapshot'::regclass)))=0 THEN
        RAISE EXCEPTION 'source_snapshot_state does not admit unchanged';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_source_snapshot_guard'
                     AND NOT p.prosecdef
                     AND p.prosrc LIKE '%An unchanged quick update must match the current corpus%'
                     AND p.prosrc LIKE '%WHERE singleton FOR SHARE;%'
                     AND p.prosrc LIKE '%NEW.organization_id IS DISTINCT FROM base.organization_id%'
                     AND p.prosrc LIKE '%''staging'',''ready'',''rejected'',''unchanged''%') THEN
        RAISE EXCEPTION 'stewardship_source_snapshot_guard does not admit unchanged snapshots';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_refresh_snapshot_completion_v1'
                     AND NOT p.prosecdef
                     AND p.prosrc LIKE '%NOT IN (''ready'',''promoted'',''unchanged'')%') THEN
        RAISE EXCEPTION 'stewardship_refresh_snapshot_completion_v1 does not check unchanged snapshots';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid='public.stewardship_source_snapshot'::regclass
                     AND tgname='source_snapshot_guard' AND NOT tgisinternal)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid='public.stewardship_source_snapshot'::regclass
                     AND tgname='stewardship_refresh_snapshot_complete'
                     AND NOT tgisinternal) THEN
        RAISE EXCEPTION 'The source snapshot triggers are missing';
    END IF;
END
$check$;
