-- stewardship_delivery_control_health as the baseline had it before #1288,
-- for test_resume_sender_check_postgresql's migration DO-block test.
CREATE OR REPLACE VIEW public.stewardship_delivery_control_health AS
    WITH scope AS (
        SELECT c.id AS campaign_id,r.active_configuration_id AS configuration_id,
            c.delivery_paused,w.credential_fingerprint,
            public.stewardship_mail_provider_identity_v1(r.active_configuration_id) AS identity,
            (SELECT max(created_at) FROM public.stewardship_campaign_control
                WHERE campaign_id=c.id AND action='pause') AS paused_at
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
        JOIN public.stewardship_applied_integration w
            ON w.configuration_id=r.active_configuration_id AND w.kind='google_workspace'
        WHERE r.mode='production'
    ), proof AS (
        SELECT t.id,t.submitted_at,t.finished_at FROM public.stewardship_campaign_mail_test t,scope s
        WHERE s.delivery_paused AND t.campaign_id=s.campaign_id
            AND t.configuration_id=s.configuration_id AND t.fingerprint=s.credential_fingerprint
            AND t.state='accepted' AND t.submitted_at>=s.paused_at
            AND t.submitted_at>clock_timestamp()-interval '5 minutes'
        ORDER BY t.finished_at DESC,t.id DESC LIMIT 1
    ) SELECT s.campaign_id,jsonb_build_object(
        'ready',p.id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_campaign_mail_test t
            WHERE t.campaign_id=s.campaign_id AND t.configuration_id=s.configuration_id
                AND t.created_at>=p.submitted_at
                AND t.state IN ('queued','submitting','not_sent','delivery_unknown'))
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_outbox_event e
                WHERE e.provider_identity=s.identity AND e.created_at>=p.submitted_at
                    AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
                    AND e.reason IN ('smtp_unavailable','smtp_systemic')
                    AND (e.evidence_note LIKE '{"health":"unavailable",%'
                        OR e.evidence_note LIKE '{"health":"systemic",%')),
        'proof',p.id,'checked_at',p.finished_at,
        'expires_at',p.submitted_at+interval '5 minutes'
    ) AS health FROM scope s LEFT JOIN proof p ON true;
