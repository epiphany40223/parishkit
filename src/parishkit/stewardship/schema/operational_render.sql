-- Independent fixed-content admission for outbox SQL writers. The private
-- helper also recompiles these facts; it never trusts stored arbitrary prose.
-- Contract tests compare every closed kind/phase against operational_content.py.
CREATE FUNCTION stewardship_ops_content_v1(notice uuid, deployment_mode text)
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
      WHEN 'purge_inconsistency' THEN 'Campaign purge is inconsistent'
      WHEN 'purge_cleanup_failed' THEN 'Campaign purge cleanup failed'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Operational kind is invalid' USING ERRCODE='23514'; END IF;
    status:=CASE n.phase WHEN 'resolved' THEN 'RESOLVED' ELSE n.level END;
    instruction:=CASE n.phase WHEN 'resolved'
      THEN 'This condition has recovered. Review the operational log if follow-up is needed.'
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

CREATE FUNCTION stewardship_ops_render_matches_v1(proposed jsonb, notice uuid,
    configuration uuid, address text, deployment_mode text)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; content jsonb;
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
      WHERE configuration_id=configuration AND kind='email';
    IF email IS NULL OR address IS NULL THEN RETURN false; END IF;
    content:=stewardship_ops_content_v1(notice,deployment_mode);
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
