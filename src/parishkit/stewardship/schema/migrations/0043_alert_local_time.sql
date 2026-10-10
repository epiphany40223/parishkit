-- Frozen forward migration file 0043 (the repository-wide file sequence):
-- operational and security alert emails show times in the parish's local
-- time zone with the zone named, never UTC (#967, Administrator decision
-- B3, 2026-10-10). It is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that each replaced function's copy here still equals the
-- fresh-install baseline's (schema/operational_render.sql and
-- schema/security_render.sql).
--
-- The alert content functions are the SQL twins of
-- jobs/operational_content.py and jobs/security_content.py: the outbox
-- admission recompiles each alert and compares it byte for byte. They
-- showed "MM/DD/YYYY HH24:MI:SS UTC". Now the times use the parish profile
-- of the configuration routing the alert (every applied configuration has
-- exactly one): its time zone and its date format, as web/dates.py
-- format_instant renders them, naming the zone by its abbreviation.
-- The content functions need that configuration, so this file:
--
--   adds stewardship_alert_time_v1          one instant in the parish zone
--                                            and date format
--   adds stewardship_ops_content_v2         stewardship_ops_content_v1 with
--                                            a configuration argument and
--                                            the two times changed
--   adds stewardship_security_content_v2    likewise for the security "When"
--   replaces stewardship_ops_render_matches_v1 and
--            stewardship_security_render_matches_v1
--                                            to call the v2 content
--   drops stewardship_ops_content_v1 and stewardship_security_content_v1,
--            which nothing else calls
--
-- None is SECURITY DEFINER (the baseline sets none on these), all are
-- STABLE invoker functions with the fixed search_path; the time helper also
-- pins TimeZone so the zone it sets lasts only for the call. The callers'
-- logins read stewardship_parish's timezone and date_format through the
-- runtime grants (accounts/setup_mail_grants.py for mail dispatch).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_alert_time_v1(instant timestamp with time zone,
    configuration uuid)
RETURNS text LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp SET "TimeZone" TO 'UTC' AS $$
DECLARE zone text; style text; day text; clock text;
BEGIN
    SELECT p.timezone, COALESCE(p.date_format,'us_long') INTO STRICT zone, style
      FROM stewardship_parish p WHERE p.configuration_id=configuration;
    -- Local to this call: the function's own SET clause restores the session
    -- zone on exit. to_char then reads the wall clock and the abbreviation
    -- in force at the instant (EDT or EST across a change).
    PERFORM set_config('TimeZone',stewardship_timezone_name_v1(zone),true);
    day:=CASE style
      WHEN 'us_long' THEN to_char(instant,'FMMonth FMDD, YYYY')
      WHEN 'us_medium' THEN to_char(instant,'Mon FMDD, YYYY')
      WHEN 'us_numeric' THEN to_char(instant,'MM/DD/YYYY')
      WHEN 'eu_long' THEN to_char(instant,'FMDD FMMonth YYYY')
      WHEN 'eu_medium' THEN to_char(instant,'FMDD Mon YYYY')
      WHEN 'eu_numeric' THEN to_char(instant,'DD/MM/YYYY')
      WHEN 'eu_dot' THEN to_char(instant,'DD.MM.YYYY')
      WHEN 'iso' THEN to_char(instant,'YYYY-MM-DD')
    END;
    IF day IS NULL THEN RAISE EXCEPTION 'Alert date format is invalid' USING ERRCODE='23514'; END IF;
    clock:=CASE WHEN left(style,3)='us_' THEN to_char(instant,'FMHH12:MI AM')
      ELSE to_char(instant,'HH24:MI') END;
    RETURN day||CASE WHEN style IN ('us_long','us_medium') THEN ' at '
        WHEN style IN ('eu_long','eu_medium') THEN ', ' ELSE ' ' END
      ||clock||' '||to_char(instant,'TZ');
END $$;

CREATE FUNCTION public.stewardship_ops_content_v2(notice uuid, deployment_mode text,
    configuration uuid)
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
      stewardship_alert_time_v1(n.first_seen,configuration),
      stewardship_alert_time_v1(n.observed_at,configuration),
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

CREATE FUNCTION public.stewardship_security_content_v2(event uuid, deployment_mode text,
    configuration uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE e stewardship_policy_security_event%ROWTYPE; title text; actor text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Security alert mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT e FROM stewardship_policy_security_event WHERE id=event;
    title:=CASE e.kind
      WHEN 'administrator_granted' THEN 'Administrator added to an exact address'
      WHEN 'domain_created' THEN 'Hosted-domain rule created'
      WHEN 'domain_staff_granted' THEN 'Staff added to a hosted-domain rule'
      WHEN 'backup_key_replaced' THEN 'Backup encryption key replaced'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Security alert kind is invalid' USING ERRCODE='23514'; END IF;
    SELECT email INTO actor FROM stewardship_portal_user WHERE id=e.actor_id;
    IF e.kind='backup_key_replaced' THEN
      instruction:='An Administrator replaced the public key that backups are encrypted '
        'with. From the next backup on, only the matching private key can open them. '
        'If the parish key holder did not plan this, replace the key again with the '
        'parish key and ask the server operator to check the newest backup. If you '
        'can still sign in as an Administrator, acknowledge it on the Admin dashboard; '
        'it stays there until an Administrator other than the one who made the change '
        'does. If you no longer can, contact the parish or its other Administrators.';
      labels:=ARRAY['Key before','Key after','By','When','Deployment mode',
        'Event reference'];
      vals:=ARRAY[COALESCE(e.before_roles->>0,'not known'),e.after_roles->>0,
        COALESCE(actor,'Operator recovery'),
        stewardship_alert_time_v1(e.created_at,configuration),
        initcap(deployment_mode),e.id::text];
    ELSE
    instruction:='This change to who may sign in took effect on activation. Acknowledge it '
      'on the Admin dashboard; it stays there until an Administrator who existed '
      'before the change does.';
    labels:=ARRAY['Target','Roles before','Roles after','By','When','Deployment mode',
      'Event reference'];
    vals:=ARRAY[e.target,stewardship_security_role_words_v1(e.before_roles),
      stewardship_security_role_words_v1(e.after_roles),COALESCE(actor,'Operator recovery'),
      stewardship_alert_time_v1(e.created_at,configuration),
      initcap(deployment_mode),e.id::text];
    END IF;
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||stewardship_security_escape_v1(vals[i])||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] SECURITY: '||title,
      'html',html||'</dl>','text',body);
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ops_render_matches_v1(proposed jsonb, notice uuid,
    configuration uuid, address text, deployment_mode text)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; content jsonb;
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
      WHERE configuration_id=configuration AND kind='email';
    IF email IS NULL OR address IS NULL THEN RETURN false; END IF;
    content:=stewardship_ops_content_v2(notice,deployment_mode,configuration);
    RETURN (proposed->>'configuration_id')::uuid=configuration
      AND proposed->>'template_id' IS NULL
      AND proposed->>'sender'=email->>'sender'
      AND proposed->>'reply_to'=email->>'reply_to'
      AND proposed->'intended_recipients'=jsonb_build_array(address)
      AND proposed->'routed_recipients'=jsonb_build_array(address)
      AND proposed->>'subject'=content->>'subject'
      AND proposed->>'html'=content->>'html'
      AND proposed->>'text'=content->>'text';
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_security_render_matches_v1(proposed jsonb, event uuid,
    configuration uuid, address text, deployment_mode text)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; content jsonb;
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
      WHERE configuration_id=configuration AND kind='email';
    IF email IS NULL OR address IS NULL THEN RETURN false; END IF;
    content:=stewardship_security_content_v2(event,deployment_mode,configuration);
    RETURN (proposed->>'configuration_id')::uuid=configuration
      AND proposed->>'template_id' IS NULL
      AND proposed->>'sender'=email->>'sender'
      AND proposed->>'reply_to'=email->>'reply_to'
      AND proposed->'intended_recipients'=jsonb_build_array(address)
      AND proposed->'routed_recipients'=jsonb_build_array(address)
      AND proposed->>'subject'=content->>'subject'
      AND proposed->>'html'=content->>'html'
      AND proposed->>'text'=content->>'text';
END $$;

DROP FUNCTION public.stewardship_ops_content_v1(uuid, text);
DROP FUNCTION public.stewardship_security_content_v1(uuid, text);

DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_ops_content_v1(uuid,text)') IS NOT NULL
       OR to_regprocedure('public.stewardship_security_content_v1(uuid,text)') IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0043: a UTC alert content function is still installed';
    END IF;
    -- Every added or replaced function: invoker's rights (prosecdef false),
    -- STABLE, the fixed search_path, and the new local-time rendering.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND p.provolatile='s'
          AND p.proconfig[1]='search_path=pg_catalog, public, pg_temp'
          AND ((p.proname='stewardship_alert_time_v1'
                AND pg_get_function_identity_arguments(p.oid)
                  ='instant timestamp with time zone, configuration uuid'
                AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp','TimeZone=UTC']
                AND p.prosrc LIKE '%to_char(instant,''TZ'')%')
            OR (p.proname='stewardship_ops_content_v2'
                AND pg_get_function_identity_arguments(p.oid)
                  ='notice uuid, deployment_mode text, configuration uuid'
                AND cardinality(p.proconfig)=1
                AND p.prosrc LIKE '%stewardship_alert_time_v1(n.first_seen,configuration)%'
                AND p.prosrc LIKE '%stewardship_alert_time_v1(n.observed_at,configuration)%'
                AND p.prosrc NOT LIKE '%"UTC"%')
            OR (p.proname='stewardship_security_content_v2'
                AND pg_get_function_identity_arguments(p.oid)
                  ='event uuid, deployment_mode text, configuration uuid'
                AND cardinality(p.proconfig)=1
                AND regexp_count(p.prosrc,'stewardship_alert_time_v1\(e\.created_at,configuration\)')=2
                AND p.prosrc NOT LIKE '%"UTC"%')
            OR (p.proname='stewardship_ops_render_matches_v1'
                AND cardinality(p.proconfig)=1
                AND p.prosrc LIKE '%stewardship_ops_content_v2(notice,deployment_mode,configuration)%')
            OR (p.proname='stewardship_security_render_matches_v1'
                AND cardinality(p.proconfig)=1
                AND p.prosrc LIKE '%stewardship_security_content_v2(event,deployment_mode,configuration)%'))
       )<>5 THEN
        RAISE EXCEPTION 'Migration 0043: an alert function is not installed as declared';
    END IF;
END
$check$;
