"""Exact Testing-only campaign mail and irreversible finite submission outcomes."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = r"""
CREATE FUNCTION public.stewardship_campaign_mail_live_v1(
    configuration_id uuid,campaign_id uuid,template_id uuid,
    fingerprint text,requested_by uuid)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_campaign campaign
            ON campaign.id=runtime.current_campaign_id AND campaign.state='draft'
        JOIN public.stewardship_content_version template
            ON template.configuration_id=runtime.active_configuration_id
            AND template.campaign_id=campaign.id AND template.kind='email'
        JOIN public.stewardship_applied_integration workspace
            ON workspace.configuration_id=runtime.active_configuration_id
            AND workspace.kind='google_workspace'
        JOIN public.stewardship_portal_user owner ON owner.id=$5 AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=runtime.active_configuration_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.active_configuration_id=$1 AND campaign.id=$2
            AND template.id=$3 AND workspace.credential_fingerprint=$4
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
                WHERE gate.state IN ('preparing','running'))
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials c
                WHERE c.go_live_gate)
    );
$$;

CREATE FUNCTION public.stewardship_campaign_mail_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owned boolean; live boolean; keys text[]; stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Campaign test outcomes are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Campaign test requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    live=public.stewardship_campaign_mail_live_v1(NEW.configuration_id,
        NEW.campaign_id,NEW.template_id,NEW.fingerprint,NEW.requested_by_id);
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web' OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='campaign_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.idempotency_key=NEW.id::text
                    AND task.initiated_by_id=NEW.requested_by_id) THEN
            RAISE EXCEPTION 'Campaign mail requires explicit current Admin intent'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.mail) IS DISTINCT FROM 'object'
           OR octet_length(NEW.mail::text)>1048576 THEN
            RAISE EXCEPTION 'Invalid campaign mail sample' USING ERRCODE='23514';
        END IF;
        SELECT array_agg(key ORDER BY key) INTO keys
            FROM jsonb_object_keys(NEW.mail) key;
        IF keys IS DISTINCT FROM ARRAY['delivery_id','html','recipient','reply_to',
                                      'sender','subject','text']
           OR EXISTS (SELECT 1 FROM jsonb_each(NEW.mail) pair
                WHERE jsonb_typeof(pair.value)<>'string')
           OR NEW.mail->>'delivery_id' IS DISTINCT FROM NEW.id::text
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration runtime
                JOIN public.stewardship_applied_integration email
                    ON email.configuration_id=runtime.active_configuration_id
                    AND email.kind='email'
                WHERE runtime.testing_recipient=NEW.mail->>'recipient'
                    AND email.settings->>'sender'=NEW.mail->>'sender'
                    AND email.settings->>'reply_to'=NEW.mail->>'reply_to') THEN
            RAISE EXCEPTION 'Campaign test may address only the Testing recipient'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','submitting') THEN
        RAISE EXCEPTION 'Terminal campaign test cannot be rewritten'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user<>'pk_stewardship_scheduler' OR NEW.actor_id IS NOT NULL
           OR (live AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id
                    AND task.state IN ('failed','cancelled'))) THEN
            RAISE EXCEPTION 'Only stale unsent campaign tests can be cancelled'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT EXISTS (SELECT 1 FROM public.stewardship_task_run task
            WHERE task.id=NEW.run_id AND task.id=NEW.task_id
                AND task.root_id=NEW.task_id
                AND task.task_type='campaign_mail_test'
                AND task.domain_request_id=NEW.id
                AND task.initiated_by_id=NEW.requested_by_id AND task.state='running'
                AND task.fence=NEW.task_fence AND task.worker_id=NEW.worker_id
                AND task.lease_expires_at>stamp) INTO owned;
        IF OLD.state='queued' AND NEW.state='submitting' THEN
            IF current_user<>'pk_stewardship_mail_dispatch' OR NOT live OR NOT owned
               OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR NEW.mail IS DISTINCT FROM OLD.mail
               OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                    WHERE task.id=NEW.run_id
                        AND task.lease_expires_at>stamp+interval '30 seconds') THEN
                RAISE EXCEPTION 'Only the live mail worker may begin a campaign test'
                    USING ERRCODE='23514';
            END IF;
            NEW.submitted_at=stamp; NEW.deadline_at=stamp+interval '30 seconds';
            RETURN NEW;
        ELSIF OLD.state='submitting'
              AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
            IF current_user='pk_stewardship_mail_dispatch' AND owned
               AND NEW.actor_id=NEW.worker_id THEN
                IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                    RAISE EXCEPTION 'Late campaign test result remains unknown'
                        USING ERRCODE='23514';
                END IF;
            ELSIF current_user='pk_stewardship_scheduler' AND NOT owned
                  AND stamp>=OLD.deadline_at AND NEW.state='delivery_unknown'
                  AND NEW.actor_id IS NULL THEN
                NULL;
            ELSE
                RAISE EXCEPTION 'Campaign test outcome requires live or drained owner'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Invalid campaign test transition' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.finished_at=stamp; NEW.mail='{}'::jsonb;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_mail_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_campaign_mail_test
FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mail_guard_v1();

CREATE FUNCTION public.stewardship_campaign_mail_audit_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        'campaign_mail_'||NEW.state,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',CASE
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_campaign_mail_audit
AFTER INSERT OR UPDATE ON public.stewardship_campaign_mail_test
FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mail_audit_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_campaign_mail_test IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_campaign_mail_test) THEN
        RAISE EXCEPTION 'Campaign test history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_campaign_mail_audit ON public.stewardship_campaign_mail_test;
DROP FUNCTION public.stewardship_campaign_mail_audit_v1();
DROP TRIGGER stewardship_campaign_mail_admission
    ON public.stewardship_campaign_mail_test;
DROP FUNCTION public.stewardship_campaign_mail_guard_v1();
DROP FUNCTION public.stewardship_campaign_mail_live_v1(uuid,uuid,uuid,text,uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0090_campaign_mail_test")]
    operations = [
        mutable_guard_v1(
            "stewardship_campaign_mail_test",
            frozen_fields=(
                "configuration_id",
                "campaign_id",
                "template_id",
                "requested_by_id",
                "request_key",
                "fingerprint",
                "task_id",
            ),
            write_once_fields=(
                "submitted_at",
                "deadline_at",
                "finished_at",
                "run_id",
                "task_fence",
                "worker_id",
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
