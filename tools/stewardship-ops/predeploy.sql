-- Read-only checks before a Production upgrade, run by predeploy-check.sh
-- (docs/guides/stewardship-operator-scripts.md). :'tz' is the time zone the
-- times are shown in. The last line, "predeploy: blocking=N", counts what an
-- upgrade would interrupt: task runs that are running and messages that are
-- being submitted. Queued and retry-waiting work is listed but not counted;
-- stopping the background services leaves it for after the upgrade.
BEGIN READ ONLY;
SELECT to_char(now() AT TIME ZONE :'tz', 'YYYY-MM-DD HH24:MI:SS') AS now_local;

-- Background task runs not finished (the TaskRun nonterminal states). A
-- running source refresh should finish first.
SELECT task_type, state, count(*) AS n,
       to_char(min(created_at) AT TIME ZONE :'tz', 'YYYY-MM-DD HH24:MI')
           AS oldest,
       bool_or(initiated_by_id IS NOT NULL) AS manual
FROM stewardship_task_run
WHERE state IN ('queued', 'running', 'retry_wait', 'abandoned')
GROUP BY task_type, state
ORDER BY task_type, state;

-- Outbox messages not yet in a final or uncertain state.
SELECT purpose, mode, state, count(*) AS n
FROM stewardship_outbox_message
WHERE state IN ('pending', 'submitting', 'retry_wait')
GROUP BY purpose, mode, state
ORDER BY purpose, mode, state;

-- Family sessions seen in the last 15 minutes.
SELECT mode, count(*) AS active_last_15m
FROM stewardship_family_session
WHERE presence_at > now() - interval '15 minutes'
GROUP BY mode
ORDER BY mode;

\pset tuples_only on
\pset format unaligned
SELECT 'predeploy: blocking='
       || ((SELECT count(*) FROM stewardship_task_run WHERE state = 'running')
           + (SELECT count(*) FROM stewardship_outbox_message
              WHERE state = 'submitting'));
ROLLBACK;
