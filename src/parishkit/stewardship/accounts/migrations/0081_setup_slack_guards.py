"""Original-Admin notification intent and isolated Slack submission ownership."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = r"""
CREATE FUNCTION public.stewardship_setup_slack_live_v1(
    attempt_id uuid, attempt_version bigint, credential_id uuid,
    credential_version bigint, fingerprint text)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_task_run original ON original.id=attempt.source_task_id
            AND original.created_at>clock_timestamp()-interval '2 hours'
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='slack' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL AND slack.values->'enabled'='true'::jsonb
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND candidate.settings->>'channel_id'=slack.values->>'channel_id'
    );
$$;

CREATE FUNCTION public.stewardship_setup_slack_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup notification history is retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup notification requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR (OLD.state='queued' AND NEW.state='submitting') THEN
        IF NOT public.stewardship_setup_slack_live_v1(
            NEW.attempt_id,NEW.attempt_version,NEW.credential_id,
            NEW.credential_version,NEW.fingerprint) THEN
            RAISE EXCEPTION 'Setup notification needs its exact live draft'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web' OR NEW.state<>'queued'
           OR NEW.version<>1 OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_attempt attempt
                WHERE attempt.id=NEW.attempt_id AND attempt.owner_id=NEW.actor_id) THEN
            RAISE EXCEPTION 'Setup notification requires original Admin intent'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler',
                                'pk_stewardship_credential_slack')
           OR NEW.actor_id IS NOT NULL
           OR public.stewardship_setup_slack_live_v1(
                NEW.attempt_id,NEW.attempt_version,NEW.credential_id,
                NEW.credential_version,NEW.fingerprint) THEN
            RAISE EXCEPTION 'Only stale unsent notifications can be cancelled'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state='queued' AND NEW.state='submitting' THEN
        IF current_user<>'pk_stewardship_credential_slack'
           OR NEW.worker_id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id THEN
            RAISE EXCEPTION 'Only the isolated Slack owner may submit'
                USING ERRCODE='23514';
        END IF;
        NEW.submitted_at=stamp;
        NEW.deadline_at=stamp+interval '30 seconds';
        RETURN NEW;
    END IF;
    IF OLD.state='submitting'
       AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
        IF current_user='pk_stewardship_credential_slack'
           AND NEW.actor_id=OLD.worker_id THEN
            IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                RAISE EXCEPTION 'Late notification results remain unknown'
                    USING ERRCODE='23514';
            END IF;
        ELSIF current_user IN (
            'pk_stewardship_scheduler','pk_stewardship_credential_slack')
            AND NEW.actor_id IS NULL AND stamp>=OLD.deadline_at
            AND NEW.state='delivery_unknown' THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'Notification outcome needs original ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Notification cannot be retried or rewritten' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_setup_slack_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_setup_slack_delivery
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_slack_guard_v1();

CREATE FUNCTION public.stewardship_setup_slack_audit_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        'setup_slack_'||NEW.state,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome', CASE
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_slack_audit
AFTER INSERT OR UPDATE ON public.stewardship_setup_slack_delivery
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_slack_audit_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_slack_delivery IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_slack_delivery) THEN
        RAISE EXCEPTION 'Setup notification history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_slack_audit ON public.stewardship_setup_slack_delivery;
DROP FUNCTION public.stewardship_setup_slack_audit_v1();
DROP TRIGGER stewardship_setup_slack_admission
    ON public.stewardship_setup_slack_delivery;
DROP FUNCTION public.stewardship_setup_slack_guard_v1();
DROP FUNCTION public.stewardship_setup_slack_live_v1(uuid,bigint,uuid,bigint,text);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0080_setup_slack_delivery")]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_slack_delivery",
            frozen_fields=(
                "attempt_id",
                "attempt_version",
                "request_key",
                "candidate_digest",
                "credential_id",
                "credential_version",
                "fingerprint",
            ),
            write_once_fields=(
                "worker_id",
                "submitted_at",
                "deadline_at",
                "finished_at",
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
