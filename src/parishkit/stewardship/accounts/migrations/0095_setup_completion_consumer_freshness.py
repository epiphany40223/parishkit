"""Recheck exact initial consumer receipts at the atomic activation boundary."""

from django.db import migrations

# ruff: noqa: E501 -- preserve complete frozen SQL predicates.
FORWARD = """
CREATE FUNCTION public.stewardship_setup_final_receipt_read_v1(request_id uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN false; END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_prepared prepared ON prepared.readiness_id=binding.readiness_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='frozen' AND attempt.version=intent.attempt_version
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_task_run task ON task.domain_request_id=prepared.id
            AND task.task_type='setup_finalize' AND task.state='running'
            AND task.initiated_by_id=attempt.owner_id AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_task_run root ON root.id=task.root_id
            AND root.domain_request_id=prepared.id AND root.task_type=task.task_type
            AND root.idempotency_key=prepared.id::text
            AND root.initiated_by_id=attempt.owner_id
        JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
            AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
        WHERE binding.request_id=$1 AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_config_abort WHERE intent_id=intent.id)
    );
END $$;
CREATE POLICY setup_final_receipt_read ON public.stewardship_secret_request
FOR SELECT USING (public.stewardship_setup_final_receipt_read_v1(id));

CREATE FUNCTION public.stewardship_setup_consumers_current_v1(preparation_id uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE readiness public.stewardship_setup_readiness_binding%ROWTYPE;
    candidate public.stewardship_configuration_version%ROWTYPE;
    integration jsonb; bound public.stewardship_setup_credential_install%ROWTYPE;
    installed public.stewardship_secret_request%ROWTYPE; target_count integer=0;
BEGIN
    -- Serialize the observation through activation against the existing secret
    -- state owner. This follows setup's installation/work/secret lock order;
    -- no credential file or provider IO occurs while this transaction is held.
    PERFORM pg_advisory_xact_lock(736213,1);
    SELECT ready.* INTO readiness FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_prepared prepared ON prepared.readiness_id=ready.id
        WHERE prepared.id=$1;
    SELECT version.* INTO candidate FROM public.stewardship_configuration_version version
        JOIN public.stewardship_setup_prepared prepared ON prepared.configuration_id=version.id
        WHERE prepared.id=$1;
    IF readiness.id IS NULL OR candidate.id IS NULL THEN RETURN false; END IF;
    FOR integration IN SELECT value->'values' FROM jsonb_array_elements(
        candidate.canonical_document->'sections'->'integrations')
        WHERE value->'values'->>'kind' IN ('parishsoft','google_workspace','slack')
    LOOP
        target_count=target_count+1;
        SELECT id,request_id,target,fingerprint INTO bound.id,bound.request_id,bound.target,bound.fingerprint
            FROM public.stewardship_setup_credential_install
            WHERE readiness_id=readiness.id AND target=integration->>'kind';
        SELECT * INTO installed FROM public.stewardship_secret_request WHERE id=bound.request_id;
        IF bound.id IS NULL OR installed.id IS NULL
            OR installed.target IS DISTINCT FROM bound.target
            OR bound.fingerprint IS DISTINCT FROM integration->>'credential_fingerprint'
            OR installed.resulting_fingerprint IS DISTINCT FROM bound.fingerprint
            OR installed.state IS DISTINCT FROM 'awaiting_ack'
            OR installed.expires_at<=clock_timestamp()
            OR installed.required_consumers IS DISTINCT FROM
                public.stewardship_credential_consumers_v1(bound.target)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(installed.required_consumers) required(consumer_name)
                WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_credential_consumer_ack ack
                    WHERE ack.request_id=installed.id AND ack.consumer=required.consumer_name
                        AND ack.fingerprint=bound.fingerprint)) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN target_count=(CASE WHEN readiness.slack_delivery_id IS NULL THEN 2 ELSE 3 END)
        AND target_count=(SELECT count(*) FROM public.stewardship_setup_credential_install
            WHERE readiness_id=readiness.id);
END $$;
"""
BACKWARD = """
DROP FUNCTION public.stewardship_setup_consumers_current_v1(uuid);
DROP POLICY setup_final_receipt_read ON public.stewardship_secret_request;
DROP FUNCTION public.stewardship_setup_final_receipt_read_v1(uuid);
"""


def rewrite(editor, *, reverse=False):
    """Recheck after preparation without weakening any original completion gate."""
    before = "WHERE current_user='pk_stewardship_worker'\n        AND NOT EXISTS"
    after = "WHERE current_user='pk_stewardship_worker'\n        AND public.stewardship_setup_consumers_current_v1(prepared.id)\n        AND NOT EXISTS"
    if reverse:
        before, after = after, before
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('public.stewardship_setup_completion_context_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    if definition.count(before) != 1:
        raise RuntimeError("Setup completion consumer predecessor differs.")
    editor.execute(definition.replace(before, after), params=None)


def forward(apps, editor):
    """Read only bound public receipts, never foreign credential plaintext."""
    editor.execute(FORWARD, params=None)
    rewrite(editor)


def backward(apps, editor):
    """Remove the recheck only when no completed history depends on it."""
    editor.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.stewardship_setup_completion) THEN
            RAISE EXCEPTION 'Completed setup history prevents downgrade' USING ERRCODE='23514';
        END IF;
    END $$;""")
    rewrite(editor, reverse=True)
    editor.execute(BACKWARD)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0094_chair_transaction_wraparound")]
    operations = [migrations.RunPython(forward, backward)]
