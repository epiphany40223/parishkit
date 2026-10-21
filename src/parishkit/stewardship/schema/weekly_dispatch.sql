-- Weekly messages share the isolated MAIL claim and SMTP evidence protocol, but
-- never inherit Family credential reads, refusal propagation or group writes.
-- paused_ok admits a held resend of an unknown delivery while paused; the
-- pause hold, not this gate, then keeps it unsent. Workers call the
-- one-argument form, so every provider attempt still honours the pause.
CREATE FUNCTION stewardship_weekly_dispatch_live_v1(message uuid,paused_ok boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_weekly_digest_snapshot s ON s.id=recipient.snapshot_id
        JOIN stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN stewardship_campaign c ON c.id=p.campaign_id
        JOIN stewardship_system_configuration runtime ON runtime.current_campaign_id=c.id
        JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            AND a.email=recipient.address AND a.roles @> '["administrator"]'::jsonb
        WHERE m.id=$1 AND m.purpose='weekly_digest' AND m.credential_namespace='none' AND m.family_id IS NULL
          AND m.campaign_id=p.campaign_id AND m.mode=p.mode AND p.phase='complete'
          AND o.state IN ('pending','running') AND o.due_at<=stewardship_campaign_now_v1()
          AND (paused_ok OR NOT (m.mode='production' AND c.delivery_paused) OR stewardship_delivery_message_released_v1(m.id))
          AND stewardship_weekly_digest_scope_v1(p.campaign_id,p.revision_id,p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id)
          AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
$$;

-- Web requests a retry but cannot copy private report content into an arbitrary
-- render. MAIL must replace this fixed, non-sendable seed under its live claim.
CREATE FUNCTION stewardship_weekly_digest_seed_v1(message uuid,configuration uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; intended jsonb; routed jsonb; mode text; testing text;
    content jsonb; heading text:='Weekly report awaiting preparation';
    body text:='PARISHKIT_PENDING_WEEKLY_DIGEST: Weekly report awaiting preparation.';
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT testing_recipient INTO testing FROM stewardship_system_configuration;
    SELECT m.mode,jsonb_build_array(recipient.address) INTO mode,intended
        FROM stewardship_outbox_message m
        JOIN stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=m.id
        WHERE m.id=message AND m.purpose='weekly_digest';
    IF email IS NULL OR mode IS NULL OR jsonb_array_length(intended)<>1 THEN
        RAISE EXCEPTION 'Weekly retry requires exact configured routing' USING ERRCODE='23514'; END IF;
    routed:=CASE mode WHEN 'testing' THEN jsonb_build_array(testing) ELSE intended END;
    IF mode='testing' THEN
        heading:='[TEST] '||heading;
        body:='TEST — pending report is routed only to the Testing recipient. '||body;
    END IF;
    content:=jsonb_build_object('sender',email->>'sender','reply_to',email->>'reply_to',
        'intended_recipients',intended,'routed_recipients',routed,'subject',heading,
        'html',CASE mode WHEN 'testing' THEN '<h2>TEST</h2>' ELSE '' END||'<p>'||body||'</p>',
        'text',body);
    RETURN content||jsonb_build_object('configuration_id',configuration,'template_id',NULL,
        'payload_digest',encode(sha256(convert_to(content::text,'UTF8')),'hex'));
END $$;
REVOKE ALL ON FUNCTION stewardship_weekly_digest_seed_v1(uuid,uuid) FROM PUBLIC;
