# Stewardship mail send report

After a large send, such as the launch invitations, this read-only report
shows how long the send took and where the time went. Use it to decide
whether the batched mail helper's caps should change. It reads the send
statistics that every Family outcome records (#284); see
[Family mail dispatch](stewardship-family-mail-dispatch.md#falling-back-to-one-helper-per-message)
for the transports, and the launch runbooks'
[measuring the launch send](stewardship-launch-runbooks.md#measuring-the-launch-send)
for when to run it at launch and what to record. Statistics are
observations only: they never affect an
outcome, and they hold no addresses, names or provider text. The database
admits only whole numbers, yes/no values, a random helper id and a closed
list of fixed words (such as `batched` or `cap_age`) there, at most 48 of
them per message.

## Running it

Save the SQL below as `mail-send-report.sql` on the host. Then run it with
the operator login, as the backup runbook does for its queries, choosing the
time window (UTC is safest). An empty `purpose` means all mail the Family
mail worker sends (`initial`, `reminder`, `receipt`, `family_test`,
`daily_digest`, `weekly_digest`); name one of those to narrow it. Name
`operational` or `security_event` to see Administrator alerts instead,
which are never included otherwise (they record helper statistics but no
transport). `definition` narrows to one schedule definition (its UUID):

```sh
docker compose ... exec -T postgres psql --username pk_stewardship_operator \
  --dbname DATABASE_NAME -v ON_ERROR_STOP=1 \
  -v since='2026-10-03 12:00Z' -v until='2026-10-04 00:00Z' \
  -v purpose='' -v definition='' -f - < mail-send-report.sql
```

The report runs in one transaction that it rolls back at the end. Its only
object is a temporary view, and after that the transaction is read-only, so
it changes nothing. Outcomes settled before statistics were recorded appear
in the counts, with `without_stats`, but not in the timings.

## What it shows

1. **Overview**: outcomes, messages, the wall clock from the first
   submission to the last outcome, and accepted messages per minute.
2. **Outcomes** by transport and outcome reason.
3. **Throughput** per 5 minutes.
4. **Phase timings**: p50, p90, p99 and maximum, in milliseconds, per
   transport. Each phase counts only the messages that went through it
   (`n`): a message that reused its connection records no connect, AUTH
   or token time, and one that reused its helper records no spawn time, so
   the setup percentiles describe real setups:
   - `wait_ms`: from the message's due time to the worker starting it.
   - `request_ms`: from starting it to the helper request, including
     preparation and committing the submission.
   - `spawn_ms`: starting a helper process.
   - `helper_ms`: from request to result, for batched sending.
   - `submit_ms`: the whole helper call.
   - `token_ms`, `connect_ms` (TCP, TLS and EHLO) and `auth_ms`: when this
     message opened a connection.
   - `envelope_ms` (MAIL and RCPT) and `data_ms` (DATA through its reply).
   - `smtp_ms`: everything inside the helper for this message.
   - `total_ms`: from starting the message to its outcome.
   - `conn_age_ms`: how old a reused connection was.
5. **Connections and helpers**: messages per helper and per connection, the
   connection reuse ratio and token fetches.
6. **Lifetimes** of connections and helpers.
7. **Close and retire reasons**:
   - `conn_replaced` (`cap_messages`, `cap_age`, `stale`) and `conn_end`
     (`non_accepted`, `connect_failed`, `token_failed`) for connections.
   - `prev_helper_end` (`cap_messages`, `cap_age`, `idle`, `key_change`,
     `exited`, `close`) and `helper_end` (`limit`, `outage`, `systemic`,
     `unknown`, `kill`) for helpers.
   - `helper_restart`: a helper was gone before starting a message, so a
     fresh one took it.
   - A worker that died cannot record anything; its message is recovered
     as delivery unknown, without statistics.
8. **Limits, retries and unknowns**, and how long each limit refusal held
   its message before the next attempt.
9. **What batching saved**: the setup time actually spent against the same
   setup paid once per message.

## Tuning the caps

The caps in effect are recorded with every message (`cap_conn_msgs`,
`cap_conn_s`, `cap_helper_msgs`, `cap_helper_s`, `cap_idle_s`,
`cap_helper_idle_s`), so runs with different settings can be compared.

- **Connections per message.** If `conn_replaced = cap_messages` is common
  and connection setup (`connect_ms` plus `auth_ms`) is a large share of
  `total_ms`, a larger connection message cap saves time. If `stale` is
  common, Gmail closes idle connections sooner than the age cap. A lower
  `CONNECTION_SECONDS` avoids paying for the failed first try.
- **Helpers.** `prev_helper_end = cap_messages` or `cap_age` show helper
  rotation. With the helper start-up in the what-if section, they show what
  a larger batch would save. `idle` means the queue ran dry, not that the
  cap is too small.
- **Throughput.** Accepted per minute, per bucket, shows whether the send
  was limited by the mail worker (steady rate, low `wait_ms`) or by
  scheduling and limits (gaps, high `wait_ms`, limit holds).
- **Limits.** Many `daily` or `rate` refusals mean the send outran Gmail's
  limits; larger batches will not help there.

The caps are constants in the application (`CONNECTION_MESSAGES` and
`CONNECTION_SECONDS` in `family_delivery.py`; `SESSION_MESSAGES`,
`SESSION_SECONDS` and `HELPER_IDLE_SECONDS` in `family_delivery_worker.py`;
`PARENT_IDLE_SECONDS` in `family_delivery_process.py`), so changing one is
a reviewed release.

## The report

```sql
BEGIN;
CREATE TEMP VIEW send_outcome AS
SELECT e.created_at AS settled_at, e.submitted_at, e.message_id, e.attempt,
       e.reason, e.state, m.purpose,
       CASE WHEN e.evidence_note LIKE '{%'
            THEN (e.evidence_note::jsonb)->'stats' END AS stats
FROM stewardship_outbox_event e
JOIN stewardship_outbox_message m ON m.id = e.message_id
WHERE e.previous_state = 'submitting'
  AND (e.reason LIKE 'smtp%' OR e.reason = 'recovery_unknown')
  AND e.created_at >= :'since'::timestamptz
  AND e.created_at < :'until'::timestamptz
  AND (m.purpose = :'purpose' OR (:'purpose' = '' AND m.purpose IN
      ('initial', 'reminder', 'receipt', 'family_test', 'daily_digest',
       'weekly_digest')))
  AND (:'definition' = '' OR EXISTS (
      SELECT 1 FROM stewardship_schedule_occurrence o
      WHERE o.id = m.semantic_key AND o.definition_id::text = :'definition'));
SET LOCAL transaction_read_only = on;

-- 1. Overview
SELECT count(*) AS outcomes,
       count(DISTINCT message_id) AS messages,
       count(*) FILTER (WHERE reason = 'smtp_accepted') AS accepted,
       count(*) FILTER (WHERE stats IS NULL) AS without_stats,
       min(submitted_at) AS first_submit,
       max(settled_at) AS last_outcome,
       max(settled_at) - min(submitted_at) AS wall_clock,
       round((count(*) FILTER (WHERE reason = 'smtp_accepted'))::numeric
             / greatest(extract(epoch FROM max(settled_at) - min(submitted_at))
                        / 60, 0.001), 1) AS accepted_per_minute
FROM send_outcome;

-- 2. Outcomes by transport and reason
SELECT coalesce(stats->>'transport', 'none') AS transport, reason,
       count(*) AS outcomes
FROM send_outcome
GROUP BY 1, 2
ORDER BY 1, 2;

-- 3. Throughput per 5 minutes
SELECT to_timestamp(floor(extract(epoch FROM settled_at) / 300) * 300)
           AS bucket,
       count(*) AS outcomes,
       count(*) FILTER (WHERE reason = 'smtp_accepted') AS accepted,
       round((count(*) FILTER (WHERE reason = 'smtp_accepted')) / 5.0, 1)
           AS accepted_per_minute
FROM send_outcome
GROUP BY 1
ORDER BY 1;

-- 4. Phase timings (milliseconds)
SELECT stats->>'transport' AS transport, phase.key AS phase,
       count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY phase.value::numeric)
           AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY phase.value::numeric)
           AS p90,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY phase.value::numeric)
           AS p99,
       max(phase.value::numeric) AS max
FROM send_outcome, jsonb_each_text(stats) phase
WHERE stats IS NOT NULL AND right(phase.key, 3) = '_ms'
GROUP BY 1, 2
ORDER BY 1, 2;

-- 5. Connections and helpers
WITH used AS (
    SELECT stats->>'transport' AS transport,
           coalesce(stats->>'helper_id', message_id::text || ':' || attempt)
               AS helper,
           stats->>'conn_seq' AS conn, stats
    FROM send_outcome
    WHERE stats IS NOT NULL)
SELECT transport,
       count(*) AS messages,
       count(DISTINCT helper) AS helpers,
       -- A message that never got a connection (a token failure) has none.
       count(DISTINCT (helper, conn)) FILTER (WHERE conn IS NOT NULL)
           AS connections,
       round(count(*)::numeric / nullif(count(DISTINCT helper), 0), 1)
           AS messages_per_helper,
       round((count(*) FILTER (WHERE conn IS NOT NULL))::numeric
             / nullif(count(DISTINCT (helper, conn))
                      FILTER (WHERE conn IS NOT NULL), 0), 1)
           AS messages_per_connection,
       round(avg((stats->>'conn_reused')::boolean::int), 3)
           AS connection_reuse_ratio,
       count(*) FILTER (WHERE (stats->>'token_refreshed')::boolean)
           AS token_fetches
FROM used
GROUP BY 1
ORDER BY 1;

-- 6. Lifetimes (milliseconds)
WITH used AS (
    SELECT stats->>'transport' AS transport,
           coalesce(stats->>'helper_id', message_id::text || ':' || attempt)
               AS helper,
           stats->>'conn_seq' AS conn, submitted_at, settled_at, stats
    FROM send_outcome
    WHERE stats IS NOT NULL),
lifetime AS (
    SELECT 'connection' AS what, transport, count(*) AS messages,
           max(coalesce((stats->>'conn_age_ms')::numeric, 0)
               + (stats->>'smtp_ms')::numeric) AS lifetime_ms
    FROM used
    WHERE conn IS NOT NULL
    GROUP BY transport, helper, conn
    UNION ALL
    SELECT 'helper', transport, count(*),
           extract(epoch FROM max(settled_at) - min(submitted_at)) * 1000
    FROM used
    GROUP BY transport, helper)
SELECT what, transport, count(*) AS n,
       round(avg(messages), 1) AS messages_avg,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY lifetime_ms) AS p50_ms,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY lifetime_ms) AS p90_ms,
       max(lifetime_ms) AS max_ms
FROM lifetime
GROUP BY 1, 2
ORDER BY 1, 2;

-- 7. Close and retire reasons
SELECT stats->>'transport' AS transport, reason.key AS kind,
       reason.value AS reason, count(*) AS times
FROM send_outcome, jsonb_each_text(stats) reason
WHERE stats IS NOT NULL
  AND reason.key IN ('conn_replaced', 'conn_end', 'prev_helper_end',
                     'helper_end', 'helper_restart')
GROUP BY 1, 2, 3
ORDER BY 1, 2, 4 DESC;

-- 8. Limits, retries and unknowns, and how long each limit held
SELECT count(*) FILTER (WHERE stats ? 'limit') AS limit_refusals,
       count(*) FILTER (WHERE stats->>'limit' = 'daily') AS daily,
       count(*) FILTER (WHERE stats->>'limit' = 'rate') AS rate,
       count(*) FILTER (WHERE stats->>'limit' = 'message') AS message,
       count(*) FILTER (WHERE state = 'retry_wait') AS retries_scheduled,
       count(*) FILTER (WHERE state = 'permanent_failure') AS failed,
       count(*) FILTER (WHERE state = 'delivery_unknown') AS delivery_unknown
FROM send_outcome;

SELECT held.limit_kind, count(*) AS refusals,
       count(held.held) AS retried,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY held.held) AS p50_held,
       max(held.held) AS max_held,
       sum(held.held) AS total_held
FROM (
    SELECT o.stats->>'limit' AS limit_kind,
           (SELECT min(n.submitted_at) FROM stewardship_outbox_event n
            WHERE n.message_id = o.message_id
              AND n.submitted_at > o.settled_at) - o.settled_at AS held
    FROM send_outcome o
    WHERE o.stats ? 'limit') held
GROUP BY 1
ORDER BY 1;

-- 9. What batching saved (seconds): setup actually spent, and the same
-- setup paid once per message. Helper overhead is the time around the SMTP
-- work: start-up and pipes for a helper's first message, pipes after that.
SELECT stats->>'transport' AS transport,
       count(*) AS messages,
       count(*) FILTER (WHERE stats ? 'connect_ms') AS connections_opened,
       round(coalesce(sum((stats->>'token_ms')::numeric), 0) / 1000, 1)
           AS token_s,
       round(coalesce(sum(coalesce((stats->>'connect_ms')::numeric, 0)
                          + coalesce((stats->>'auth_ms')::numeric, 0)), 0)
             / 1000, 1) AS connect_auth_s,
       -- Phases are recorded only when they happened, so these averages
       -- are per real setup.
       round(count(*) * avg((stats->>'token_ms')::numeric) / 1000, 1)
           AS token_s_if_per_message,
       round(count(*) * avg(coalesce((stats->>'connect_ms')::numeric, 0)
                            + coalesce((stats->>'auth_ms')::numeric, 0))
                 FILTER (WHERE stats ? 'connect_ms') / 1000, 1)
           AS connect_auth_s_if_per_message,
       round(avg((stats->>'submit_ms')::numeric - (stats->>'smtp_ms')::numeric)
             FILTER (WHERE (stats->>'helper_index')::int = 1))
           AS first_message_overhead_ms,
       round(avg((stats->>'submit_ms')::numeric - (stats->>'smtp_ms')::numeric)
             FILTER (WHERE (stats->>'helper_index')::int > 1))
           AS later_message_overhead_ms
FROM send_outcome
WHERE stats IS NOT NULL AND stats ? 'smtp_ms'
GROUP BY 1
ORDER BY 1;

ROLLBACK;
```
