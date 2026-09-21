-- Independent fixed-content admission for security alert outbox writers. The
-- private helper also recompiles these facts from the immutable event; it
-- never trusts stored arbitrary prose. Contract tests compare every kind
-- against jobs/security_content.py byte for byte.
CREATE FUNCTION stewardship_security_role_words_v1(roles jsonb)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT COALESCE(string_agg(label, ', ' ORDER BY ordinal), 'none') FROM (
      SELECT 1 AS ordinal, 'Administrator' AS label WHERE roles ? 'administrator'
      UNION ALL SELECT 2, 'Staff' WHERE roles ? 'staff'
      UNION ALL SELECT 3, 'Ministry leader' WHERE roles ? 'ministry_leader') words
$$;

CREATE FUNCTION stewardship_security_content_v1(event uuid, deployment_mode text)
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
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Security alert kind is invalid' USING ERRCODE='23514'; END IF;
    SELECT email INTO actor FROM stewardship_portal_user WHERE id=e.actor_id;
    instruction:='This change to who may sign in took effect on activation. Acknowledge it '
      'on the Admin dashboard; it stays there until an Administrator who existed '
      'before the change does.';
    labels:=ARRAY['Target','Roles before','Roles after','By','When','Deployment mode',
      'Event reference'];
    vals:=ARRAY[e.target,stewardship_security_role_words_v1(e.before_roles),
      stewardship_security_role_words_v1(e.after_roles),COALESCE(actor,'Operator recovery'),
      to_char(e.created_at AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      initcap(deployment_mode),e.id::text];
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||stewardship_security_escape_v1(vals[i])||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] SECURITY: '||title,
      'html',html||'</dl>','text',body);
END $$;

-- html.escape(quote=True): & < > " ' in that order, as Python does it.
CREATE FUNCTION stewardship_security_escape_v1(value text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT replace(replace(replace(replace(replace(value,'&','&amp;'),'<','&lt;'),
      '>','&gt;'),'"','&quot;'),'''','&#x27;')
$$;

CREATE FUNCTION stewardship_security_render_matches_v1(proposed jsonb, event uuid,
    configuration uuid, address text, deployment_mode text)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; content jsonb;
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
      WHERE configuration_id=configuration AND kind='email';
    IF email IS NULL OR address IS NULL THEN RETURN false; END IF;
    content:=stewardship_security_content_v1(event,deployment_mode);
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
