"""Pin exact successful delivery and source evidence without allowing activation."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION public.stewardship_setup_readiness_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
    intent public.stewardship_setup_config_intent%ROWTYPE;
    request public.stewardship_config_request%ROWTYPE;
    slack_enabled boolean;
BEGIN
    IF current_user<>'pk_stewardship_web' OR NOT EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup readiness needs its ordered original web owner'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO intent FROM public.stewardship_setup_config_intent
        WHERE id=NEW.intent_id;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=intent.attempt_id;
    SELECT * INTO request FROM public.stewardship_config_request
        WHERE id=intent.request_id;
    IF attempt.state IS DISTINCT FROM 'frozen'
        OR attempt.version IS DISTINCT FROM intent.attempt_version
        OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.request_schema IS DISTINCT FROM 'initial-setup-patch-v7'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration runtime
            WHERE runtime.active_configuration_id=attempt.base_id
                AND runtime.mode='testing' AND NOT runtime.restore_review_required
                AND runtime.current_campaign_id IS NULL)
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=request.id ORDER BY sequence DESC LIMIT 1)
                IS DISTINCT FROM 'staged'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session login
            JOIN public.stewardship_portal_user owner
                ON owner.id=login.principal_id AND NOT owner.disabled
            JOIN public.stewardship_address_rule rule
                ON rule.configuration_id=attempt.base_id AND rule.email=owner.email
                AND rule.roles @> '["administrator"]'::jsonb
            WHERE login.id=attempt.session_id AND login.principal_id=attempt.owner_id
                AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                AND login.last_activity_at>clock_timestamp()-interval '30 minutes')
        THEN
        RAISE EXCEPTION 'Setup readiness requires a live frozen original request'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_source_result result
        JOIN public.stewardship_setup_source_exchange exchange
            ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
            AND exchange.scrubbed_at IS NULL
        JOIN public.stewardship_task_run task ON task.id=exchange.task_id
            AND task.root_id=attempt.source_task_id AND task.state='succeeded'
            AND task.fence=exchange.task_fence
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=exchange.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=exchange.credential_version
            AND credential.fingerprint=exchange.fingerprint
        JOIN public.stewardship_source_snapshot snapshot
            ON snapshot.id=result.snapshot_id
            AND snapshot.state='ready' AND snapshot.task_id=task.id
            AND snapshot.source_fence=exchange.source_fence
        WHERE result.id=NEW.source_result_id) THEN
        RAISE EXCEPTION 'Setup readiness source is not the exact reviewed catalog'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        WHERE delivery.id=NEW.mail_delivery_id AND delivery.attempt_id=attempt.id
            AND delivery.attempt_version=intent.attempt_version-1
            AND delivery.candidate_digest=request.candidate_digest
            AND delivery.state='accepted'
            AND NEW.testing_recipient=testing.values->>'testing_recipient'
            AND NEW.testing_recipient=credential.settings->>'recipient') THEN
        RAISE EXCEPTION 'Setup readiness requires exact accepted Testing mail'
            USING ERRCODE='23514';
    END IF;
    SELECT (values->>'enabled')::boolean INTO slack_enabled
        FROM public.stewardship_setup_draft_section
        WHERE attempt_id=attempt.id AND step='slack' AND scrubbed_at IS NULL;
    IF slack_enabled IS NULL OR (slack_enabled AND NEW.slack_delivery_id IS NULL)
       OR (NOT slack_enabled AND NEW.slack_delivery_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Setup Slack readiness differs from reviewed settings'
            USING ERRCODE='23514';
    END IF;
    IF slack_enabled AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        WHERE delivery.id=NEW.slack_delivery_id AND delivery.attempt_id=attempt.id
            AND delivery.attempt_version=intent.attempt_version-1
            AND delivery.candidate_digest=request.candidate_digest
            AND delivery.state='accepted'
            AND credential.settings->>'channel_id'=slack.values->>'channel_id') THEN
        RAISE EXCEPTION 'Setup readiness requires the exact accepted Slack test'
            USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting'))
       OR EXISTS (SELECT 1 FROM public.stewardship_setup_slack_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting')) THEN
        RAISE EXCEPTION 'Setup tests must finish before confirmation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_readiness_insert
BEFORE INSERT ON public.stewardship_setup_readiness_binding
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_readiness_guard_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_readiness_binding IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_readiness_binding) THEN
        RAISE EXCEPTION 'Setup readiness history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_readiness_insert
    ON public.stewardship_setup_readiness_binding;
DROP FUNCTION public.stewardship_setup_readiness_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0082_setup_readiness_binding")]
    operations = [
        immutable_guard_v1("stewardship_setup_readiness_binding"),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
