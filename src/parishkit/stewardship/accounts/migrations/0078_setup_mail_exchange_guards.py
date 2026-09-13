"""Only the original live mail worker can receive a one-time Workspace relay."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = """
CREATE FUNCTION public.stewardship_setup_mail_exchange_live_v1(
    delivery_id uuid, run_id uuid, task_fence bigint, worker_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
        JOIN public.stewardship_task_run task ON task.id=$2
            AND task.root_id=delivery.task_id AND task.task_type='setup_mail_test'
            AND task.domain_request_id=delivery.id
            AND task.state='running' AND task.fence=$3 AND task.worker_id=$4
            AND task.lease_expires_at>clock_timestamp()
        WHERE delivery.id=$1 AND delivery.state='queued'
            AND public.stewardship_setup_mail_live_v1(
                delivery.attempt_id,delivery.attempt_version,delivery.credential_id,
                delivery.credential_version,delivery.fingerprint)
    );
$$;

CREATE FUNCTION public.stewardship_setup_mail_exchange_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup mail exchange receipts are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mail exchange requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler')
           OR OLD.scrubbed_at IS NOT NULL OR NEW.ciphertext IS NOT NULL
           OR NEW.actor_id IS NOT NULL
           OR NEW.replied_at IS DISTINCT FROM OLD.replied_at
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
                JOIN public.stewardship_setup_attempt attempt
                    ON attempt.id=delivery.attempt_id
                WHERE delivery.id=NEW.delivery_id
                    AND attempt.state IN ('expired','completed')) THEN
            RAISE EXCEPTION 'Mail relay scrub requires its terminal original setup'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=clock_timestamp();
        RETURN NEW;
    END IF;
    IF NEW.scrubbed_at IS NOT NULL OR octet_length(NEW.public_key)<>32
       OR NOT public.stewardship_setup_mail_exchange_live_v1(
            NEW.delivery_id,NEW.run_id,NEW.task_fence,NEW.worker_id) THEN
        RAISE EXCEPTION 'Mail relay requires exact live original ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_mail_dispatch'
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NEW.ciphertext IS NOT NULL OR NEW.replied_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only the mail worker may request a Workspace relay'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_credential_google_workspace'
       OR OLD.scrubbed_at IS NOT NULL OR OLD.replied_at IS NOT NULL
       OR NEW.actor_id IS NOT NULL OR NEW.ciphertext IS NULL
       OR octet_length(NEW.ciphertext) NOT BETWEEN 1 AND 2097152
       OR NEW.replied_at IS NULL THEN
        RAISE EXCEPTION 'Only the Workspace installer may reply once'
            USING ERRCODE='23514';
    END IF;
    NEW.replied_at=clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_mail_exchange_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_setup_mail_exchange
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_exchange_guard_v1();

CREATE FUNCTION public.stewardship_setup_scrub_mail_exchanges_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_mail_exchange SET ciphertext=NULL,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE delivery_id IN (SELECT id FROM public.stewardship_setup_mail_delivery
                WHERE attempt_id=NEW.id) AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_scrub_mail_exchanges
AFTER UPDATE ON public.stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_mail_exchanges_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_mail_exchange IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_mail_exchange) THEN
        RAISE EXCEPTION 'Setup mail relay history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_scrub_mail_exchanges ON public.stewardship_setup_attempt;
DROP FUNCTION public.stewardship_setup_scrub_mail_exchanges_v1();
DROP TRIGGER stewardship_setup_mail_exchange_admission
    ON public.stewardship_setup_mail_exchange;
DROP FUNCTION public.stewardship_setup_mail_exchange_guard_v1();
DROP FUNCTION public.stewardship_setup_mail_exchange_live_v1(uuid,uuid,bigint,uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0077_setup_mail_exchange")]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_mail_exchange",
            frozen_fields=(
                "delivery_id",
                "run_id",
                "task_fence",
                "worker_id",
                "public_key",
            ),
            write_once_fields=("replied_at", "scrubbed_at"),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
