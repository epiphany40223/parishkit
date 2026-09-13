"""Original-login test intake and irreversible, fenced provider outcomes."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = r"""
CREATE FUNCTION public.stewardship_setup_mail_live_v1(
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
            AND candidate.target='google_workspace' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section mail
            ON mail.attempt_id=attempt.id AND mail.step='mail'
            AND mail.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND candidate.settings->>'sender'=mail.values->>'sender'
            AND candidate.settings->>'reply_to'=mail.values->>'reply_to'
            AND candidate.settings->>'delegated_email'=mail.values->>'delegated_email'
            AND candidate.settings->>'recipient'=testing.values->>'testing_recipient'
    );
$$;

CREATE FUNCTION public.stewardship_setup_mail_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        owned boolean; live boolean; keys text[]; stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup delivery outcomes are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup delivery requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS DISTINCT FROM OLD.scrubbed_at THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler')
           OR OLD.scrubbed_at IS NOT NULL OR NEW.scrubbed_at IS NULL
           OR NEW.mail<>'{}'::jsonb OR NEW.actor_id IS NOT NULL
           OR attempt.state NOT IN ('expired','completed')
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
           OR NEW.deadline_at IS DISTINCT FROM OLD.deadline_at
           OR NEW.run_id IS DISTINCT FROM OLD.run_id
           OR NEW.task_fence IS DISTINCT FROM OLD.task_fence
           OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
           OR (OLD.state='queued' AND NEW.state<>'cancelled')
           OR (OLD.state<>'queued' AND NEW.state<>OLD.state) THEN
            RAISE EXCEPTION 'Setup delivery scrub requires terminal setup'
                USING ERRCODE='23514';
        END IF;
        IF OLD.state='queued' THEN NEW.finished_at=stamp;
        ELSIF NEW.finished_at IS DISTINCT FROM OLD.finished_at THEN
            RAISE EXCEPTION 'Scrubbing cannot rewrite a delivery result'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' AND NEW.mail IS DISTINCT FROM OLD.mail THEN
        RAISE EXCEPTION 'Setup delivery content is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR (OLD.state='queued' AND NEW.state='submitting') THEN
        live=public.stewardship_setup_mail_live_v1(NEW.attempt_id,NEW.attempt_version,
            NEW.credential_id,NEW.credential_version,NEW.fingerprint);
        IF NOT live OR NEW.scrubbed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Setup delivery requires exact current draft and credential'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web'
           OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='setup_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.initiated_by_id=attempt.owner_id) THEN
            RAISE EXCEPTION 'Setup mail requires its explicit original Admin request'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.mail) IS DISTINCT FROM 'object'
           OR octet_length(NEW.mail::text)>1048576 THEN
            RAISE EXCEPTION 'Invalid setup mail payload' USING ERRCODE='23514';
        END IF;
        SELECT array_agg(key ORDER BY key) INTO keys
            FROM jsonb_object_keys(NEW.mail) key;
        IF keys IS DISTINCT FROM ARRAY['delivery_id','html','recipient','reply_to',
                                      'sender','subject','text']
           OR EXISTS (SELECT 1 FROM jsonb_each(NEW.mail) pair
                WHERE jsonb_typeof(pair.value)<>'string')
           OR NEW.mail->>'delivery_id' IS DISTINCT FROM NEW.id::text
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential key
                WHERE key.id=NEW.credential_id
                    AND key.settings->>'recipient'=NEW.mail->>'recipient'
                    AND key.settings->>'sender'=NEW.mail->>'sender'
                    AND key.settings->>'reply_to'=NEW.mail->>'reply_to') THEN
            RAISE EXCEPTION 'Setup mail may address only its exact Testing recipient'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','submitting') THEN
        RAISE EXCEPTION 'Terminal setup delivery cannot be replayed or rewritten'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler',
                               'pk_stewardship_mail_dispatch')
           OR NEW.actor_id IS NOT NULL
           OR public.stewardship_setup_mail_live_v1(NEW.attempt_id,NEW.attempt_version,
                NEW.credential_id,NEW.credential_version,NEW.fingerprint) THEN
            RAISE EXCEPTION 'Only stale unsent setup work can be cancelled'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    SELECT EXISTS (SELECT 1 FROM public.stewardship_task_run task
        WHERE task.id=NEW.run_id AND task.root_id=NEW.task_id
            AND task.task_type='setup_mail_test' AND task.domain_request_id=NEW.id
            AND task.initiated_by_id=attempt.owner_id AND task.state='running'
            AND task.fence=NEW.task_fence AND task.worker_id=NEW.worker_id
            AND task.lease_expires_at>stamp) INTO owned;
    IF OLD.state='queued' AND NEW.state='submitting' THEN
        IF current_user<>'pk_stewardship_mail_dispatch' OR NOT owned
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.run_id
                    AND task.lease_expires_at>stamp+interval '30 seconds') THEN
            RAISE EXCEPTION 'Only the live mail worker may begin submission'
                USING ERRCODE='23514';
        END IF;
        NEW.submitted_at=stamp;
        NEW.deadline_at=stamp+interval '30 seconds';
        RETURN NEW;
    END IF;
    IF OLD.state='submitting'
       AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
        IF current_user='pk_stewardship_mail_dispatch' AND owned
           AND NEW.actor_id=NEW.worker_id THEN
            IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                RAISE EXCEPTION 'A late setup delivery result remains unknown'
                    USING ERRCODE='23514';
            END IF;
        ELSIF current_user IN
              ('pk_stewardship_mail_dispatch','pk_stewardship_scheduler')
              AND NOT owned AND stamp>=OLD.deadline_at
              AND NEW.state='delivery_unknown' AND NEW.actor_id IS NULL THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'Setup outcome needs live ownership or expired uncertainty'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid setup delivery transition' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_setup_mail_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_setup_mail_delivery
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_guard_v1();

CREATE FUNCTION public.stewardship_setup_scrub_mail_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_mail_delivery SET mail='{}'::jsonb,
            state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_scrub_mail
AFTER UPDATE ON public.stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_mail_v1();

CREATE FUNCTION public.stewardship_setup_mail_audit_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid(); event text;
BEGIN
    event='setup_mail_' || CASE WHEN NEW.scrubbed_at IS NOT NULL
        AND (TG_OP='INSERT' OR OLD.scrubbed_at IS NULL) THEN 'scrubbed'
        ELSE NEW.state END;
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,event,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome', CASE
            WHEN event='setup_mail_scrubbed' THEN 'changed'
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_mail_audit
AFTER INSERT OR UPDATE ON public.stewardship_setup_mail_delivery
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_audit_v1();

CREATE POLICY setup_secret_mail_metadata ON public.stewardship_setup_sealed_credential
FOR SELECT USING (current_user='pk_stewardship_mail_dispatch'
                  AND target='google_workspace');
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_mail_delivery IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery) THEN
        RAISE EXCEPTION 'Setup delivery history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_mail_audit ON public.stewardship_setup_mail_delivery;
DROP FUNCTION public.stewardship_setup_mail_audit_v1();
DROP POLICY setup_secret_mail_metadata ON public.stewardship_setup_sealed_credential;
DROP TRIGGER stewardship_setup_scrub_mail ON public.stewardship_setup_attempt;
DROP FUNCTION public.stewardship_setup_scrub_mail_v1();
DROP TRIGGER stewardship_setup_mail_admission ON public.stewardship_setup_mail_delivery;
DROP FUNCTION public.stewardship_setup_mail_guard_v1();
DROP FUNCTION public.stewardship_setup_mail_live_v1(uuid,bigint,uuid,bigint,text);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0075_setup_mail_delivery")]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_mail_delivery",
            frozen_fields=(
                "attempt_id",
                "attempt_version",
                "request_key",
                "candidate_digest",
                "credential_id",
                "credential_version",
                "fingerprint",
                "task_id",
            ),
            write_once_fields=(
                "submitted_at",
                "deadline_at",
                "finished_at",
                "scrubbed_at",
                "run_id",
                "task_fence",
                "worker_id",
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
