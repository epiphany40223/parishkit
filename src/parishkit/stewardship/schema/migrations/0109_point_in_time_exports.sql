-- Frozen forward migration file 0109 (the repository-wide file sequence,
-- number assigned centrally): exports are point-in-time (#1398; operations
-- spec, "Point-in-time exports"). When an export ends, its saved data goes:
-- the capture of what it read is cleared and its fact and source pins are
-- released, so nothing private outlives its short download window. This
-- file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced functions equal
-- the fresh-install baseline's. A later change gets its own numbered file. A
-- fresh install runs the baseline, the earlier files and then this; the
-- baseline already carries every replaced function and trigger change, so
-- the install ends in the same catalog as an upgraded database.
--
-- The window. The deployment's export_download_window_minutes (15-240,
-- default 60) is a Django setting the database cannot read, so every caller
-- that asks whether an export has ended sets it as the custom setting
-- stewardship.export_window_minutes on its connection (reports.export_window).
-- stewardship_export_window_v1 reads it, and without a valid value it is 240
-- minutes, the longest window: a caller that forgot can only discard late,
-- never early.
--
-- Created, only here:
--   stewardship_export_discard             one row per ended export whose
--                                          saved data was discarded: which
--                                          export, how many captures were
--                                          cleared and pins released, and
--                                          whether this file's sweep did it
--   stewardship_export_window_v1()         the window (above)
--   stewardship_export_ended_v1(uuid)      whether a report export has ended:
--                                          its file's window is over, it was
--                                          cancelled and its work has
--                                          stopped, or its latest run failed
--                                          one window ago or more
--   stewardship_exact_export_ended_v1(uuid) the same for a latest-data export,
--                                          whose handed-off file must have
--                                          been discarded first
--   stewardship_export_capture_cleared_v1(oid,jsonb,jsonb) whether a
--                                          capture UPDATE is a discard's one
--                                          admitted change (below)
--   stewardship_export_discard_v1(uuid,boolean)       one report export
--   stewardship_exact_export_discard_v1(uuid,boolean) one latest-data export
--   stewardship_export_discard_batch_v1(integer,boolean) a small batch of
--                                          ended exports not discarded yet
--   stewardship_export_discard_due_v1(integer) the scheduler's batch (by_deploy
--                                          false); 0 only asks whether any
--                                          export is due
-- The discard functions are SECURITY DEFINER; only the last is executable,
-- by the scheduler login alone, through its runtime grant, so only this
-- file's sweep records a discard as by_deploy. They run as the schema owner,
-- and each refuses an export that has not ended, so no live export loses
-- anything; a capture shared by more than one request (an earlier release's
-- regeneration reused it) is cleared only when every request using it has
-- ended. A discard clears the capture's document to {}, releases the pins,
-- records the discard and clears the request's (or the latest-data
-- resolution's) link to its fact generation, so fact compaction may remove
-- that generation. No table lock and no trigger is set aside: the capture
-- triggers and the export history trigger each admit exactly that one change,
-- made by the schema owner, and still refuse every other UPDATE.
--
-- Changed tables: stewardship_exact_export_resolution.fact_set_id becomes
-- nullable, and export_report_known no longer requires a participation or
-- archive request's fact_set_id, which the discard clears (the request guard
-- and its pin check still require one on every new request).
--
-- Replaced in place, each copied whole from its latest definition (the
-- baseline's) with only this change:
--   stewardship_export_disposable_v1   a file is disposable at its window's
--                                      end, never later than expires_at
--   stewardship_fact_disposable        an ended latest-data export no longer
--                                      holds its fact inputs
--   stewardship_exact_source_pin_guard_v1  nor its source pin
--   stewardship_safe_context_v1        0106's body plus the action keys
--                                      report and format, closed sets, and
--                                      the failure word export_discard
--   stewardship_fact_disposable        (also) a generation an export still
--                                      links is kept
--   stewardship_export_immutable_v1    admits the discard's link clearing
--   stewardship_export_request_guard_v1  refuses a cleared capture
--   the seven capture triggers' functions admit the discard's clearing:
--     stewardship_directory_export_capture_v1, ..._financial_..._v1 (the
--     baseline's), stewardship_information_export_capture_v2 and
--     stewardship_ministry_export_capture_v2 (0064's),
--     stewardship_family_test_names_export_capture_v1 (0037's),
--     stewardship_timeline_export_capture_v1 (0063's) and
--     stewardship_comparison_export_capture_v1 (0071's)
-- None is SECURITY DEFINER, and each keeps its identity and attributes.
-- The capture functions a frozen file made are copied from that file.
--
-- The sweep. Every export that has already ended when this file runs is
-- discarded here, with the 240-minute window, so no export that might still
-- be inside a longer window loses anything; the scheduler's regular pass
-- then catches the rest with the deployment's own window. The sweep is safe
-- to repeat: a discarded export is skipped. It says what it did as a
-- NOTICE, and each discard row records it (by_deploy). Reversing needs its
-- own forward migration.
--
-- Errors. A batch discards each export in its own subtransaction: one that
-- fails is skipped and counted in an operational log entry (candidates
-- come in random order, so one that keeps failing cannot hold back the
-- rest). A lock or statement timeout is never swallowed: it stops the whole
-- batch, which the scheduler records as a timeout.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The durable record of each discard, as its Django model declares it.
CREATE TABLE "stewardship_export_discard" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "documents" smallint NOT NULL CHECK ("documents" >= 0), "pins" integer NOT NULL CHECK ("pins" >= 0), "by_deploy" boolean NOT NULL, "exact_request_id" uuid NULL UNIQUE, "request_id" uuid NULL UNIQUE, CONSTRAINT "export_discard_one_request" CHECK ((("exact_request_id" IS NULL AND "request_id" IS NOT NULL) OR ("exact_request_id" IS NOT NULL AND "request_id" IS NULL))), CONSTRAINT "export_discard_documents" CHECK ("documents" <= 1));
ALTER TABLE "stewardship_export_discard" ADD CONSTRAINT "stewardship_export_d_exact_request_id_cda865f5_fk_stewardsh" FOREIGN KEY ("exact_request_id") REFERENCES "stewardship_exact_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_discard" ADD CONSTRAINT "stewardship_export_d_request_id_789a62a8_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_discard_correlation_id_8a27a031" ON "stewardship_export_discard" ("correlation_id");

-- A discard clears the link to the generation an ended export used.
ALTER TABLE public.stewardship_exact_export_resolution ALTER COLUMN fact_set_id DROP NOT NULL;
-- The known report kinds, in the form PostgreSQL deparses the model's
-- declaration: 0084's arms, the participation and campaign_archive ones
-- without their fact_set_id requirement. Every existing row still holds.
ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[])) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_timeline'::text) AND (timeline_snapshot_id IS NOT NULL)) OR ((comparison_snapshot_id IS NOT NULL) AND (directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'campaign_comparison'::text) AND (timeline_snapshot_id IS NULL)) OR ((comparison_snapshot_id IS NULL) AND (directory_snapshot_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = 'xlsx'::text) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'campaign_archive'::text) AND (timeline_snapshot_id IS NULL))));
-- History: a discard is never changed or removed.
CREATE TRIGGER export_immutable BEFORE UPDATE OR DELETE ON stewardship_export_discard
FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1();

CREATE FUNCTION public.stewardship_export_window_v1() RETURNS interval
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT make_interval(mins => CASE
        WHEN setting ~ '^[0-9]{1,3}$' AND setting::integer BETWEEN 15 AND 240
        THEN setting::integer ELSE 240 END)
    FROM (SELECT current_setting('stewardship.export_window_minutes', true) AS setting) s
$$;

-- A report export has ended (reports.export_services.export_ended). A
-- cancelled one ends only once its work has stopped, so a render still at
-- its safe point never loses its capture. NULL for an unknown request.
CREATE FUNCTION public.stewardship_export_ended_v1(request_uuid uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE
        WHEN p.id IS NOT NULL THEN
            LEAST(p.expires_at,p.created_at+stewardship_export_window_v1())<=clock_timestamp()
        WHEN latest.state IN ('queued','running','retry_wait','abandoned') THEN false
        WHEN c.id IS NOT NULL THEN true
        ELSE latest.state='failed'
            AND latest.updated_at+stewardship_export_window_v1()<=clock_timestamp()
    END
    FROM stewardship_export_request r
    LEFT JOIN stewardship_export_publication p ON p.request_id=r.id
    LEFT JOIN stewardship_export_cancellation c ON c.request_id=r.id
    LEFT JOIN LATERAL (SELECT t.state,t.updated_at FROM stewardship_task_run t
        WHERE t.root_id=r.task_id ORDER BY t.retry_sequence DESC LIMIT 1) latest ON true
    WHERE r.id=request_uuid
$$;

-- A latest-data export has ended (exact_services.exact_export_ended): once
-- it handed off, only when that report export's saved data is discarded.
CREATE FUNCTION public.stewardship_exact_export_ended_v1(request_uuid uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE
        WHEN x.export_id IS NOT NULL THEN
            EXISTS(SELECT 1 FROM stewardship_export_discard d WHERE d.request_id=x.export_id)
        WHEN latest.state IN ('queued','running','retry_wait','abandoned') THEN false
        WHEN c.id IS NOT NULL THEN true
        ELSE latest.state='failed'
            AND latest.updated_at+stewardship_export_window_v1()<=clock_timestamp()
    END
    FROM stewardship_exact_export_request r
    LEFT JOIN stewardship_exact_export_resolution x ON x.request_id=r.id
    LEFT JOIN stewardship_exact_export_cancel c ON c.request_id=r.id
    LEFT JOIN LATERAL (SELECT t.state,t.updated_at FROM stewardship_task_run t
        WHERE t.root_id=r.task_id ORDER BY t.retry_sequence DESC LIMIT 1) latest ON true
    WHERE r.id=request_uuid
$$;

-- Whether a capture UPDATE is the one change a discard makes: run as the
-- capture table's owner (only the discard functions are), it clears the
-- document to {} and changes nothing else. Each capture trigger admits it.
CREATE FUNCTION public.stewardship_export_capture_cleared_v1(relation oid, old jsonb, new jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce(new->'document'='{}'::jsonb AND new-'document'=old-'document'
        AND pg_has_role(current_user,(SELECT relowner FROM pg_class WHERE oid=relation),'USAGE'),false)
$$;

-- Discard one ended report export's saved data. Returns how many captures
-- and pins it removed; 0 when it was already discarded. Refuses an export
-- that has not ended.
CREATE FUNCTION public.stewardship_export_discard_v1(request_uuid uuid, by_deploy boolean)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE r stewardship_export_request%ROWTYPE; cleared integer:=0; released integer;
        n integer; target regclass; snapshot uuid; column_name text;
BEGIN
    SELECT * INTO r FROM stewardship_export_request WHERE id=request_uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No such export' USING ERRCODE='23514';
    END IF;
    -- A retry that wins these locks adds a newer run first, so the export
    -- is then not ended; one that waits sees the discard.
    PERFORM 1 FROM stewardship_task_run WHERE root_id=r.task_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM stewardship_export_discard WHERE request_id=request_uuid) THEN
        RETURN 0;
    END IF;
    IF NOT coalesce(stewardship_export_ended_v1(request_uuid),false) THEN
        RAISE EXCEPTION 'This export has not ended; its saved data stays' USING ERRCODE='23514';
    END IF;
    FOR target, snapshot, column_name IN VALUES
        ('stewardship_information_export_snapshot'::regclass, r.information_snapshot_id, 'information_snapshot_id'::text),
        ('stewardship_directory_export_snapshot'::regclass, r.directory_snapshot_id, 'directory_snapshot_id'),
        ('stewardship_ministry_export_snapshot'::regclass, r.ministry_snapshot_id, 'ministry_snapshot_id'),
        ('stewardship_financial_export_snapshot'::regclass, r.financial_snapshot_id, 'financial_snapshot_id'),
        ('stewardship_family_test_names_export_snapshot'::regclass, r.family_test_names_snapshot_id, 'family_test_names_snapshot_id'),
        ('stewardship_timeline_export_snapshot'::regclass, r.timeline_snapshot_id, 'timeline_snapshot_id'),
        ('stewardship_comparison_export_snapshot'::regclass, r.comparison_snapshot_id, 'comparison_snapshot_id')
    LOOP
        CONTINUE WHEN snapshot IS NULL;
        -- The capture trigger admits this one change, by the owner
        -- (stewardship_export_capture_cleared_v1). A capture another live
        -- request still uses stays whole.
        EXECUTE format('UPDATE %s s SET document=''{}''::jsonb WHERE s.id=$1 '
            'AND s.document<>''{}''::jsonb AND NOT EXISTS(SELECT 1 FROM '
            'stewardship_export_request o WHERE o.%I=s.id AND o.id<>$2 '
            'AND NOT coalesce(stewardship_export_ended_v1(o.id),false))',
            target, column_name) USING snapshot, request_uuid;
        GET DIAGNOSTICS n = ROW_COUNT;
        cleared:=cleared+n;
    END LOOP;
    DELETE FROM stewardship_fact_pin WHERE parent_kind='export' AND parent_id=request_uuid;
    GET DIAGNOSTICS released = ROW_COUNT;
    INSERT INTO stewardship_export_discard(id,correlation_id,documents,pins,by_deploy,request_id)
        VALUES (gen_random_uuid(),r.correlation_id,cleared,released,by_deploy,request_uuid);
    -- Last: the history trigger admits this only once the discard is
    -- recorded. The generation is then free for compaction.
    UPDATE stewardship_export_request SET fact_set_id=NULL
        WHERE id=request_uuid AND fact_set_id IS NOT NULL;
    RETURN cleared+released;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_export_discard_v1(uuid,boolean) FROM PUBLIC;

-- Discard one ended latest-data export's pins: its current-population
-- source pin and, through the discard row, its fact inputs, then its
-- resolution's link to the generation. Its handed-off report export is
-- discarded on its own first.
CREATE FUNCTION public.stewardship_exact_export_discard_v1(request_uuid uuid, by_deploy boolean)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE r stewardship_exact_export_request%ROWTYPE; released integer;
BEGIN
    SELECT * INTO r FROM stewardship_exact_export_request WHERE id=request_uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No such export' USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM stewardship_task_run WHERE root_id=r.task_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM stewardship_export_discard WHERE exact_request_id=request_uuid) THEN
        RETURN 0;
    END IF;
    IF NOT coalesce(stewardship_exact_export_ended_v1(request_uuid),false) THEN
        RAISE EXCEPTION 'This export has not ended; its saved data stays' USING ERRCODE='23514';
    END IF;
    -- The row first: the source pin guard admits the release only once the
    -- discard is recorded. Its pin count is set from the release below.
    SELECT count(*) INTO released FROM stewardship_source_pin
        WHERE parent_kind='report' AND parent_id=request_uuid;
    INSERT INTO stewardship_export_discard(id,correlation_id,documents,pins,by_deploy,exact_request_id)
        VALUES (gen_random_uuid(),r.correlation_id,0,released,by_deploy,request_uuid);
    DELETE FROM stewardship_source_pin WHERE parent_kind='report' AND parent_id=request_uuid;
    UPDATE stewardship_exact_export_resolution SET fact_set_id=NULL
        WHERE request_id=request_uuid AND fact_set_id IS NOT NULL;
    RETURN released;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_exact_export_discard_v1(uuid,boolean) FROM PUBLIC;

-- A small batch of ended exports not yet discarded: up to batch report
-- exports, then up to batch latest-data ones, whose handed-off file must go
-- first. Candidates come in random order, so one that keeps failing cannot
-- hold back the rest. One that cannot be discarded now is skipped and
-- counted (one WARNING operational log entry per batch), and tried again
-- next time; a lock or statement timeout is re-raised and stops the batch.
-- Returns {"discarded": n, "skipped": n}.
-- Not executable by any login: the sweep below calls it, and the scheduler
-- calls stewardship_export_discard_due_v1.
CREATE FUNCTION public.stewardship_export_discard_batch_v1(batch integer, by_deploy boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE identifier uuid; done integer:=0; skipped integer:=0;
BEGIN
    IF batch IS NULL OR batch NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'A discard batch is 1 to 1000 exports' USING ERRCODE='22023';
    END IF;
    FOR identifier IN SELECT r.id FROM stewardship_export_request r
        WHERE NOT EXISTS(SELECT 1 FROM stewardship_export_discard d WHERE d.request_id=r.id)
          AND r.created_at<=clock_timestamp()-interval '15 minutes'
          AND coalesce(stewardship_export_ended_v1(r.id),false)
        ORDER BY random() LIMIT batch
    LOOP
        BEGIN
            PERFORM stewardship_export_discard_v1(identifier,by_deploy);
            done:=done+1;
        EXCEPTION
            WHEN lock_not_available OR query_canceled THEN RAISE;
            WHEN others THEN skipped:=skipped+1;
        END;
    END LOOP;
    FOR identifier IN SELECT r.id FROM stewardship_exact_export_request r
        WHERE NOT EXISTS(SELECT 1 FROM stewardship_export_discard d WHERE d.exact_request_id=r.id)
          AND r.created_at<=clock_timestamp()-interval '15 minutes'
          AND coalesce(stewardship_exact_export_ended_v1(r.id),false)
        ORDER BY random() LIMIT batch
    LOOP
        BEGIN
            PERFORM stewardship_exact_export_discard_v1(identifier,by_deploy);
            done:=done+1;
        EXCEPTION
            WHEN lock_not_available OR query_canceled THEN RAISE;
            WHEN others THEN skipped:=skipped+1;
        END;
    END LOOP;
    -- Skips are a WARNING in the operational log (the System logs page),
    -- not only in the server's: what failed, how many, and that it retries.
    IF skipped>0 THEN
        INSERT INTO stewardship_operational_log(id,correlation_id,event,level,schema,context)
        VALUES (gen_random_uuid(),gen_random_uuid(),'task_failed','WARNING','failure',
            jsonb_build_object('failure','export_discard','count',skipped,'outcome','retry'));
    END IF;
    RETURN jsonb_build_object('discarded',done,'skipped',skipped);
END $$;
REVOKE ALL ON FUNCTION public.stewardship_export_discard_batch_v1(integer,boolean) FROM PUBLIC;

-- The scheduler's call (reports.export_cleanup.produce_discards), under the
-- work order: a batch with by_deploy false. A batch of 0 takes no lock and
-- returns only {"pending": true|false}, whether any export is due, so the
-- scheduler takes the work order only when there is work.
CREATE FUNCTION public.stewardship_export_discard_due_v1(batch integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF batch=0 THEN
        RETURN jsonb_build_object('pending',
            EXISTS(SELECT 1 FROM stewardship_export_request r
                WHERE NOT EXISTS(SELECT 1 FROM stewardship_export_discard d WHERE d.request_id=r.id)
                  AND r.created_at<=clock_timestamp()-interval '15 minutes'
                  AND coalesce(stewardship_export_ended_v1(r.id),false))
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_request r
                WHERE NOT EXISTS(SELECT 1 FROM stewardship_export_discard d WHERE d.exact_request_id=r.id)
                  AND r.created_at<=clock_timestamp()-interval '15 minutes'
                  AND coalesce(stewardship_exact_export_ended_v1(r.id),false)));
    END IF;
    RETURN stewardship_export_discard_batch_v1(batch,false);
END $$;
REVOKE ALL ON FUNCTION public.stewardship_export_discard_due_v1(integer) FROM PUBLIC;

-- The replaced functions, each the baseline's definition.
CREATE OR REPLACE FUNCTION public.stewardship_export_disposable_v1(attempt_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_export_attempt a
        WHERE a.id=attempt_uuid
          AND NOT EXISTS(SELECT 1 FROM stewardship_export_cleanup c WHERE c.attempt_id=a.id)
          AND NOT EXISTS(SELECT 1 FROM stewardship_export_publication p
              WHERE p.attempt_id=a.id
                -- The window's end (#1398): never later than the recorded one.
                AND LEAST(p.expires_at,p.created_at+stewardship_export_window_v1())>clock_timestamp())
          AND NOT EXISTS(SELECT 1 FROM stewardship_task_run t
              WHERE t.id=a.run_id AND t.fence=a.fence AND t.worker_id=a.actor_id
                AND t.state='running' AND t.lease_expires_at>clock_timestamp()))
$$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_source_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF OLD.parent_kind='report' THEN
        -- Until the export has ended and its saved data is discarded (#1398).
        IF EXISTS(SELECT 1 FROM stewardship_exact_export_request r
            WHERE r.id=OLD.parent_id AND r.source_id=OLD.snapshot_id
              AND NOT EXISTS(SELECT 1 FROM stewardship_export_discard d
                  WHERE d.exact_request_id=r.id)) THEN
            RAISE EXCEPTION 'Retained exact export still requires its source input' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_disposable(identifier uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_fact_set old
        JOIN stewardship_daily_fact_set newer ON newer.campaign_id=old.campaign_id
            AND newer.population_scope=old.population_scope AND newer.state='ready'
            AND newer.id<>old.id AND newer.created_at>=old.created_at
            AND newer.source_generation>=old.source_generation
            AND newer.submission_watermark>=old.submission_watermark
            AND ((newer.timezone_configuration_id=old.timezone_configuration_id
                    AND newer.through_date>=old.through_date)
                OR (newer.timezone_configuration_id<>old.timezone_configuration_id
                    AND EXISTS (SELECT 1 FROM public.stewardship_campaign c
                        WHERE c.id=newer.campaign_id
                            AND c.active_configuration_id=newer.timezone_configuration_id)))
        WHERE old.id=identifier AND old.state='ready')
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pointer WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.fact_set_id=identifier
            AND t.state IN ('queued','running','retry_wait','abandoned'))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date)
            -- A latest-data export that has ended keeps nothing (#1398).
            AND NOT EXISTS(SELECT 1 FROM stewardship_export_discard d
                WHERE d.exact_request_id=r.id))
    -- An export still linked to the generation keeps it; an ended export's
    -- discard clears the link (#1398), so only those go.
    AND NOT EXISTS(SELECT 1 FROM stewardship_export_request WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand
        WHERE claimed_generation_id=identifier);
$$;

CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint; item jsonb;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count',
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','directory_reach','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id',
            'report_sort','directory_data_check','directory_response_columns',
            'archive_year','archive_days','archive_family_rows','void_reason','task_state',
            -- How old the Google sign-in was when a setup credential was
            -- sealed, in whole seconds (#1402; audit.schemas).
            'sign_in_age_seconds',
            -- An export's report kind and file format (#1398), so the audit
            -- log answers who exported what without its request row.
            'report','format']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        WHEN 'timeout' THEN ARRAY['task_id','task_type','attempt','limit_seconds','elapsed_seconds','what','helper','count','outcome']
        -- What made scheduled work late (#634): the task type, how many tasks
        -- and how late against which limit, or the Family send that stalled
        -- or overran, with its counts, how long since its last progress and
        -- how many other tasks were late in the same check.
        WHEN 'due_work' THEN ARRAY['task_type','count','lag_seconds','limit_seconds','definition_id','revision_id',
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count',
            'occurrence_id','task_id']
        -- What failed and what happens next (#633): a closed word for what
        -- failed, the exception's category, the task, message and attempt,
        -- a closed provider reason or HTTP status, and whether it retries.
        WHEN 'failure' THEN ARRAY['failure','failure_kind','task_id','task_type','message_id','version',
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome','command']
        -- An operational incident that ended (#633): the incident, its kind,
        -- the entry that opened it, how long it lasted and how often it was seen.
        WHEN 'recovery' THEN ARRAY['incident_id','incident_kind','log_id','elapsed_seconds','count']
        -- A refused Admin input (#1403): its page or command, server-owned
        -- form and field names, error codes and the reason, never what was
        -- submitted (audit.refusal_context).
        WHEN 'refusal' THEN ARRAY['route','command','method','status','form','fields','codes',
            'reason','exception_class','expected','actual','unexpected_count','mode','capped']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    -- A refusal always says why and names exactly one of its page and its
    -- command; an exception class and an unexpected-field count belong
    -- only to their own reasons.
    IF schema_name='refusal' AND (NOT payload ? 'reason'
        OR (payload ? 'route')=(payload ? 'command')
        OR (payload ? 'exception_class' AND payload->>'reason' IS DISTINCT FROM 'unclassified')
        OR (payload ? 'unexpected_count' AND payload->>'reason' IS DISTINCT FROM 'unexpected_fields')) THEN
        RETURN false;
    END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        -- A refused Admin input (#1403), checked by its own rules: these keys
        -- mean something else in the other schemas. Identifiers share one
        -- shape (Python holds their closed sets); expected and actual are
        -- setting values such as zone names, never an address.
        IF schema_name='refusal' THEN
            IF key IN ('route','method','form','reason','exception_class') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
            ELSIF key='command' THEN
                IF jsonb_typeof(value)<>'string' OR length(text_value)>64
                   OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
            ELSIF key IN ('fields','codes') THEN
                IF jsonb_typeof(value)<>'array' OR jsonb_array_length(value) NOT BETWEEN 1 AND 10 THEN RETURN false; END IF;
                FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
                    IF jsonb_typeof(item)<>'string' OR item#>>'{}'!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
                END LOOP;
            ELSIF key IN ('expected','actual') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[A-Za-z0-9_+./-]{1,64}$' THEN RETURN false; END IF;
            ELSIF key='mode' THEN
                IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('testing','production') THEN RETURN false; END IF;
            ELSIF key='capped' THEN
                IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
            ELSIF key='status' THEN
                IF jsonb_typeof(value)<>'number' OR text_value NOT IN ('400','403','404','409','410','422') THEN RETURN false; END IF;
            ELSE
                IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,4}$' THEN RETURN false; END IF;
            END IF;
        ELSIF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('no_deliverable_recipient',
                'smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic','smtp_delivery_unknown',
                'preparation_failed','slack_not_sent','slack_delivery_unknown') THEN RETURN false; END IF;
        -- What failed (#633): a closed word (audit.schemas.FAILURES).
        ELSIF key='failure' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('organization_mismatch','destructive_change',
                'shifted_scan','invalid_payload','incomplete_collection','invalid_response','lease_unavailable',
                'configuration_activating','configuration_busy','scope_changed','credential_unreadable',
                'credential_changed','provider_status','provider_timeout','provider_unreachable',
                'source_configuration','organization_changed','source_health_check','mail_health_check',
                'due_work_health_check','backup_health_check','export_cleanup','export_discard','fact_verification',
                'source_retention','family_engagement','alert_mail','security_mail','slack_alert',
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive',
                'admin_command','admin_command_outcome_unknown','digest_funnel_chart',
                'digest_activity_chart','digest_comparison_chart',
                'retry_refusal') THEN RETURN false; END IF;
        -- An Admin command-line command (#617): its catalog name, lower-case
        -- words such as 'export create' (Python checks the catalog itself).
        ELSIF key='command' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
        -- A failure's category and an incident's kind: identifier words whose
        -- closed sets Python owns (observability.FailureKind, IncidentKind).
        ELSIF key IN ('failure_kind','incident_kind') THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived',
                'purging','purge_cleanup_failed','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid','ministry_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key IN ('ministry_duids','previous_ministry_duids','added_ministry_duids','removed_ministry_duids') THEN
            IF jsonb_typeof(value)<>'array' THEN RETURN false; END IF;
            previous_ministry:=0;
            FOR ministry IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(ministry)<>'number' OR ministry#>>'{}'!~'^[0-9]{1,10}$'
                THEN RETURN false; END IF;
                IF (ministry#>>'{}')::bigint NOT BETWEEN previous_ministry+1 AND 2147483647
                THEN RETURN false; END IF;
                previous_ministry:=(ministry#>>'{}')::bigint;
            END LOOP;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        -- Work stopped by a time limit (#293): the task type and the limit.
        ELSIF key='task_type' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='what' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('read_guard','lease','retention_budget','drive_copy_budget',
                'drive_retry_budget','drive_request','drive_probe_wait',
                'statement_timeout','lock_timeout','transaction_timeout','mail_helper','source_helper','provider_check',
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget',
                'archive_check','digest_chart') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker',
                'archive_check_worker','chart_render_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        -- A discarded setup load (#1353): why it was discarded and how the
        -- load had ended (audit.schemas.VOID_REASONS, VOIDED_TASK_STATES).
        ELSIF key='void_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('key_changed','organization_changed','reload') THEN RETURN false; END IF;
        ELSIF key='task_state' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('succeeded','failed') THEN RETURN false; END IF;
        -- An export's report kind and file format (#1398): the closed sets
        -- export_report_known admits (audit.schemas.EXPORT_REPORTS, EXPORT_FORMATS).
        ELSIF key='report' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('participation','additional_information',
                'family_directory','postal_outreach','ministry','financial','family_test_names',
                'family_timeline','campaign_comparison','campaign_archive') THEN RETURN false; END IF;
        ELSIF key='format' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('csv','xlsx','pdf','png') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
        -- A response list's sort order (#851): its closed sort token, a column
        -- key ascending or '-key' descending (audit.schemas.REPORT_SORTS).
        ELSIF key='report_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('family','-family',
                'duid','-duid','envelope','-envelope','submitted','-submitted',
                'submissions','-submissions','opened','-opened','progressed','-progressed',
                'invited','-invited','link','-link','last','-last','mailing','-mailing',
                'problem','-problem','staff_entered','-staff_entered') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key='directory_phone' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        -- The directory's Response filter (#933): its closed choices, plus the
        -- older yes and no that earlier entries recorded.
        ELSIF key='directory_response' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no','submitted',
                'more-than-once','started','progressed','opened-only','never-opened',
                'link-followed','link-not-followed','not-invited','not-submitted') THEN RETURN false; END IF;
        -- The directory's ParishSoft data to check filter (#933).
        ELSIF key='directory_data_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','anything','mailing-name','envelope') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid','duid_desc',
                'invited','invited_desc','link','link_desc','opened','opened_desc',
                'progressed','progressed_desc','submitted','submitted_desc','last','last_desc',
                'submissions','submissions_desc') THEN RETURN false; END IF;
        -- How campaign mail can reach the listed Families (#388 L1).
        ELSIF key='directory_reach' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','email','mail','neither') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

CREATE OR REPLACE FUNCTION public.stewardship_export_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- The one admitted change (#1398, migration 0109): once an ended
    -- export's saved data is discarded, its discard, run as the schema
    -- owner, clears the request's or the latest-data resolution's link to
    -- the fact generation it used, so fact compaction may remove it.
    IF TG_OP='UPDATE'
       AND TG_TABLE_NAME IN ('stewardship_export_request','stewardship_exact_export_resolution')
       AND to_jsonb(OLD)->>'fact_set_id' IS NOT NULL
       AND to_jsonb(NEW)->'fact_set_id'='null'::jsonb
       AND to_jsonb(NEW)-'fact_set_id'=to_jsonb(OLD)-'fact_set_id'
       AND pg_has_role(current_user,(SELECT relowner FROM pg_class WHERE oid=TG_RELID),'USAGE')
       AND EXISTS(SELECT 1 FROM stewardship_export_discard d
           WHERE (TG_TABLE_NAME='stewardship_export_request'
                  AND d.request_id=(to_jsonb(OLD)->>'id')::uuid)
              OR (TG_TABLE_NAME='stewardship_exact_export_resolution'
                  AND d.exact_request_id=(to_jsonb(OLD)->>'request_id')::uuid)) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Export history is immutable' USING ERRCODE = '23514';
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_export_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
        snapshot stewardship_information_export_snapshot%ROWTYPE;
        directory stewardship_directory_export_snapshot%ROWTYPE;
        ministry stewardship_ministry_export_snapshot%ROWTYPE;
        financial stewardship_financial_export_snapshot%ROWTYPE;
        names stewardship_family_test_names_export_snapshot%ROWTYPE;
        timeline stewardship_timeline_export_snapshot%ROWTYPE;
        comparison stewardship_comparison_export_snapshot%ROWTYPE;
        handoff boolean; inputs_valid boolean:=false;
BEGIN
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    SELECT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution x
        JOIN stewardship_exact_export_request r ON r.id=x.request_id
        WHERE x.export_id=NEW.id AND x.fact_set_id=NEW.fact_set_id
          AND ROW(NEW.campaign_id,NEW.requester_id,NEW.request_key,NEW.configuration_id,NEW.format,NEW.browser_timezone)=
              ROW(r.campaign_id,r.requester_id,NEW.id,r.configuration_id,r.format,r.browser_timezone)
          AND stewardship_fact_live(x.run_id,x.fence,x.worker_id)) INTO handoff;
    IF NEW.report='participation' THEN
        SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id FOR SHARE;
        inputs_valid:=facts.id IS NOT NULL AND facts.state='ready'
            AND facts.campaign_id=NEW.campaign_id
            AND NEW.parameters=jsonb_build_object('population_scope',facts.population_scope,
                'sort','date_asc','selected_ids','[]'::jsonb,'filters','{}'::jsonb);
    ELSIF NEW.report='ministry' THEN
        SELECT * INTO ministry FROM stewardship_ministry_export_snapshot WHERE id=NEW.ministry_snapshot_id;
        inputs_valid:=ministry.id IS NOT NULL AND ministry.campaign_id=NEW.campaign_id
            AND NEW.parameters=ministry.parameters AND NEW.authorization_scope=ministry.authorization_scope
            AND (ministry.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.ministry_snapshot_id=ministry.id AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='additional_information' THEN
        SELECT * INTO snapshot FROM stewardship_information_export_snapshot WHERE id=NEW.information_snapshot_id;
        inputs_valid:=snapshot.id IS NOT NULL AND snapshot.campaign_id=NEW.campaign_id
            AND NEW.parameters=snapshot.parameters
            AND (snapshot.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.information_snapshot_id=snapshot.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report IN ('family_directory','postal_outreach') THEN
        SELECT * INTO directory FROM stewardship_directory_export_snapshot WHERE id=NEW.directory_snapshot_id;
        inputs_valid:=directory.id IS NOT NULL AND directory.campaign_id=NEW.campaign_id
            AND NEW.parameters=directory.parameters
            AND (NEW.report='postal_outreach')=(directory.parameters->>'postal')::boolean
            AND (directory.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.directory_snapshot_id=directory.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='financial' THEN
        SELECT * INTO financial FROM stewardship_financial_export_snapshot WHERE id=NEW.financial_snapshot_id;
        inputs_valid:=financial.id IS NOT NULL AND financial.campaign_id=NEW.campaign_id
            AND NEW.parameters=financial.parameters
            AND (financial.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.financial_snapshot_id=financial.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='family_test_names' THEN
        SELECT * INTO names FROM stewardship_family_test_names_export_snapshot
            WHERE id=NEW.family_test_names_snapshot_id;
        inputs_valid:=names.id IS NOT NULL AND names.campaign_id=NEW.campaign_id
            AND NEW.parameters=names.parameters
            AND (names.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.family_test_names_snapshot_id=names.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='family_timeline' THEN
        -- One Family's timeline (migration 0063): Administrator only, as
        -- the page's full view, so any current Administrator may also
        -- regenerate another's capture.
        SELECT * INTO timeline FROM stewardship_timeline_export_snapshot WHERE id=NEW.timeline_snapshot_id;
        inputs_valid:=timeline.id IS NOT NULL AND timeline.campaign_id=NEW.campaign_id
            AND NEW.parameters=timeline.parameters
            AND stewardship_export_authorized_v1(NEW.requester_id,true);
    ELSIF NEW.report='campaign_comparison' THEN
        -- The Campaign comparison (migration 0071): its own capture, for
        -- the requester who captured it, an Administrator, or a requester
        -- of an earlier export of it, as the financial capture.
        SELECT * INTO comparison FROM stewardship_comparison_export_snapshot
            WHERE id=NEW.comparison_snapshot_id;
        inputs_valid:=comparison.id IS NOT NULL AND comparison.campaign_id=NEW.campaign_id
            AND NEW.parameters=comparison.parameters
            AND (comparison.actor_id=NEW.requester_id
                OR stewardship_export_authorized_v1(NEW.requester_id,true)
                OR EXISTS(SELECT 1 FROM stewardship_export_request prior
                    WHERE prior.comparison_snapshot_id=comparison.id
                      AND prior.requester_id=NEW.requester_id));
    ELSIF NEW.report='campaign_archive' THEN
        -- The campaign archive workbook (migration 0084): an Administrator's
        -- XLSX of one ready historical fact set, cut off at a day that set
        -- holds, final or partial, named for its stewardship year. The
        -- worker reads the submissions itself under those pins.
        SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id FOR SHARE;
        inputs_valid:=facts.id IS NOT NULL AND facts.state='ready'
            AND facts.population_scope='historical' AND facts.campaign_id=NEW.campaign_id
            AND NEW.format='xlsx' AND jsonb_typeof(NEW.parameters)='object'
            AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
                =ARRAY['cutoff_date','final','year']
            AND jsonb_typeof(NEW.parameters->'final')='boolean'
            AND jsonb_typeof(NEW.parameters->'year')='number'
            AND NEW.parameters->>'year' ~ '^2[01][0-9]{2}$'
            AND jsonb_typeof(NEW.parameters->'cutoff_date')='string'
            AND NEW.parameters->>'cutoff_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            AND EXISTS(SELECT 1 FROM stewardship_daily_fact d WHERE d.fact_set_id=facts.id
                AND d.local_date::text=NEW.parameters->>'cutoff_date')
            -- Only a closed or archived campaign's archive may be final.
            AND (NEW.parameters->'final'='false'::jsonb OR EXISTS(SELECT 1
                FROM stewardship_campaign c WHERE c.id=NEW.campaign_id
                  AND c.state IN ('closed','archived')))
            AND stewardship_export_authorized_v1(NEW.requester_id,true);
    END IF;
    IF inputs_valid IS DISTINCT FROM true OR NEW.actor_id IS DISTINCT FROM NEW.requester_id
       OR NOT stewardship_export_request_authorized_v1(NEW.requester_id,NEW)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR (NOT handoff AND NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id))
       OR (current_user='pk_stewardship_worker' AND NOT handoff)
       OR (NEW.report<>'ministry' AND NEW.authorization_scope<>'{"capability":"campaign_report"}'::jsonb)
       -- A capture an ended export's discard cleared is never used again
       -- (#1398, migration 0109): its file could not be made.
       OR '{}'::jsonb IN (snapshot.document,directory.document,ministry.document,
           financial.document,names.document,timeline.document,comparison.document)
       OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
           WHERE name=public.stewardship_timezone_name_v1(NEW.browser_timezone))
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
           AND t.root_id=t.id AND t.task_type='report_export' AND t.domain_request_id=NEW.id
           AND t.initiated_by_id=NEW.requester_id AND t.idempotency_key=NEW.id::text
           AND t.state='queued')
    THEN RAISE EXCEPTION 'Export request lacks exact authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_directory_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE postal boolean; phones boolean;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Directory export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Directory export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_directory_report_v2(NEW.campaign_id,NEW.parameters);
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Directory export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    -- Keep only the private contact columns the requested file renders
    -- (#388 L6; reports.directory_documents): the postal mail merge uses the
    -- address, and the code list uses phones only for reach 'neither' and the
    -- envelope number (#933). The keys stay, emptied, so the document keeps
    -- its shape; heads (names and head emails) are in both.
    postal:=coalesce((NEW.parameters->>'postal')::boolean,false);
    phones:=NOT postal AND NEW.parameters->'filters'->>'reach' IS NOT DISTINCT FROM 'neither';
    NEW.document:=jsonb_set(NEW.document,'{rows}',coalesce((SELECT jsonb_agg(
        e.r||CASE WHEN postal THEN jsonb_build_object('envelope',NULL) ELSE '{}'::jsonb END
           ||CASE WHEN postal THEN '{}'::jsonb ELSE jsonb_build_object('address','{}'::jsonb) END
           ||CASE WHEN phones THEN '{}'::jsonb ELSE jsonb_build_object('phones','[]'::jsonb) END
        ORDER BY e.ordinal)
        FROM jsonb_array_elements(NEW.document->'rows') WITH ORDINALITY e(r,ordinal)),
        '[]'::jsonb));
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_financial_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Financial export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    -- Administrators and Staff are exactly who hold financial detail; a
    -- Ministry leader never captures money, whatever the application asked.
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Financial export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_financial_report_v1(NEW.campaign_id,NEW.parameters,NULL,NULL);
    IF NEW.document IS NULL OR NEW.document ?| ARRAY['disabled','unavailable'] THEN
        RAISE EXCEPTION 'Financial export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

-- stewardship_information_export_capture_v2: 0064_report_local_days.sql's body plus the discard's one change.
CREATE OR REPLACE FUNCTION public.stewardship_information_export_capture_v2() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Information export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Information export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    -- Filters with a zone are browser-local days (#558); without one they are
    -- the campaign-zone days of a release from before 0064 (a rollback).
    NEW.document:=CASE WHEN NEW.parameters->'filters' ? 'zone'
        THEN stewardship_information_report_v2(NEW.campaign_id,NEW.parameters)
        ELSE stewardship_information_report_v1(NEW.campaign_id,NEW.parameters) END;
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Information export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

-- stewardship_ministry_export_capture_v2: 0064_report_local_days.sql's body plus the discard's one change.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_export_capture_v2() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE current_scope jsonb; filters jsonb; ministry_id integer; request_action text;
        selection bigint[]; packet boolean;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    current_scope:=stewardship_ministry_scope_v1(NEW.actor_id);
    IF current_scope IS NULL
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Ministry export capture is unavailable' USING ERRCODE='23514'; END IF;
    -- Closed parameters have identical meanings in HTML and immutable capture.
    filters:=NEW.parameters->'filters';
    request_action:=NEW.parameters->>'action';
    packet:=coalesce(request_action='packet',false);
    -- Only a packet carries a Ministry selection, and it always carries one.
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR NOT NEW.parameters ?& ARRAY['filters','ministry','action']
       OR NEW.parameters-ARRAY['filters','ministry','action','ministries']<>'{}'::jsonb
       OR (NEW.parameters ? 'ministries') IS DISTINCT FROM packet
       OR jsonb_typeof(filters) IS DISTINCT FROM 'object'
       OR NOT filters ?& ARRAY['search','activity','history','state','start','end','sort']
       OR filters-ARRAY['search','activity','history','state','start','end','sort','zone']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(filters) f WHERE jsonb_typeof(f.value)<>'string')
       OR octet_length(filters->>'search')>131072
       OR filters->>'activity' NOT IN ('any','active','inactive','unavailable')
       OR filters->>'history' NOT IN ('current','all')
       OR filters->>'state' NOT IN ('any','unresolved','new','assigned','in_progress',
           'resolved','closed_no_response','cancelled','superseded')
       OR filters->>'sort' NOT IN ('name','name_desc','newest','oldest','duid','duid_desc')
       OR request_action IS NULL
       OR request_action NOT IN ('summary','join','leave','packet')
    THEN RAISE EXCEPTION 'Invalid Ministry export parameters' USING ERRCODE='23514'; END IF;
    IF NEW.parameters->'ministry'<>'null'::jsonb THEN
        IF jsonb_typeof(NEW.parameters->'ministry')<>'number'
           OR NOT (NEW.parameters->>'ministry') ~ '^[1-9][0-9]{0,9}$'
           OR (NEW.parameters->>'ministry')::bigint>2147483647
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        ministry_id:=(NEW.parameters->>'ministry')::integer;
    END IF;
    IF packet THEN
        -- A packet's only choices are its Ministries and the history option;
        -- every other filter must hold its neutral value so one capture has
        -- exactly one meaning. JSON null selects every authorized Ministry.
        -- A packet has no dates, so its zone (#558) is blank when sent.
        IF coalesce(filters->>'zone','')<>''
           OR filters-'history'-'zone'<>jsonb_build_object('search','','activity','any',
               'state','any','start','','end','','sort','name')
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        IF NEW.parameters->'ministries'<>'null'::jsonb THEN
            IF jsonb_typeof(NEW.parameters->'ministries')<>'array'
               OR jsonb_array_length(NEW.parameters->'ministries') NOT BETWEEN 1 AND 200
               OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.parameters->'ministries') e
                   WHERE jsonb_typeof(e.value)<>'number'
                      OR NOT (e.value#>>'{}') ~ '^[1-9][0-9]{0,9}$'
                      OR (e.value#>>'{}')::bigint>2147483647)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
            selection:=ARRAY(SELECT (e.value#>>'{}')::bigint
                FROM jsonb_array_elements(NEW.parameters->'ministries')
                    WITH ORDINALITY e(value,position) ORDER BY e.position);
            -- Canonical means strictly ascending, which also forbids repeats.
            IF selection<>ARRAY(SELECT DISTINCT v FROM unnest(selection) v ORDER BY v)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    IF (ministry_id IS NULL) IS DISTINCT FROM (request_action IN ('summary','packet'))
       OR (request_action='summary' AND (filters->>'history'<>'current'
           OR filters->>'state'<>'any' OR filters->>'start'<>'' OR filters->>'end'<>''
           OR filters->>'sort' NOT IN ('name','name_desc','duid','duid_desc')))
       OR EXISTS(SELECT 1 FROM jsonb_each_text(filters) f
           WHERE f.key IN ('start','end') AND f.value<>'' AND (
               NOT f.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
               OR NOT pg_input_is_valid(f.value,'date')))
       OR (filters->>'start'<>'' AND filters->>'end'<>''
           AND filters->>'start'>filters->>'end')
       -- A zone (#558) is required with a date and always a known one.
       OR (filters ? 'zone' AND (filters->>'start'<>'' OR filters->>'end'<>'')
           AND filters->>'zone'='')
       OR (filters ? 'zone' AND filters->>'zone'<>''
           AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(filters->>'zone')))
    THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    IF packet THEN
        NEW.document:=stewardship_ministry_packet_v1(NEW.campaign_id,selection,
            filters->>'history'='all',(current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')));
        -- Scope is intersected, never trusted. An explicit selection must
        -- resolve to exactly the Ministries asked for: dropping one the caller
        -- cannot see would capture a packet that misstates its own request.
        IF selection IS NOT NULL AND NEW.document ? 'sections'
           AND jsonb_array_length(NEW.document->'sections')<>cardinality(selection)
        THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    ELSE
        -- Filters with a zone are browser-local days (#558); without one they
        -- are the campaign-zone days of a release from before 0064.
        NEW.document:=CASE WHEN filters ? 'zone'
            THEN stewardship_ministry_report_v3(NEW.campaign_id,filters,
            (current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
            ministry_id,request_action)
            ELSE stewardship_ministry_report_v1(NEW.campaign_id,filters,
            (current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
            ministry_id,request_action) END;
    END IF;
    IF NEW.document IS NULL OR NEW.document ?| ARRAY['disabled','unavailable']
       OR (NEW.document->'authorized'<>'true'::jsonb AND
           (ministry_id IS NOT NULL OR current_scope->'operational'<>'true'::jsonb))
    THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    -- Lifecycle authority intentionally covers the whole selected policy scope.
    -- Audit attribution instead names only Ministries present after filtering,
    -- including a named empty detail section but not an empty summary result.
    NEW.authorization_scope:=NEW.document->'authorization_scope' || jsonb_build_object(
        'result_ministries',coalesce((SELECT jsonb_agg((value->>'duid')::bigint
            ORDER BY (value->>'duid')::bigint)
            FROM jsonb_array_elements(NEW.document->
                CASE WHEN packet THEN 'sections' ELSE 'summaries' END)), '[]'::jsonb));
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

-- stewardship_family_test_names_export_capture_v1: 0037_family_test_names.sql's body plus the discard's one change.
CREATE OR REPLACE FUNCTION public.stewardship_family_test_names_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE chosen jsonb; captured jsonb;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Chosen-Family name captures are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id,true)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration s
           JOIN stewardship_campaign c ON c.id=s.current_campaign_id
           WHERE s.active_configuration_id=NEW.configuration_id AND s.mode='testing'
             AND c.id=NEW.campaign_id AND c.state='draft')
    THEN RAISE EXCEPTION 'Chosen-Family name capture is unavailable' USING ERRCODE='23514'; END IF;
    chosen:=NEW.parameters->'duids';
    captured:=NEW.document->'rows';
    -- The JSON types first, so the checks below never apply an object or
    -- array function to the wrong type.
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR jsonb_typeof(chosen) IS DISTINCT FROM 'array'
       OR jsonb_typeof(captured) IS DISTINCT FROM 'array'
    THEN RAISE EXCEPTION 'Chosen-Family name capture does not match its review'
        USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['duids','revision']
       OR (SELECT array_agg(k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['rows']
       OR coalesce(NEW.parameters->>'revision','')
           !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR jsonb_array_length(chosen) NOT BETWEEN 1 AND 10
       OR jsonb_array_length(captured)<>jsonb_array_length(chosen)
       OR NEW.row_count<>jsonb_array_length(chosen)
       OR (SELECT count(DISTINCT d) FROM jsonb_array_elements(chosen) d)<>jsonb_array_length(chosen)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(chosen) WITH ORDINALITY d(duid,n)
           LEFT JOIN jsonb_array_elements(captured) WITH ORDINALITY r(entry,n) ON r.n=d.n
           WHERE d.duid::text !~ '^[1-9][0-9]{0,17}$'
              OR CASE WHEN jsonb_typeof(r.entry)='object'
                 THEN r.entry-'duid'-'name'<>'{}'::jsonb
                      OR r.entry->'duid' IS DISTINCT FROM d.duid
                      OR jsonb_typeof(r.entry->'name') IS DISTINCT FROM 'string'
                 ELSE true END)
    THEN RAISE EXCEPTION 'Chosen-Family name capture does not match its review'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- stewardship_timeline_export_capture_v1: 0063_timeline_exports.sql's body plus the discard's one change.
CREATE OR REPLACE FUNCTION public.stewardship_timeline_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE event jsonb;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Timeline export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF current_user='pk_stewardship_worker'
       OR NOT stewardship_export_authorized_v1(NEW.actor_id,true)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f
           WHERE f.id=NEW.family_id AND f.campaign_id=NEW.campaign_id)
    THEN RAISE EXCEPTION 'Timeline export capture is unavailable' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['family_id','mode','rehearsal_epoch_id']
       OR NEW.parameters->>'family_id' IS DISTINCT FROM NEW.family_id::text
       OR NOT ((NEW.parameters->>'mode'='production'
               AND NEW.parameters->'rehearsal_epoch_id'='null'::jsonb)
           OR (NEW.parameters->>'mode'='testing'
               AND jsonb_typeof(NEW.parameters->'rehearsal_epoch_id')='string'
               AND EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch e
                   WHERE e.id::text=NEW.parameters->>'rehearsal_epoch_id'
                     AND e.campaign_id=NEW.campaign_id AND e.state='active')))
    THEN RAISE EXCEPTION 'Invalid timeline export parameters' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['as_of','events','family','mode','summary']
       OR NEW.document->>'mode' IS DISTINCT FROM NEW.parameters->>'mode'
       OR jsonb_typeof(NEW.document->'as_of') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'family') IS DISTINCT FROM 'object'
       OR jsonb_typeof(NEW.document->'events') IS DISTINCT FROM 'array'
       OR jsonb_typeof(NEW.document->'summary') IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document->'summary') k)
           IS DISTINCT FROM ARRAY['first_submitted_at','furthest_at','furthest_step',
               'last_email','last_seen_at','last_submitted_at','submissions']
       OR jsonb_typeof(NEW.document->'summary'->'submissions') IS DISTINCT FROM 'number'
       OR EXISTS(SELECT 1 FROM unnest(ARRAY['first_submitted_at','last_submitted_at',
               'furthest_step','furthest_at','last_seen_at']) field
           WHERE jsonb_typeof(NEW.document->'summary'->field) NOT IN ('string','null'))
       OR jsonb_typeof(NEW.document->'summary'->'last_email') NOT IN ('object','null')
       OR (jsonb_typeof(NEW.document->'summary'->'last_email')='object'
           AND (SELECT array_agg(k ORDER BY k)
               FROM jsonb_object_keys(NEW.document->'summary'->'last_email') k)
               IS DISTINCT FROM ARRAY['at','name','outcome'])
    THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document->'family') k)
           IS DISTINCT FROM ARRAY['duid','envelope','id','name','reach','reminder_workgroup']
       OR NEW.document->'family'->>'id' IS DISTINCT FROM NEW.family_id::text
       OR jsonb_typeof(NEW.document->'family'->'duid') IS DISTINCT FROM 'number'
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f WHERE f.id=NEW.family_id
           AND f.family_duid::text=NEW.document->'family'->>'duid')
       OR jsonb_typeof(NEW.document->'family'->'name') NOT IN ('string','null')
       OR jsonb_typeof(NEW.document->'family'->'envelope') NOT IN ('number','null')
       OR jsonb_typeof(NEW.document->'family'->'reach') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'family'->'reminder_workgroup') NOT IN ('string','null')
       -- One Family's timeline is small; a bound keeps a capture from
       -- growing without limit, as the Ministry capture's search bound does.
       OR octet_length(NEW.document::text)>131072
    THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    FOR event IN SELECT value FROM jsonb_array_elements(NEW.document->'events') LOOP
        IF jsonb_typeof(event) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514';
        END IF;
        IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(event) k)
               IS DISTINCT FROM ARRAY['at','detail','what']
           OR jsonb_typeof(event->'at') IS DISTINCT FROM 'string'
           OR jsonb_typeof(event->'what') IS DISTINCT FROM 'string'
           OR jsonb_typeof(event->'detail') IS DISTINCT FROM 'string'
        THEN RAISE EXCEPTION 'Invalid timeline export document' USING ERRCODE='23514'; END IF;
    END LOOP;
    NEW.created_at:=statement_timestamp();
    NEW.row_count:=jsonb_array_length(NEW.document->'events');
    RETURN NEW;
END $$;

-- stewardship_comparison_export_capture_v1: 0071_comparison_exports.sql's body plus the discard's one change.
CREATE OR REPLACE FUNCTION public.stewardship_comparison_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE width integer; item jsonb; value jsonb;
BEGIN
    -- An ended export's discard (#1398, migration 0109), run as the schema
    -- owner, may clear the document; nothing else is ever changed.
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Comparison export captures are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF current_user='pk_stewardship_worker'
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Comparison export capture is unavailable' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.parameters) k)
           IS DISTINCT FROM ARRAY['day']
       OR NOT (NEW.parameters->'day'='null'::jsonb
           OR (jsonb_typeof(NEW.parameters->'day')='number'
               AND NEW.parameters->>'day' ~ '^[1-9][0-9]{0,3}$'))
    THEN RAISE EXCEPTION 'Invalid comparison export parameters' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['campaign','charts','columns','compared_with','day',
               'financial','report_date','rows','tiles','updating']
       OR NEW.document->'day' IS DISTINCT FROM NEW.parameters->'day'
       OR jsonb_typeof(NEW.document->'campaign') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'compared_with') IS DISTINCT FROM 'string'
       OR jsonb_typeof(NEW.document->'financial') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(NEW.document->'updating') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(NEW.document->'report_date') IS DISTINCT FROM
           (CASE WHEN NEW.parameters->'day'='null'::jsonb THEN 'null' ELSE 'string' END)
       OR (NEW.document->'report_date'<>'null'::jsonb
           AND NEW.document->>'report_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       OR EXISTS(SELECT 1 FROM unnest(ARRAY['tiles','columns','rows','charts']) part
           WHERE jsonb_typeof(NEW.document->part) IS DISTINCT FROM 'array')
       OR jsonb_array_length(NEW.document->'columns')=0
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'columns') c
           WHERE jsonb_typeof(c)<>'string')
       -- A comparison is a few rows and charts of a few campaigns; a bound
       -- keeps a capture from growing without limit.
       OR octet_length(NEW.document::text)>524288
    THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
    width:=jsonb_array_length(NEW.document->'columns');
    FOR item IN SELECT r FROM jsonb_array_elements(NEW.document->'rows') r LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'array' OR jsonb_array_length(item)<>width THEN
            RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514';
        END IF;
        FOR value IN SELECT v FROM jsonb_array_elements(item) v LOOP
            IF NOT (jsonb_typeof(value) IN ('null','number','string')
                OR (jsonb_typeof(value)='object'
                    AND (SELECT array_agg(k) FROM jsonb_object_keys(value) k)=ARRAY['money']
                    AND jsonb_typeof(value->'money')='string'
                    AND value->>'money' ~ '^-?[0-9]{1,20}\.[0-9]{2}$')
                OR (jsonb_typeof(value)='object'
                    AND (SELECT array_agg(k) FROM jsonb_object_keys(value) k)=ARRAY['date']
                    AND jsonb_typeof(value->'date')='string'
                    AND value->>'date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'))
            THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
        END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'tiles') t
        WHERE jsonb_typeof(t) IS DISTINCT FROM 'object'
           OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(t) k)
               IS DISTINCT FROM ARRAY['label','lines','value']
           OR jsonb_typeof(t->'label')<>'string' OR jsonb_typeof(t->'value')<>'string'
           OR jsonb_typeof(t->'lines')<>'array'
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(t->'lines') l
               WHERE jsonb_typeof(l)<>'string'))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'charts') c
        WHERE jsonb_typeof(c) IS DISTINCT FROM 'object'
           OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(c) k)
               IS DISTINCT FROM ARRAY['axis','kind','last_day','lines','title']
           OR jsonb_typeof(c->'title')<>'string' OR jsonb_typeof(c->'axis')<>'string'
           OR coalesce(c->>'kind','') NOT IN ('count','percent','money')
           OR jsonb_typeof(c->'last_day')<>'number'
           OR jsonb_typeof(c->'lines')<>'array'
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(c->'lines') l
               WHERE jsonb_typeof(l) IS DISTINCT FROM 'object'
                  OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(l) k)
                      IS DISTINCT FROM ARRAY['name','points','role']
                  OR jsonb_typeof(l->'name')<>'string'
                  OR coalesce(l->>'role','') NOT IN ('current','last','average','other')
                  OR jsonb_typeof(l->'points')<>'array'
                  OR EXISTS(SELECT 1 FROM jsonb_array_elements(l->'points') p
                      WHERE jsonb_typeof(p) IS DISTINCT FROM 'array'
                         OR jsonb_array_length(p)<>2
                         OR jsonb_typeof(p->0)<>'number'
                         OR jsonb_typeof(p->1) NOT IN ('number','null'))))
    THEN RAISE EXCEPTION 'Invalid comparison export document' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.row_count:=jsonb_array_length(NEW.document->'rows');
    RETURN NEW;
END $$;

-- The deploy-time sweep: every export already ended, with the longest
-- window so nothing still inside a longer one is touched, in bounded
-- batches until a batch discards nothing more. Safe to repeat: a discarded
-- export is skipped, and one that cannot be discarded is left for the
-- scheduler (the NOTICE counts the last batch's skips).
DO $sweep$
DECLARE result jsonb; total integer:=0; captures integer; pins integer;
BEGIN
    PERFORM set_config('stewardship.export_window_minutes','240',true);
    LOOP
        result:=stewardship_export_discard_batch_v1(1000,true);
        total:=total+(result->>'discarded')::integer;
        EXIT WHEN (result->>'discarded')::integer=0;
    END LOOP;
    SELECT coalesce(sum(documents),0), coalesce(sum(d.pins),0) INTO captures, pins
        FROM stewardship_export_discard d WHERE d.by_deploy;
    RAISE NOTICE 'Migration 0109: discarded the saved data of % ended exports (% captures cleared, % pins released; % left for the scheduler)',
        total, captures, pins, result->>'skipped';
    PERFORM set_config('stewardship.export_window_minutes','',true);
END $sweep$;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
DECLARE name text;
BEGIN
    IF to_regclass('public.stewardship_export_discard') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='export_immutable'
           AND tgrelid='public.stewardship_export_discard'::regclass AND tgenabled='O') THEN
        RAISE EXCEPTION 'Migration 0109: stewardship_export_discard is not installed as declared';
    END IF;
    -- The four discard functions run as the owner and no one may call them
    -- but through a runtime grant.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND p.oid IN ('public.stewardship_export_discard_v1(uuid,boolean)'::regprocedure,
              'public.stewardship_exact_export_discard_v1(uuid,boolean)'::regprocedure,
              'public.stewardship_export_discard_batch_v1(integer,boolean)'::regprocedure,
              'public.stewardship_export_discard_due_v1(integer)'::regprocedure)
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>4 THEN
        RAISE EXCEPTION 'Migration 0109: the discard functions are not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proname IN ('stewardship_export_window_v1','stewardship_export_ended_v1',
              'stewardship_exact_export_ended_v1','stewardship_export_capture_cleared_v1'))<>4 THEN
        RAISE EXCEPTION 'Migration 0109: the window and ended checks are not installed as declared';
    END IF;
    -- The window falls back to its longest, never shorter.
    IF stewardship_export_window_v1()<>interval '240 minutes' THEN
        RAISE EXCEPTION 'Migration 0109: an unset window is not 240 minutes';
    END IF;
    -- Every capture table keeps its enabled immutability trigger, whose
    -- function admits the discard's one change.
    FOREACH name IN ARRAY ARRAY['information','directory','ministry','financial',
        'family_test_names','timeline','comparison'] LOOP
        -- (A table a later closure file makes is absent while the users
        -- swap is pending; this file then runs again with it.)
        CONTINUE WHEN to_regclass('public.stewardship_'||name||'_export_snapshot') IS NULL;
        IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname=name||'_export_capture'
               AND tgrelid=('public.stewardship_'||name||'_export_snapshot')::regclass
               AND tgenabled='O'
               AND position('stewardship_export_capture_cleared_v1' IN
                   (SELECT prosrc FROM pg_proc WHERE oid=tgfoid))>0) THEN
            RAISE EXCEPTION 'Migration 0109: the % capture trigger is not installed as declared', name;
        END IF;
    END LOOP;
    -- The replaced functions carry their change and stay invokers.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND (
            (p.proname='stewardship_export_disposable_v1'
             AND position('stewardship_export_window_v1()' IN p.prosrc)>0)
         OR (p.proname='stewardship_fact_disposable'
             AND position('d.exact_request_id=r.id' IN p.prosrc)>0
             AND position('stewardship_exact_export_resolution WHERE fact_set_id=identifier' IN p.prosrc)>0)
         OR (p.proname='stewardship_exact_source_pin_guard_v1'
             AND position('d.exact_request_id=r.id' IN p.prosrc)>0)
         OR (p.proname='stewardship_export_immutable_v1'
             AND position('stewardship_export_discard d' IN p.prosrc)>0)
         OR (p.proname='stewardship_export_request_guard_v1'
             AND position('snapshot.document,directory.document' IN p.prosrc)>0)))<>5 THEN
        RAISE EXCEPTION 'Migration 0109: the replaced export functions are not installed as declared';
    END IF;
    IF NOT public.stewardship_safe_context_v1('action',
            '{"report": "family_directory", "format": "pdf", "outcome": "started"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"report": "anything"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"format": "docx"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"report": 1}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('action',
            '{"void_reason": "reload", "sign_in_age_seconds": 3600}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('failure',
            '{"failure": "export_discard", "count": 2, "outcome": "retry"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('refusal',
            '{"route": "admin:home", "reason": "denied"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0109: stewardship_safe_context_v1 does not admit report and format as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'Migration 0109: stewardship_safe_context_v1 is not installed as declared';
    END IF;
END
$check$;
