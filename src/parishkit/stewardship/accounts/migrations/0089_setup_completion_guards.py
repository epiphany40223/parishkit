"""Permit one fenced initial activation, inseparable from its complete population.

The worker receives no YAML or prepared-projection write authority. Additional
SQL writes are admitted only inside its exact original setup's final transaction;
deferred constraints prevent an activation or source pointer escaping alone.
"""

from importlib import import_module

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

# ruff: noqa: E501 -- keep frozen SQL predicates readable.
GUARDED_TABLES = (
    "stewardship_system_configuration",
    "stewardship_config_checkpoint",
    "stewardship_campaign",
    "stewardship_campaign_boundary",
    "stewardship_schedule_definition",
    "stewardship_schedule_selection",
    "stewardship_schedule_occurrence",
    "stewardship_occurrence_transition",
    "stewardship_policy_security_event",
    "stewardship_policy_epoch",
)

SQL = """
CREATE FUNCTION public.stewardship_setup_completion_scrub_v1(attempt_id uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN false; END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.stewardship_setup_completion completed
        JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='completed'
        JOIN public.stewardship_task_run task ON task.id=completed.task_id
            AND task.fence=completed.task_fence AND task.state='running'
            AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
            AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
            AND lease.fence=completed.source_fence AND lease.expires_at>clock_timestamp()
        WHERE attempt.id=$1
    );
END $$;
CREATE POLICY setup_secret_completed_metadata ON public.stewardship_setup_sealed_credential
FOR SELECT USING (public.stewardship_setup_completion_scrub_v1(attempt_id));
CREATE POLICY setup_secret_completed_scrub ON public.stewardship_setup_sealed_credential
FOR UPDATE USING (public.stewardship_setup_completion_scrub_v1(attempt_id))
WITH CHECK (public.stewardship_setup_completion_scrub_v1(attempt_id));

CREATE FUNCTION public.stewardship_setup_completion_cleanup_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE attempt_id uuid;
BEGIN
    IF current_user='pk_stewardship_worker' THEN
        IF TG_TABLE_NAME='stewardship_setup_mail_exchange' THEN
            SELECT delivery.attempt_id INTO attempt_id FROM public.stewardship_setup_mail_delivery delivery
                WHERE delivery.id=NEW.delivery_id;
        ELSE attempt_id=NEW.attempt_id;
        END IF;
        IF NEW.scrubbed_at IS NULL OR NOT public.stewardship_setup_completion_scrub_v1(attempt_id) THEN
            RAISE EXCEPTION 'Worker setup scrub requires its atomic completed owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_setup_completion_context_v1() RETURNS uuid
LANGUAGE sql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT prepared.id FROM public.stewardship_setup_prepared prepared
    JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
    JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
    JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
        AND attempt.state='frozen' AND attempt.version=intent.attempt_version
    JOIN public.stewardship_config_request request ON request.id=intent.request_id
        AND request.request_schema='initial-setup-patch-v7'
        AND request.candidate_version_id=prepared.configuration_id
        AND request.base_id=attempt.base_id AND request.actor_id=attempt.owner_id
    JOIN public.stewardship_system_configuration runtime
        ON runtime.active_configuration_id IN (attempt.base_id,prepared.configuration_id)
        AND runtime.mode='testing' AND NOT runtime.restore_review_required
        AND (runtime.current_campaign_id IS NULL OR runtime.current_campaign_id=attempt.id)
    JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
        AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
        AND login.expires_at>clock_timestamp()
        AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
    JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id AND NOT owner.disabled
    JOIN public.stewardship_address_rule rule ON rule.configuration_id=attempt.base_id
        AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
    JOIN public.stewardship_task_run task ON task.domain_request_id=prepared.id
        AND task.task_type='setup_finalize' AND task.initiated_by_id=attempt.owner_id
        AND task.state='running' AND task.lease_expires_at>clock_timestamp()
        AND task.created_at>=prepared.created_at
    JOIN public.stewardship_task_run root ON root.id=task.root_id
        AND root.task_type=task.task_type AND root.domain_request_id=prepared.id
        AND root.idempotency_key=prepared.id::text AND root.initiated_by_id=attempt.owner_id
    JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
        AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
        AND lease.phase='full' AND lease.expires_at>clock_timestamp()
    JOIN public.stewardship_source_snapshot snapshot ON snapshot.task_id=task.id
        AND snapshot.source_fence=lease.fence AND snapshot.state='promoted'
        AND snapshot.started_at>=prepared.created_at AND snapshot.base_id IS NULL
    JOIN public.stewardship_source_current current ON current.snapshot_id=snapshot.id
        AND current.generation=snapshot.generation AND current.organization_id=snapshot.organization_id
    WHERE current_user='pk_stewardship_worker'
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_setup_config_abort WHERE intent_id=intent.id)
        AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted);
$$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_activation_owner_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' OR EXISTS (
        SELECT 1 FROM public.stewardship_config_request
        WHERE id=NEW.request_id AND request_schema='initial-setup-patch-v7') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_prepared prepared
            JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
            JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
            JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            WHERE prepared.id=public.stewardship_setup_completion_context_v1()
                AND prepared.configuration_id=NEW.configuration_id
                AND intent.request_id=NEW.request_id AND attempt.owner_id=NEW.actor_id
                AND attempt.base_id=NEW.predecessor_id
        ) THEN
            RAISE EXCEPTION 'Setup activation requires its exact atomic owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_setup_completion_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
        public.stewardship_setup_completion_context_v1() IS NULL THEN
        RAISE EXCEPTION 'Worker configuration effects require atomic setup ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_setup_testing_recipient_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.testing_recipient IS DISTINCT FROM OLD.testing_recipient AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_prepared prepared
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        WHERE prepared.id=public.stewardship_setup_completion_context_v1()
            AND NEW.active_configuration_id=prepared.configuration_id
            AND NEW.testing_recipient=ready.testing_recipient
    ) THEN
        RAISE EXCEPTION 'Testing recipient requires the exact initial activation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_testing_recipient BEFORE UPDATE
ON public.stewardship_system_configuration
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_testing_recipient_v1();

CREATE FUNCTION public.stewardship_setup_completion_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.preparation_id IS DISTINCT FROM public.stewardship_setup_completion_context_v1()
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_prepared prepared
            JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
            JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
            JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            JOIN public.stewardship_config_activation activation
                ON activation.id=NEW.activation_id AND activation.request_id=intent.request_id
                AND activation.configuration_id=prepared.configuration_id
            JOIN public.stewardship_system_configuration runtime
                ON runtime.active_configuration_id=prepared.configuration_id
                AND runtime.current_campaign_id=attempt.id AND runtime.mode='testing'
                AND runtime.testing_recipient=ready.testing_recipient
            JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=NEW.snapshot_id
                AND snapshot.task_id=NEW.task_id AND snapshot.source_fence=NEW.source_fence
                AND snapshot.state='promoted' AND snapshot.kind='full'
            JOIN public.stewardship_source_current current ON current.snapshot_id=snapshot.id
            JOIN public.stewardship_source_lease lease ON lease.owner_id=NEW.task_id
                AND lease.fence=NEW.source_fence AND lease.task_fence=NEW.task_fence
            JOIN public.stewardship_campaign_credentials population
                ON population.campaign_id=attempt.id AND population.source_snapshot_id=snapshot.id
                AND population.source_generation=snapshot.generation AND NOT population.population_dirty
                AND population.eligible_count=(SELECT count(*) FROM public.stewardship_family_campaign
                    WHERE campaign_id=attempt.id AND portal_eligible)
            WHERE prepared.id=NEW.preparation_id AND NEW.actor_id=attempt.owner_id
                AND snapshot.cursor->>'window_digest'=encode(sha256(convert_to(
                    public.stewardship_source_current_window_v1(attempt.id),'UTF8')),'hex')
                AND (SELECT count(*) FROM public.stewardship_family_campaign WHERE campaign_id=attempt.id)
                    = (SELECT count(*) FROM public.stewardship_snapshot_family WHERE snapshot_id=snapshot.id)
                AND NOT EXISTS (
                    SELECT 1 FROM public.stewardship_snapshot_family member
                    JOIN public.stewardship_source_family payload ON payload.id=member.payload_id
                    LEFT JOIN public.stewardship_family_campaign family ON family.campaign_id=attempt.id
                        AND family.family_duid=member.source_key::bigint
                    WHERE member.snapshot_id=snapshot.id AND (
                        family.id IS NULL OR family.source_generation IS DISTINCT FROM snapshot.generation
                        OR family.active IS DISTINCT FROM (payload.canonical::jsonb->>'active')::boolean
                        OR family.portal_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'portal_eligible')::boolean
                        OR family.email_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'email_eligible')::boolean
                    )
                )
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign family
                    WHERE family.campaign_id=attempt.id AND family.portal_eligible
                        AND (coalesce(family.code_ciphertext,'')='' OR NOT EXISTS (
                            SELECT 1 FROM public.stewardship_family_code_mac mac WHERE mac.family_id=family.id)))
        ) THEN
        RAISE EXCEPTION 'Setup completion requires exact activation, source and population'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_completion_insert BEFORE INSERT
ON public.stewardship_setup_completion
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_insert_v1();

CREATE FUNCTION public.stewardship_setup_completion_required_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE prepared_id uuid;
BEGIN
    IF TG_TABLE_NAME='stewardship_config_activation' THEN
        SELECT prepared.id INTO prepared_id FROM public.stewardship_setup_prepared prepared
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        WHERE intent.request_id=NEW.request_id;
    ELSIF TG_TABLE_NAME='stewardship_source_current' THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_source_snapshot snapshot
            JOIN public.stewardship_task_run task ON task.id=snapshot.task_id
            WHERE snapshot.id=NEW.snapshot_id AND task.task_type='setup_source_load') THEN
            RAISE EXCEPTION 'Initial catalog is never promoted source truth' USING ERRCODE='23514';
        END IF;
        SELECT task.domain_request_id INTO prepared_id FROM public.stewardship_source_snapshot snapshot
        JOIN public.stewardship_task_run task ON task.id=snapshot.task_id AND task.task_type='setup_finalize'
        WHERE snapshot.id=NEW.snapshot_id;
    ELSE
        prepared_id=NEW.preparation_id;
    END IF;
    IF prepared_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_completion completed
        JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id AND attempt.state='completed'
        JOIN public.stewardship_task_run task ON task.id=completed.task_id
            AND task.state='succeeded' AND task.fence=completed.task_fence
        WHERE completed.preparation_id=prepared_id
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_lease WHERE owner_id=task.id)
    ) THEN
        RAISE EXCEPTION 'Initial configuration and source require atomic completion'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_setup_activation_completion AFTER INSERT
ON public.stewardship_config_activation DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();
CREATE CONSTRAINT TRIGGER stewardship_setup_source_completion AFTER UPDATE
ON public.stewardship_source_current DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();
CREATE CONSTRAINT TRIGGER stewardship_setup_atomic_completion AFTER INSERT
ON public.stewardship_setup_completion DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();
"""

ATTEMPT_ENTRY = "    IF current_user='pk_stewardship_worker' THEN\n"
ATTEMPT_COMPLETION = """    IF TG_OP='UPDATE' AND NEW.state='completed' THEN
        IF current_user<>'pk_stewardship_worker' OR OLD.state<>'frozen'
            OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
            OR (to_jsonb(NEW)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
            OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
                WHERE intent.attempt_id=OLD.id AND intent.attempt_version=OLD.version
                    AND prepared.id=public.stewardship_setup_completion_context_v1()
            ) THEN
            RAISE EXCEPTION 'Setup completion requires its atomic finalization receipt'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
"""
RECIPIENT_ENTRY = "active_configuration_id = NEW.configuration_id,"
RECIPIENT_ASSIGNMENT = """active_configuration_id = NEW.configuration_id,
            testing_recipient = coalesce((SELECT ready.testing_recipient
                FROM public.stewardship_setup_prepared prepared
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                WHERE prepared.configuration_id=NEW.configuration_id),testing_recipient),"""
REPLACEMENTS = (
    (
        "stewardship_setup_attempt_guard_v1",
        ATTEMPT_ENTRY,
        ATTEMPT_COMPLETION + ATTEMPT_ENTRY,
    ),
    (
        "stewardship_setup_attempt_audit_v1",
        "WHEN 'expired' THEN 'setup_expired'",
        "WHEN 'completed' THEN 'setup_completed' WHEN 'expired' THEN 'setup_expired'",
    ),
    (
        "stewardship_setup_attempt_audit_v1",
        "CASE WHEN NEW.state='expired' THEN 'cancelled' ELSE 'changed' END",
        "CASE WHEN NEW.state='completed' THEN 'succeeded' WHEN NEW.state='expired' THEN 'cancelled' ELSE 'changed' END",
    ),
    (
        "stewardship_system_configuration_mutable_v1",
        'NEW."testing_recipient" IS DISTINCT FROM OLD."testing_recipient"',
        "false /* initial setup recipient has its own guard */",
    ),
    ("stewardship_activation_effects_v1", RECIPIENT_ENTRY, RECIPIENT_ASSIGNMENT),
    *(
        (
            name,
            "current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler')",
            "current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler','pk_stewardship_worker')",
        )
        for name in (
            "stewardship_setup_mail_guard_v1",
            "stewardship_setup_mail_exchange_guard_v1",
        )
    ),
)

CLEANUP_TABLES = (
    "stewardship_setup_draft_section",
    "stewardship_setup_sealed_credential",
    "stewardship_setup_source_exchange",
    "stewardship_setup_mail_delivery",
    "stewardship_setup_mail_exchange",
)


def extend_owners(apps, editor):
    """Retain all previous guards; fail migration if any predecessor has drifted."""
    with editor.connection.cursor() as cursor:
        for name, before, after in REPLACEMENTS:
            cursor.execute(
                "SELECT pg_get_functiondef(%s::regprocedure)", (f"public.{name}()",)
            )
            original = cursor.fetchone()[0]
            if original.count(before) != 1:
                raise RuntimeError(f"Initial completion predecessor differs: {name}.")
            editor.execute(original.replace(before, after), params=None)
    for table in GUARDED_TABLES:
        editor.execute(
            f"CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();"
        )
    for table in CLEANUP_TABLES:
        editor.execute(
            f"CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();"
        )


def restore_owners(apps, editor):
    """Never downgrade a completed deployment into an unconfigured one."""
    editor.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.stewardship_setup_completion) THEN
            RAISE EXCEPTION 'Completed setup history prevents downgrade' USING ERRCODE='23514';
        END IF;
    END $$;""")
    with editor.connection.cursor() as cursor:
        for name, before, after in reversed(REPLACEMENTS):
            cursor.execute(
                "SELECT pg_get_functiondef(%s::regprocedure)", (f"public.{name}()",)
            )
            definition = cursor.fetchone()[0]
            if definition.count(after) != 1:
                raise RuntimeError(f"Initial completion successor differs: {name}.")
            editor.execute(definition.replace(after, before), params=None)
    for table in GUARDED_TABLES:
        editor.execute(f"DROP TRIGGER aaa_setup_completion_write ON public.{table};")
    for table in CLEANUP_TABLES:
        editor.execute(f"DROP TRIGGER aaa_setup_completion_cleanup ON public.{table};")


_previous = import_module(
    "parishkit.stewardship.accounts.migrations.0064_setup_configuration_guards"
).FORWARD
_start = _previous.index(
    "CREATE FUNCTION public.stewardship_setup_activation_owner_v1()"
)
_end = _previous.index("END $$;", _start) + len("END $$;")
REVERSE = (
    _previous[_start:_end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    + """
DROP TRIGGER stewardship_setup_atomic_completion ON public.stewardship_setup_completion;
DROP TRIGGER stewardship_setup_source_completion ON public.stewardship_source_current;
DROP TRIGGER stewardship_setup_activation_completion ON public.stewardship_config_activation;
DROP FUNCTION public.stewardship_setup_completion_required_v1();
DROP TRIGGER stewardship_setup_completion_insert ON public.stewardship_setup_completion;
DROP FUNCTION public.stewardship_setup_completion_insert_v1();
DROP TRIGGER stewardship_setup_testing_recipient ON public.stewardship_system_configuration;
DROP FUNCTION public.stewardship_setup_testing_recipient_v1();
DROP FUNCTION public.stewardship_setup_completion_write_v1();
DROP FUNCTION public.stewardship_setup_completion_context_v1();
DROP POLICY setup_secret_completed_scrub ON public.stewardship_setup_sealed_credential;
DROP POLICY setup_secret_completed_metadata ON public.stewardship_setup_sealed_credential;
DROP FUNCTION public.stewardship_setup_completion_cleanup_write_v1();
DROP FUNCTION public.stewardship_setup_completion_scrub_v1(uuid);
"""
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0088_setup_completion")]
    operations = [
        immutable_guard_v1("stewardship_setup_completion"),
        migrations.RunSQL(SQL, REVERSE),
        migrations.RunPython(extend_owners, restore_owners),
    ]
