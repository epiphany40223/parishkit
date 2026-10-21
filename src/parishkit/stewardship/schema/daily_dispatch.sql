-- Daily messages share the isolated MAIL claim and SMTP evidence protocol, but
-- never inherit Family credential reads, refusal propagation or group writes.
-- paused_ok admits a held resend of an unknown delivery while paused; the
-- pause hold, not this gate, then keeps it unsent. Workers call the
-- one-argument form, so every provider attempt still honours the pause.
CREATE FUNCTION stewardship_daily_dispatch_live_v1(message uuid,paused_ok boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_outbox_message m
        JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
        JOIN stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN stewardship_campaign c ON c.id=p.campaign_id
        JOIN stewardship_system_configuration runtime ON runtime.current_campaign_id=c.id
        JOIN stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            AND a.email=recipient.address AND a.roles @> '["administrator"]'::jsonb
        WHERE m.id=$1 AND m.purpose='daily_digest' AND m.credential_namespace='none' AND m.family_id IS NULL
          AND m.campaign_id=p.campaign_id AND m.mode=p.mode AND p.phase='complete'
          AND o.state IN ('pending','running') AND o.due_at<=stewardship_campaign_now_v1()
          AND (paused_ok OR NOT (m.mode='production' AND c.delivery_paused) OR stewardship_delivery_message_released_v1(m.id))
          AND stewardship_daily_digest_scope_v1(p.campaign_id,p.revision_id,p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id)
          AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
$$;

-- Web requests a retry but cannot copy private report content into an arbitrary
-- render. MAIL must replace this fixed, non-sendable seed under its live claim.
CREATE FUNCTION stewardship_daily_digest_seed_v1(message uuid,configuration uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; intended jsonb; routed jsonb; mode text; testing text;
    content jsonb; heading text:='Daily report awaiting preparation';
    body text:='PARISHKIT_PENDING_DAILY_DIGEST: Daily report awaiting preparation.';
BEGIN
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT testing_recipient INTO testing FROM stewardship_system_configuration;
    SELECT m.mode,jsonb_build_array(recipient.address) INTO mode,intended
        FROM stewardship_outbox_message m
        JOIN stewardship_daily_digest_recipient recipient ON recipient.outbox_id=m.id
        WHERE m.id=message AND m.purpose='daily_digest';
    IF email IS NULL OR mode IS NULL OR jsonb_array_length(intended)<>1 THEN
        RAISE EXCEPTION 'Daily retry requires exact configured routing' USING ERRCODE='23514'; END IF;
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
REVOKE ALL ON FUNCTION stewardship_daily_digest_seed_v1(uuid,uuid) FROM PUBLIC;

-- Completion is derived from the entire immutable cohort, never one child's
-- outcome. An independently guarded revocation cancels a recipient obligation,
-- never records provider acceptance. Entirely revoked cohorts are explicit empty
-- fulfillment, not delivered fulfillment. It has no provider authority and may
-- truthfully settle observations
-- after a pause/restore gate, just as an in-flight Family attempt may settle.
CREATE VIEW stewardship_daily_digest_completion_ready AS
    SELECT p.id AS preparation_id,
        CASE WHEN EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient recipient
            LEFT JOIN stewardship_outbox_message m ON m.id=recipient.outbox_id
            WHERE recipient.ready_id=ready.id AND (m.state='delivered'
                OR (recipient.outbox_id IS NULL AND jsonb_array_length(recipient.covered_messages)>0)))
            THEN 'delivered' ELSE 'empty' END AS disposition
        FROM stewardship_daily_digest_preparation p
        JOIN stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
        JOIN stewardship_daily_digest_ready ready ON ready.snapshot_id=s.id
        WHERE p.phase='complete' AND jsonb_array_length(ready.recipients)>0
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(ready.recipients) AS desired(address)
            WHERE NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient recipient
                LEFT JOIN stewardship_outbox_message m ON m.id=recipient.outbox_id
                WHERE recipient.ready_id=ready.id AND recipient.address=desired.address
                  AND (m.state='delivered'
                      OR (m.state='cancelled' AND m.reason='recipient_revoked')
                      -- A failed report to a recipient who is not currently an
                      -- Administrator (the dispatchers' recipient_revoked test)
                      -- can never be retried, so it settles like that
                      -- cancellation, whatever ended it: a provider refusal or
                      -- an Admin's confirm_unsent. The roster is read live, so
                      -- a pending occurrence could reopen if the address became
                      -- an Administrator again before completion; a completed
                      -- occurrence never changes. No worker owns this
                      -- settlement: the metadata finalizer completes it.
                      OR (m.state='permanent_failure' AND NOT EXISTS(
                          SELECT 1 FROM stewardship_system_configuration runtime
                          JOIN stewardship_address_rule admin
                              ON admin.configuration_id=runtime.active_configuration_id
                          WHERE admin.roles @> '["administrator"]'::jsonb
                            AND admin.email=recipient.address))
                      OR (recipient.outbox_id IS NULL
                      AND jsonb_array_length(recipient.covered_messages)>0
                      AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(recipient.covered_messages) AS referenced(message_id)
                          WHERE NOT EXISTS(SELECT 1 FROM stewardship_outbox_message accepted
                              WHERE accepted.id=referenced.message_id::uuid AND accepted.state='delivered'))))));
REVOKE ALL ON stewardship_daily_digest_completion_ready FROM PUBLIC;
