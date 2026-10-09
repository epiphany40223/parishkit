-- Frozen forward migration file 0033 (the repository-wide file sequence):
-- the Administrator's instruction for the source_retention_failing incident
-- also covers retention that keeps stopping at its limits (#833). It is
-- installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced function equals
-- the fresh-install baseline's (schema/operational_render.sql).
--
-- The incident now also opens when the newest refreshes all stopped their
-- retention at its time or lock limits with old work left, and its
-- instruction said only "skipped by the last three refreshes". The notice
-- content function (stewardship_ops_content_v1) mirrors the Python text
-- (jobs/operational_content.py) for the SQL writers, so it is replaced with
-- that one sentence changed and nothing else. It stays invoker's rights,
-- STABLE, with its fixed search_path.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_ops_content_v1(notice uuid, deployment_mode text)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE n stewardship_ops_notice%ROWTYPE; kind text; title text; status text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Operational mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT n FROM stewardship_ops_notice WHERE id=notice;
    SELECT incident.kind INTO STRICT kind FROM stewardship_ops_incident incident WHERE id=n.incident_id;
    title:=CASE kind
      WHEN 'database_unavailable' THEN 'Database unavailable'
      WHEN 'storage_integrity' THEN 'Storage integrity requires attention'
      WHEN 'task_failed' THEN 'Background task failed'
      WHEN 'system_failure' THEN 'System operation requires attention'
      WHEN 'source_refresh_failed' THEN 'Parish data refresh failed'
      WHEN 'source_stale' THEN 'Parish data refresh is overdue'
      WHEN 'source_tenant_mismatch' THEN 'Parish data organization mismatch'
      WHEN 'source_destructive_change' THEN 'Unexpected parish data loss'
      WHEN 'mail_provider_unavailable' THEN 'Email provider unavailable'
      WHEN 'scheduler_lag' THEN 'Scheduled work is overdue'
      WHEN 'worker_unavailable' THEN 'Background worker unavailable'
      WHEN 'admin_abuse' THEN 'Sustained administration login abuse'
      WHEN 'family_abuse' THEN 'Sustained Family login abuse'
      WHEN 'limiter_unavailable' THEN 'Login rate limiter unavailable'
      WHEN 'limiter_state_lost' THEN 'Login rate limiter state was lost'
      WHEN 'publication_ambiguous' THEN 'Parish data publication is uncertain'
      WHEN 'production_cleanup_failed' THEN 'Campaign preparation cleanup failed'
      WHEN 'backup_rpo_breach' THEN 'Required backup is overdue'
      WHEN 'backup_offsite_failed' THEN 'Off-site backup copy failed'
      WHEN 'backup_key_changed' THEN 'Backup encryption key changed'
      WHEN 'source_retention_failing' THEN 'Parish data cleanup keeps failing'
      WHEN 'purge_inconsistency' THEN 'Campaign purge is inconsistent'
      WHEN 'purge_cleanup_failed' THEN 'Campaign purge cleanup failed'
      WHEN 'automation_approved' THEN 'An automation session was approved'
      WHEN 'automation_irreversible' THEN 'An automation session took an irreversible action'
      WHEN 'automation_policy_change' THEN 'An automation session changed user access, integration keys or notification settings'
      WHEN 'automation_refused' THEN 'An automation session was refused'
      WHEN 'web_unhealthy' THEN 'Web server is not responding'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Operational kind is invalid' USING ERRCODE='23514'; END IF;
    status:=CASE n.phase WHEN 'resolved' THEN 'RESOLVED' ELSE n.level END;
    instruction:=CASE WHEN n.phase='resolved' AND kind='backup_key_changed'
      THEN 'The backup encryption key change is no longer recent. If you have not already, ask the server operator to confirm that each kept copy of the private key opens a new backup, as the backup runbook describes.'
      WHEN n.phase='resolved' AND kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'No further automation events of this kind in the last hour. Review the automation notices on the Admin dashboard if you have not already.'
      WHEN n.phase='resolved'
      THEN 'This condition has recovered. Review the operational log if follow-up is needed.'
      WHEN kind='backup_key_changed' THEN 'A backup in the last two days was sealed to a different encryption key than the backup before it. Unless the server operator installed a new key on purpose, new backups may not open with the kept private key. Ask the operator to open the newest backup with each kept copy of the private key, as the backup runbook describes.'
      WHEN kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'Review the automation notices on the Admin dashboard. They name the automation session and what it did.'
      WHEN kind='source_retention_failing' THEN 'Removing old ParishSoft copies was skipped by the last three refreshes, or kept stopping at its time or lock limits with old copies left, so the database keeps growing. Refreshes still work. Ask the server operator to check the System log and the worker log for the cause.'
      WHEN kind='web_unhealthy' THEN 'The web server has not answered three health checks in a row, a minute apart, so the Admin and Family portals may not be loading. Docker restarts web only if it stops running. Ask the server operator to restart web from the Compose directory of the deployment (docker compose ... restart web) and to check its log.'
      ELSE 'Administrator attention is required. Review the operational log for details.' END;
    labels:=ARRAY['Status','Notification','Deployment mode','First observed',
      'Latest observation','Occurrences','Incident reference'];
    vals:=ARRAY[status,n.phase,initcap(deployment_mode),
      to_char(n.first_seen AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.observed_at AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.occurrences,'FM9,999,999,999,999,999,999'),n.incident_id::text];
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||vals[i]||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] '||status||': '||title,
      'html',html||'</dl>','text',body);
END $$;

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_ops_content_v1'
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%kept stopping at its time or lock limits%'
          AND p.prosrc NOT LIKE '%three refreshes, so the database%')<>1 THEN
        RAISE EXCEPTION 'stewardship_ops_content_v1 was not installed as declared';
    END IF;
END
$check$;
