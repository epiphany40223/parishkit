-- Frozen forward migration file 0097 (the repository-wide file sequence):
-- resuming live delivery after an ordinary pause no longer needs a sender
-- check (#1288). This file is installed by the Django migration that names
-- it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest, and
-- tests/stewardship/database/test_resume_sender_check_postgresql.py checks
-- that its view text still equals the fresh-install baseline's
-- (schema/delivery_control.sql). A fresh install runs the baseline, the
-- earlier files and then this; the baseline already carries this view, so
-- the install ends in the same catalog as an upgraded database.
--
-- stewardship_delivery_control_health is the sender check that Resume, a
-- closed campaign's Release and their SQL guard
-- (stewardship_delivery_control_guard_v1, which reads it at run time and is
-- not replaced) require. It used to require, for every pause, a test email
-- accepted after the pause and less than five minutes before. Now a check
-- is required only after real sender or delivery trouble (a test since the
-- pause still being sent, failed or uncertain; a live outcome of the
-- current sender with unavailable or systemic provider health, whatever its
-- reason, unless a healthy one submitted after it was recorded came before
-- the pause, as jobs/mail_health.py judges recovery; or a sender other than
-- the one behind the last live outcome before the pause), and its proof
-- lasts 30 minutes. A Gmail sending-limit refusal carries healthy health
-- and is retried after its own hold, so it is not trouble. The document gains
-- ``required`` and ``trouble``. The column list is unchanged, so CREATE OR
-- REPLACE VIEW keeps the owner and the grants, which the DO block compares
-- with the ones recorded before the replacement. A resume or release
-- previewed before this upgrade binds the older document and must be
-- previewed again. No row changes. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The view's grants before the replacement, compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0097_acl', (
    SELECT coalesce(c.relacl::text,'default')
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='stewardship_delivery_control_health'), true);

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
    ), facts AS (
        SELECT s.*,
            -- When the current sender's last healthy live outcome before the
            -- pause was submitted. Provider outcomes are judged by their
            -- recorded health, never their reason, exactly as
            -- jobs/mail_health.py observed_results and health_filter (and
            -- the outbox_provider_healthy index) select them.
            (SELECT e.submitted_at FROM public.stewardship_outbox_event e
                WHERE e.provider_identity=s.identity AND e.created_at<s.paused_at
                    AND e.evidence_note LIKE '{"health":"healthy",%'
                    AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
                    AND e.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
                        'smtp_permanent','smtp_delivery_unknown','smtp_systemic')
                ORDER BY e.created_at DESC,e.id DESC LIMIT 1) AS healthy_submitted_at,
            -- Which sender gave the last live outcome before the pause.
            (SELECT e.provider_identity FROM public.stewardship_outbox_event e
                WHERE e.previous_state='submitting' AND e.created_at<s.paused_at
                    AND e.provider_identity<>''
                ORDER BY e.created_at DESC,e.id DESC LIMIT 1) AS last_identity,
            (SELECT max(t.created_at) FROM public.stewardship_campaign_mail_test t
                WHERE t.campaign_id=s.campaign_id AND t.created_at>=s.paused_at
                    AND t.state IN ('queued','submitting')) AS pending_test_at,
            (SELECT max(t.created_at) FROM public.stewardship_campaign_mail_test t
                WHERE t.campaign_id=s.campaign_id AND t.created_at>=s.paused_at
                    AND t.state IN ('not_sent','delivery_unknown')) AS failed_test_at
        FROM scope s WHERE s.delivery_paused
    ), trouble AS (
        SELECT f.*,
            -- The current sender's latest live outcome with unavailable or
            -- systemic health, whatever its reason (a delivery-unknown or
            -- temporary refusal can carry it), unless that healthy outcome
            -- was submitted after it was recorded. As in mail_health.py's
            -- recovery, a healthy send already in flight when the failure
            -- came (submitted before it) does not clear it.
            (SELECT max(e.created_at) FROM public.stewardship_outbox_event e
                WHERE e.provider_identity=f.identity
                    AND e.created_at>=coalesce(f.healthy_submitted_at,'-infinity')
                    AND (e.evidence_note LIKE '{"health":"unavailable",%'
                        OR e.evidence_note LIKE '{"health":"systemic",%')
                    AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
                    AND e.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
                        'smtp_permanent','smtp_delivery_unknown','smtp_systemic')) AS failure_at,
            -- The settings or credential changed since that last outcome.
            f.identity='' OR coalesce(f.last_identity<>f.identity,false) AS sender_changed
        FROM facts f
    ), reason AS (
        SELECT t.*,CASE
            WHEN t.pending_test_at IS NOT NULL THEN 'test_in_progress'
            WHEN t.failure_at IS NOT NULL THEN 'provider_failure'
            WHEN t.failed_test_at IS NOT NULL THEN 'test_failed'
            WHEN t.sender_changed THEN 'sender_changed' END AS code
        FROM trouble t
    ), proof AS (
        SELECT t.id,t.submitted_at,t.finished_at FROM public.stewardship_campaign_mail_test t,reason r
        WHERE t.campaign_id=r.campaign_id
            AND t.configuration_id=r.configuration_id AND t.fingerprint=r.credential_fingerprint
            AND t.state='accepted' AND t.submitted_at>=r.paused_at
            AND t.submitted_at>clock_timestamp()-interval '30 minutes'
            AND t.submitted_at>coalesce(greatest(r.pending_test_at,r.failure_at,r.failed_test_at),'-infinity')
        ORDER BY t.finished_at DESC,t.id DESC LIMIT 1
    ) SELECT s.campaign_id,jsonb_build_object(
        'ready',r.campaign_id IS NOT NULL AND (r.code IS NULL OR p.id IS NOT NULL),
        'required',r.code IS NOT NULL,'trouble',r.code,
        'proof',p.id,'checked_at',p.finished_at,
        'expires_at',p.submitted_at+interval '30 minutes'
    ) AS health FROM scope s LEFT JOIN reason r ON true LEFT JOIN proof p ON r.code IS NOT NULL;

-- Refuse to commit unless the new view (its trouble codes and 30-minute
-- proof, deparsed as '00:30:00'::interval, never the old five, and the
-- healthy_submitted_at column) is installed and
-- kept its rights.
DO $check$
BEGIN
    IF position('test_in_progress' IN pg_get_viewdef('public.stewardship_delivery_control_health'::regclass))=0
        OR position('''00:30:00''::interval' IN pg_get_viewdef('public.stewardship_delivery_control_health'::regclass))=0
        OR position('healthy_submitted_at' IN pg_get_viewdef('public.stewardship_delivery_control_health'::regclass))=0
        OR position('''00:05:00''::interval' IN pg_get_viewdef('public.stewardship_delivery_control_health'::regclass))<>0 THEN
        RAISE EXCEPTION 'Migration 0097: stewardship_delivery_control_health was not replaced with the #1288 view';
    END IF;
    IF current_setting('stewardship.migration_0097_acl', true) IS DISTINCT FROM (
        SELECT coalesce(c.relacl::text,'default')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='stewardship_delivery_control_health') THEN
        RAISE EXCEPTION 'Migration 0097: stewardship_delivery_control_health lost or changed its grants';
    END IF;
END
$check$;
