"""Fence ephemeral exchange creation/reply and scrub it with its original attempt."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

LIVE = """
CREATE FUNCTION public.stewardship_setup_exchange_live_v1(
    attempt_id uuid, credential_id uuid, credential_version bigint,
    fingerprint text, task_id uuid, task_fence bigint,
    worker_id uuid, source_fence bigint)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.attempt_id=attempt.id AND candidate.id=$2
            AND candidate.version=$3 AND candidate.fingerprint=$4
            AND candidate.target='parishsoft' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_task_run original
            ON original.id=attempt.source_task_id AND original.root_id=original.id
            AND original.created_at>clock_timestamp()-interval '2 hours'
        JOIN public.stewardship_task_run task
            ON task.id=$5 AND task.root_id=original.id
            AND task.task_type='setup_source_load'
            AND task.domain_request_id=attempt.id
            AND task.initiated_by_id=attempt.owner_id
            AND task.state='running' AND task.fence=$6 AND task.worker_id=$7
            AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_source_lease lease
            ON lease.owner_id=task.id AND lease.task_fence=task.fence
            AND lease.worker_id=task.worker_id AND lease.fence=$8
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
        WHERE attempt.id=$1 AND attempt.state='loading'
    );
$$;
"""

FORWARD = (
    LIVE
    + """
CREATE FUNCTION public.stewardship_setup_exchange_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup exchange tombstones are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup exchange requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF OLD.scrubbed_at IS NOT NULL OR NEW.ciphertext IS NOT NULL
           OR NEW.actor_id IS NOT NULL
           OR NEW.replied_at IS DISTINCT FROM OLD.replied_at
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_attempt
                WHERE id=NEW.attempt_id AND state IN ('expired','completed')) THEN
            RAISE EXCEPTION 'Setup exchange scrub needs its terminal attempt'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=clock_timestamp();
        RETURN NEW;
    END IF;
    IF NEW.scrubbed_at IS NOT NULL OR octet_length(NEW.public_key)<>32
       OR NOT public.stewardship_setup_exchange_live_v1(
            NEW.attempt_id,NEW.credential_id,NEW.credential_version,NEW.fingerprint,
            NEW.task_id,NEW.task_fence,NEW.worker_id,NEW.source_fence) THEN
        RAISE EXCEPTION 'Setup exchange requires exact live source ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_worker'
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NEW.ciphertext IS NOT NULL OR NEW.replied_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only the source worker can request an exchange'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_credential_parishsoft'
       OR OLD.scrubbed_at IS NOT NULL OR OLD.replied_at IS NOT NULL
       OR NEW.actor_id IS NOT NULL OR NEW.ciphertext IS NULL
       OR octet_length(NEW.ciphertext) NOT BETWEEN 1 AND 2097152
       OR NEW.replied_at IS NULL THEN
        RAISE EXCEPTION 'Only the target installer may reply once'
            USING ERRCODE='23514';
    END IF;
    NEW.replied_at=clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_exchange_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_setup_source_exchange
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_exchange_guard_v1();

CREATE FUNCTION public.stewardship_setup_scrub_exchanges_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_source_exchange SET ciphertext=NULL,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_scrub_exchanges
AFTER UPDATE ON public.stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_exchanges_v1();

CREATE POLICY setup_secret_source_metadata ON public.stewardship_setup_sealed_credential
FOR SELECT USING (current_user='pk_stewardship_worker' AND target='parishsoft');
"""
)

BACKWARD = """
LOCK TABLE public.stewardship_setup_source_exchange IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_source_exchange) THEN
        RAISE EXCEPTION 'Setup exchange history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP POLICY setup_secret_source_metadata ON public.stewardship_setup_sealed_credential;
DROP TRIGGER stewardship_setup_scrub_exchanges ON public.stewardship_setup_attempt;
DROP FUNCTION public.stewardship_setup_scrub_exchanges_v1();
DROP TRIGGER stewardship_setup_exchange_admission
    ON public.stewardship_setup_source_exchange;
DROP FUNCTION public.stewardship_setup_exchange_guard_v1();
DROP FUNCTION public.stewardship_setup_exchange_live_v1(
    uuid,uuid,bigint,text,uuid,bigint,uuid,bigint);
"""

_frozen = (
    "attempt_id",
    "credential_id",
    "credential_version",
    "fingerprint",
    "task_id",
    "task_fence",
    "worker_id",
    "source_fence",
    "public_key",
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0068_setup_source_exchange")]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_source_exchange",
            frozen_fields=_frozen,
            write_once_fields=("replied_at", "scrubbed_at"),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
