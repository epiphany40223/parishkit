-- One read-only line for send-monitor.sh
-- (docs/guides/stewardship-operator-scripts.md): the outbox messages of one
-- purpose and mode created since :'since', by state; how many were accepted
-- by the mail provider in the last minute; and the first and last
-- acceptance. Parameters: :'purpose', :'mode', :'since' and :'tz' (the time
-- zone the times are shown in). Run with -At.
BEGIN READ ONLY;
WITH message AS (
    SELECT id, state
    FROM stewardship_outbox_message
    WHERE purpose = :'purpose' AND mode = :'mode'
      AND created_at >= :'since'::timestamptz),
accepted AS (
    SELECT e.submitted_at, e.created_at
    FROM stewardship_outbox_event e
    JOIN message m ON m.id = e.message_id
    WHERE e.reason = 'smtp_accepted')
SELECT to_char(now() AT TIME ZONE :'tz', 'HH24:MI:SS') AS at,
       coalesce((SELECT string_agg(state || '=' || n, ' ' ORDER BY state)
                 FROM (SELECT state, count(*) AS n FROM message
                       GROUP BY state) s), 'none') AS states,
       'accepted_last_min='
           || (SELECT count(*) FROM accepted
               WHERE created_at > now() - interval '1 minute')
           AS accepted_last_min,
       'first=' || coalesce((SELECT to_char(min(submitted_at) AT TIME ZONE :'tz',
                                            'HH24:MI:SS') FROM accepted), '-')
           AS first_accept,
       'last=' || coalesce((SELECT to_char(max(created_at) AT TIME ZONE :'tz',
                                           'HH24:MI:SS') FROM accepted), '-')
           AS last_accept;
ROLLBACK;
