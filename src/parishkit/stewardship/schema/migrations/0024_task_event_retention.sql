-- Frozen forward migration file 0024 (the repository-wide file sequence):
-- prune the liveness events of long-finished task runs (#386, L2). It is
-- installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced guard equals the
-- fresh-install baseline's (schema/functions.sql).
--
-- Every lease renewal (every 20 seconds per running task) and every progress
-- update writes one immutable stewardship_task_event row, and nothing ever
-- removed one. stewardship_task_event_prune_v1 deletes the 'heartbeat' and
-- 'progress' events of up to `runs` task runs that finished (succeeded,
-- failed or cancelled) between retain_days + window_days and retain_days
-- ago, never an event another row references, and returns how many events
-- went. Claims, transitions, expiry and recovery events, and every task
-- run, are kept.
--
-- Its cost stays proportional to the batch: the runs come from the new
-- partial index task_terminal_updated (terminal runs by updated_at, which a
-- terminal run never changes again), only within the window, and each run's
-- events through task_event_version (run, version). Runs older than the
-- window are not revisited: the hourly maintenance pass keeps up, and after
-- a longer outage an operator calls it with a wider window until it returns
-- 0 (the runtime guide's task event retention step). It runs in its
-- caller's short transaction, never under the work-order lock (it touches
-- only finished runs' liveness rows, which no writer changes).
--
-- It is SECURITY DEFINER with a fixed search_path, callable by the general
-- worker (EXECUTE from database-grants, runtime_functions) and the schema
-- owner alone; it refuses fewer than 7 retained days, a window outside 1 to
-- 3,650 days, or a batch outside 1 to 200 runs.
--
-- The append-only guard (stewardship_task_event_immutable_v1) is replaced so
-- that a DELETE passes only inside that function: it runs as the schema
-- owner and sets a transaction-local flag around its one DELETE, and the
-- guard also checks the event's action and its run's terminal state (the
-- age floor is the function's). UPDATE, and any other DELETE, are still
-- refused. The replaced guard also gains a fixed search_path; the baseline
-- carries the same body.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_task_event_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                -- Only stewardship_task_event_prune_v1 deletes: it runs as
                -- the schema owner and sets the flag for its own statement,
                -- and only liveness events of a finished run go (#386). The
                -- age floor (at least 7 days) is that function's to enforce.
                IF TG_OP = 'DELETE'
                   AND current_setting('stewardship.event_prune', true) = 'on'
                   AND pg_has_role(current_user,
                                   (SELECT nspowner FROM pg_namespace
                                     WHERE nspname = 'public'), 'USAGE')
                   AND OLD.action IN ('heartbeat', 'progress')
                   AND EXISTS (SELECT 1 FROM public.stewardship_task_run r
                                WHERE r.id = OLD.run_id
                                  AND r.state IN ('succeeded', 'failed', 'cancelled')) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- Terminal runs by the time they finished (a terminal run is never updated
-- again), so the prune finds a window's runs without reading the others.
-- The predicate is written as PostgreSQL prints the model's declared index
-- (jobs/models.py), so the installed and declared indexes compare equal.
CREATE INDEX "task_terminal_updated" ON "stewardship_task_run" ("updated_at")
    WHERE ((state)::text = ANY (ARRAY[('succeeded'::character varying)::text,
        ('failed'::character varying)::text, ('cancelled'::character varying)::text]));

CREATE FUNCTION public.stewardship_task_event_prune_v1(retain_days integer, window_days integer, runs integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE removed integer;
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker prunes task events' USING ERRCODE='42501';
    END IF;
    IF retain_days IS NULL OR retain_days<7 OR window_days IS NULL OR window_days<1
       OR window_days>3650 OR runs IS NULL OR runs<1 OR runs>200 THEN
        RAISE EXCEPTION 'Task event retention needs at least 7 days, a bounded window and batch'
            USING ERRCODE='22023';
    END IF;
    PERFORM set_config('stewardship.event_prune','on',true);
    -- The candidate runs: through task_terminal_updated, within the window,
    -- and only those that still have liveness events (each probe reads one
    -- run's events through task_event_version).
    DELETE FROM public.stewardship_task_event e
     USING (SELECT r.id FROM public.stewardship_task_run r
             WHERE r.state IN ('succeeded','failed','cancelled')
               AND r.updated_at<statement_timestamp()-make_interval(days=>retain_days)
               AND r.updated_at>=statement_timestamp()
                   -make_interval(days=>retain_days+window_days)
               AND EXISTS (SELECT 1 FROM public.stewardship_task_event x
                            WHERE x.run_id=r.id AND x.action IN ('heartbeat','progress'))
             LIMIT runs) chosen
     WHERE e.run_id=chosen.id
       AND e.action IN ('heartbeat','progress')
       -- An export attempt names only claim events today; kept as a guard
       -- so a later reference to a liveness event can never be broken.
       AND NOT EXISTS (SELECT 1 FROM public.stewardship_export_attempt a
                        WHERE a.claim_event_id=e.id);
    GET DIAGNOSTICS removed = ROW_COUNT;
    PERFORM set_config('stewardship.event_prune','off',true);
    RETURN removed;
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_task_event_prune_v1(integer, integer, integer) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_task_event_prune_v1(integer, integer, integer) FROM PUBLIC;

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_task_event_prune_v1'
          AND pg_get_function_identity_arguments(p.oid)
              ='retain_days integer, window_days integer, runs integer'
          AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%retain_days<7%'
          AND p.prosrc LIKE '%runs>200%'
          AND p.prosrc LIKE '%e.action IN (''heartbeat'',''progress'')%'
          AND p.prosrc LIKE '%r.state IN (''succeeded'',''failed'',''cancelled'')%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))<>1 THEN
        RAISE EXCEPTION 'stewardship_task_event_prune_v1 was not installed as declared';
    END IF;
    -- The guard is invoker's rights (CREATE OR REPLACE resets any attribute
    -- it does not state) with its fixed search_path and the prune exception,
    -- limited to the two liveness actions of a terminal run.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_task_event_immutable_v1'
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%stewardship.event_prune%'
          AND p.prosrc LIKE '%OLD.action IN (''heartbeat'', ''progress'')%'
          AND p.prosrc LIKE '%r.state IN (''succeeded'', ''failed'', ''cancelled'')%'
          AND p.prosrc LIKE '%append-only%')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_trigger
                      WHERE tgname='stewardship_task_event_immutable_guard_v1'
                        AND tgrelid='public.stewardship_task_event'::regclass
                        AND tgenabled='O')
       OR NOT EXISTS (SELECT 1 FROM pg_indexes
                      WHERE schemaname='public' AND indexname='task_terminal_updated') THEN
        RAISE EXCEPTION 'The task event guard or its index was not installed as declared';
    END IF;
END
$check$;
