"""Keep wizard ownership, watchdogs and terminal metadata outside ordinary CRUD.

Configured-marker activation remains disabled until its complete installer owner
lands. No runtime role receives setup INSERT/UPDATE from this storage migration.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

# ruff: noqa: E501
FORWARD = """
CREATE FUNCTION stewardship_setup_attempt_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE stamp timestamptz:=clock_timestamp(); login stewardship_portal_session%ROWTYPE;
        task stewardship_task_run%ROWTYPE; selected uuid; live boolean; reason text;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mutation requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT active_configuration_id INTO selected FROM stewardship_system_configuration
        WHERE mode='testing' AND NOT restore_review_required;
    SELECT s.* INTO login FROM stewardship_portal_session s
        JOIN stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
        JOIN stewardship_address_rule rule ON rule.configuration_id=selected
            AND rule.email=u.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE s.id=NEW.session_id AND s.principal_id=NEW.owner_id;
    live=login.id IS NOT NULL AND login.revoked_at IS NULL
        AND login.expires_at>stamp AND login.last_activity_at>stamp-interval '30 minutes';
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'collecting' OR NEW.source_task_id IS NOT NULL
           OR NEW.renewed_at IS NOT NULL OR NEW.version<>1 OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR selected IS DISTINCT FROM NEW.base_id
           OR NOT EXISTS (SELECT 1 FROM stewardship_configuration_version
                WHERE id=selected AND validation_schema='bootstrap-policy-v1') THEN
            RAISE EXCEPTION 'Setup requires the original live bootstrap Admin session'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('expired','completed') OR NEW.state='completed' THEN
        RAISE EXCEPTION 'Setup completion requires its configured-marker owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS NOT NULL THEN
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.source_task_id;
        IF task.task_type IS DISTINCT FROM 'setup_source_load'
           OR task.domain_request_id IS DISTINCT FROM NEW.id
           OR task.initiated_by_id IS DISTINCT FROM NEW.owner_id
           OR task.created_at<NEW.created_at OR task.root_id<>task.id THEN
            RAISE EXCEPTION 'Setup source identity is not the original bound load'
                USING ERRCODE='23514';
        END IF;
    END IF;
    reason=CASE
        WHEN login.id IS NULL OR login.revoked_at IS NOT NULL THEN 'session'
        WHEN stamp>=login.expires_at THEN 'absolute'
        WHEN OLD.state='loading' AND stamp>=task.created_at+interval '2 hours' THEN 'watchdog'
        WHEN stamp>=login.last_activity_at+interval '30 minutes' THEN 'idle'
        ELSE NULL END;
    IF NEW.state='expired' THEN
        IF reason IS NULL THEN
            IF NOT live OR NEW.actor_id IS DISTINCT FROM NEW.owner_id THEN
                RAISE EXCEPTION 'Only the owning live Admin can cancel setup'
                    USING ERRCODE='23514';
            END IF;
            reason='cancelled';
        END IF;
        NEW.expiry_reason=reason; NEW.expired_at=stamp;
        IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id THEN
            RAISE EXCEPTION 'Setup expiry cannot change its retained bindings'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF reason IS NOT NULL OR NOT live THEN
        RAISE EXCEPTION 'Expired setup cannot accept later work' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>'frozen' AND (selected IS DISTINCT FROM NEW.base_id
        OR NEW.actor_id IS DISTINCT FROM NEW.owner_id) THEN
        RAISE EXCEPTION 'Setup no longer has its original authorized base'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='frozen' OR (
        (OLD.state=NEW.state AND NEW.state IN ('collecting','loading'))
        OR (OLD.state='collecting' AND NEW.state='loading' AND OLD.source_task_id IS NULL)
        OR (OLD.state='loading' AND NEW.state='collecting' AND task.state='succeeded')
        OR (OLD.state='collecting' AND NEW.state='frozen' AND task.state='succeeded')) IS NOT TRUE THEN
        RAISE EXCEPTION 'Setup transition has no completed owning work'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
       AND NOT (OLD.state='collecting' AND NEW.state='loading') THEN
        RAISE EXCEPTION 'Setup load binding requires its loading transition'
            USING ERRCODE='23514';
    END IF;
    IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at THEN
        IF OLD.state<>'loading' OR NEW.state<>'loading' OR task.state<>'running'
           OR task.lease_expires_at<=stamp OR task.heartbeat_at IS NULL
           OR task.heartbeat_at<=stamp-interval '90 seconds'
           OR stamp<coalesce(OLD.renewed_at,task.created_at)+interval '5 minutes'
           OR stamp>=task.created_at+interval '2 hours'
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease lease
               WHERE lease.owner_id=task.id AND lease.task_fence=task.fence
                 AND lease.worker_id=task.worker_id AND lease.expires_at>stamp
                 AND lease.heartbeat_at>stamp-interval '90 seconds') THEN
            RAISE EXCEPTION 'Setup renewal requires live correlated source ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.renewed_at=stamp;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_attempt_admission
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_setup_attempt_guard_v1();

CREATE FUNCTION stewardship_setup_attempt_audit_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid(); kind text;
BEGIN
    IF TG_OP='UPDATE' AND NEW.state=OLD.state THEN RETURN NULL; END IF;
    kind=CASE NEW.state WHEN 'collecting' THEN
            CASE WHEN TG_OP='INSERT' THEN 'setup_started' ELSE 'setup_source_completed' END
        WHEN 'loading' THEN 'setup_source_started'
        WHEN 'frozen' THEN 'setup_frozen'
        WHEN 'expired' THEN 'setup_expired'
        ELSE NULL END;
    IF kind IS NULL THEN
        RAISE EXCEPTION 'Setup audit requires an implemented lifecycle state'
            USING ERRCODE='23514';
    END IF;
    INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,
        event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,kind,NEW.id,'deployment');
    INSERT INTO stewardship_audit_context(id,created_at,actor_id,correlation_id,
        event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',
            CASE WHEN NEW.state='expired' THEN 'cancelled' ELSE 'changed' END));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_attempt_audit
AFTER INSERT OR UPDATE ON stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_setup_attempt_audit_v1();
"""

REVERSE = """
LOCK TABLE stewardship_setup_attempt IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_setup_attempt) THEN
        RAISE EXCEPTION 'Retained setup attempts prevent schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_attempt_audit ON stewardship_setup_attempt;
DROP FUNCTION stewardship_setup_attempt_audit_v1();
DROP TRIGGER stewardship_setup_attempt_admission ON stewardship_setup_attempt;
DROP FUNCTION stewardship_setup_attempt_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0046_setup_attempt"),
        ("stewardship_source", "0002_source_lease_guards"),
    ]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_attempt",
            frozen_fields=("session_id", "owner_id", "base_id"),
            write_once_fields=("source_task_id", "expired_at"),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
