"""Isolated initial credential intake and rollback retention until setup commits."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

# ruff: noqa: E501 -- frozen SQL predicates retain readable correspondence.
FORWARD = """
CREATE FUNCTION public.stewardship_setup_install_ready_live_v1(ready_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='frozen' AND attempt.version=intent.attempt_version
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base ON base.id=attempt.base_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.configuration_id=attempt.base_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE ready.id=$1
    );
$$;

CREATE FUNCTION public.stewardship_setup_install_selected_v1(
    ready_id uuid, credential_id uuid, credential_version bigint, fingerprint text)
RETURNS boolean LANGUAGE sql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=$2 AND credential.attempt_id=intent.attempt_id
            AND credential.version=$3 AND credential.fingerprint=$4
            AND credential.scrubbed_at IS NULL
        WHERE ready.id=$1 AND CASE credential.target
            WHEN 'parishsoft' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange ON exchange.id=result.exchange_id
                WHERE result.id=ready.source_result_id AND exchange.credential_id=$2
                    AND exchange.credential_version=$3 AND exchange.fingerprint=$4)
            WHEN 'google_workspace' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
                WHERE delivery.id=ready.mail_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            WHEN 'slack' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
                WHERE delivery.id=ready.slack_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            ELSE false END
    );
$$;

CREATE FUNCTION public.stewardship_setup_install_completed_v1(request_id uuid)
RETURNS timestamptz LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- Ordinary requests never require privileges on private setup proof tables.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install binding
        WHERE binding.request_id=$1) THEN RETURN NULL; END IF;
    RETURN (SELECT attempt.updated_at
        FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=binding.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='completed'
        JOIN public.stewardship_secret_request request ON request.id=binding.request_id
            AND attempt.updated_at<request.expires_at
        WHERE binding.request_id=$1);
END $$;

CREATE FUNCTION public.stewardship_setup_install_live_v1(request_id uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE readiness uuid;
BEGIN
    SELECT binding.readiness_id INTO readiness
        FROM public.stewardship_setup_credential_install binding WHERE binding.request_id=$1;
    IF readiness IS NULL THEN RETURN false; END IF;
    RETURN public.stewardship_setup_install_ready_live_v1(readiness);
END $$;

CREATE FUNCTION public.stewardship_setup_install_binding_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE credential public.stewardship_setup_sealed_credential%ROWTYPE;
    request public.stewardship_secret_request%ROWTYPE;
    attempt public.stewardship_setup_attempt%ROWTYPE;
    login public.stewardship_portal_session%ROWTYPE;
BEGIN
    IF current_user<>'pk_stewardship_credential_'||NEW.target
        OR NOT public.stewardship_setup_install_ready_live_v1(NEW.readiness_id)
        OR NOT public.stewardship_setup_install_selected_v1(
            NEW.readiness_id,NEW.credential_id,NEW.credential_version,NEW.fingerprint)
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736213 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Initial credential requires its isolated frozen setup owner'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO credential FROM public.stewardship_setup_sealed_credential
        WHERE id=NEW.credential_id;
    SELECT * INTO request FROM public.stewardship_secret_request WHERE id=NEW.request_id;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt WHERE id=credential.attempt_id;
    SELECT id,authenticated_at,expires_at INTO login.id,login.authenticated_at,login.expires_at
        FROM public.stewardship_portal_session WHERE id=attempt.session_id;
    IF NEW.request_id<>NEW.credential_id OR request.target IS DISTINCT FROM NEW.target
        OR credential.target IS DISTINCT FROM NEW.target
        OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.requested_by_id IS DISTINCT FROM attempt.owner_id
        OR request.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.state IS DISTINCT FROM 'staged' OR request.version<>1
        OR request.reauthenticated_at IS DISTINCT FROM login.authenticated_at
        OR request.expires_at>login.expires_at OR request.expires_at<=clock_timestamp()
        OR request.required_consumers IS DISTINCT FROM stewardship_credential_consumers_v1(NEW.target)
        THEN
        RAISE EXCEPTION 'Initial credential request differs from its original setup'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_install_binding
BEFORE INSERT ON public.stewardship_setup_credential_install
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_binding_v1();

CREATE FUNCTION public.stewardship_setup_install_intake_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- A separate statement avoids planning private-column queries for web
    -- intake. SQL boolean short-circuiting is not a privilege boundary.
    IF current_user<>'pk_stewardship_credential_'||NEW.target OR NEW.target NOT IN
        ('parishsoft','google_workspace','slack') THEN RETURN NULL; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_sealed_credential credential ON credential.id=binding.credential_id
        JOIN public.stewardship_sealed_credential_staging staging ON staging.request_id=NEW.id
            AND staging.ciphertext=credential.ciphertext AND staging.fingerprint=binding.fingerprint
        JOIN public.stewardship_provider_context context ON context.request_id=NEW.id
            AND context.settings=credential.settings AND context.target=NEW.target
        WHERE binding.request_id=NEW.id AND binding.target=NEW.target) THEN
        RAISE EXCEPTION 'Isolated intake requires its atomic initial credential binding'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_setup_install_intake
AFTER INSERT ON public.stewardship_secret_request DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_intake_v1();

CREATE FUNCTION public.stewardship_setup_install_progress_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE completed timestamptz;
BEGIN
    IF NEW.target NOT IN ('parishsoft','google_workspace','slack') THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install
        WHERE request_id=NEW.id) THEN RETURN NEW; END IF;
    completed=public.stewardship_setup_install_completed_v1(NEW.id);
    IF NEW.state IN ('testing','installing','awaiting_ack')
        AND NOT public.stewardship_setup_install_live_v1(NEW.id) THEN
        RAISE EXCEPTION 'Initial credential setup ownership has ended' USING ERRCODE='23514';
    END IF;
    IF NEW.state='cleanup_pending' THEN
        IF NEW.cleanup_reason='applied' AND completed IS NULL THEN
            RAISE EXCEPTION 'Initial rollback must remain until setup commits' USING ERRCODE='23514';
        ELSIF NEW.cleanup_reason<>'applied' AND completed IS NOT NULL THEN
            RAISE EXCEPTION 'Configured credentials cannot roll back' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER aab_stewardship_setup_install_progress
BEFORE UPDATE ON public.stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_progress_v1();

CREATE POLICY setup_initial_staging_intake ON public.stewardship_sealed_credential_staging
FOR INSERT WITH CHECK (current_user='pk_stewardship_credential_'||target AND EXISTS (
    SELECT 1 FROM public.stewardship_setup_credential_install binding
    WHERE binding.request_id=stewardship_sealed_credential_staging.request_id));
CREATE POLICY setup_initial_context_intake ON public.stewardship_provider_context
FOR INSERT WITH CHECK (current_user='pk_stewardship_credential_'||target AND EXISTS (
    SELECT 1 FROM public.stewardship_setup_credential_install binding
    WHERE binding.request_id=stewardship_provider_context.request_id));
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_credential_install IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install) THEN
        RAISE EXCEPTION 'Initial credential history prevents downgrade' USING ERRCODE='23514';
    END IF;
END $$;
DROP POLICY setup_initial_context_intake ON public.stewardship_provider_context;
DROP POLICY setup_initial_staging_intake ON public.stewardship_sealed_credential_staging;
DROP TRIGGER aab_stewardship_setup_install_progress ON public.stewardship_secret_request;
DROP FUNCTION public.stewardship_setup_install_progress_v1();
DROP TRIGGER stewardship_setup_install_intake ON public.stewardship_secret_request;
DROP FUNCTION public.stewardship_setup_install_intake_v1();
DROP TRIGGER stewardship_setup_install_binding ON public.stewardship_setup_credential_install;
DROP FUNCTION public.stewardship_setup_install_binding_v1();
DROP FUNCTION public.stewardship_setup_install_live_v1(uuid);
DROP FUNCTION public.stewardship_setup_install_completed_v1(uuid);
DROP FUNCTION public.stewardship_setup_install_selected_v1(uuid,uuid,bigint,text);
DROP FUNCTION public.stewardship_setup_install_ready_live_v1(uuid);
"""

# Preserve every ordinary credential transition. Initial setup proves the
# original login and its fresh sealed intake instead of inventing a new OAuth
# authentication time at the later confirmation step.
REPLACEMENTS = (
    (
        "OR NEW.reauthenticated_at<NEW.created_at-interval '5 minutes'",
        "OR (NEW.reauthenticated_at<NEW.created_at-interval '5 minutes' "
        "AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') "
        "THEN public.stewardship_setup_install_live_v1(NEW.id) ELSE false END))",
    ),
    (
        "NEW.cleanup_reason='expired' AND OLD.expires_at>statement_timestamp()",
        "NEW.cleanup_reason='expired' AND OLD.expires_at>statement_timestamp() "
        "AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') "
        "THEN EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install "
        "WHERE request_id=NEW.id) AND NOT public.stewardship_setup_install_live_v1(NEW.id) "
        "ELSE false END)",
    ),
    (
        "OLD.state<>'awaiting_ack' OR OLD.expires_at<=statement_timestamp()",
        "OLD.state<>'awaiting_ack' OR (OLD.expires_at<=statement_timestamp() "
        "AND CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') "
        "THEN public.stewardship_setup_install_completed_v1(NEW.id) IS NULL ELSE true END)",
    ),
    (
        "NEW.acknowledged_at:=statement_timestamp();",
        "NEW.acknowledged_at:=CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') "
        "THEN coalesce(public.stewardship_setup_install_completed_v1(NEW.id),statement_timestamp()) "
        "ELSE statement_timestamp() END;",
    ),
)


def rewrite(editor, reverse=False):
    """Amend the installed frozen guard only when each exact predecessor is present."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('public.stewardship_secret_state_v2()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    for before, after in REPLACEMENTS:
        if reverse:
            before, after = after, before
        if definition.count(before) != 1:
            raise RuntimeError("Initial credential transition predecessor differs.")
        definition = definition.replace(before, after)
    editor.execute(definition, params=None)


def forward(apps, editor):
    """Install only the initial-setup-bound transition exceptions."""
    rewrite(editor)


def reverse(apps, editor):
    """Refuse to remove the rollback-retention owner from retained installations."""
    editor.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install) THEN
            RAISE EXCEPTION 'Initial credential history prevents downgrade' USING ERRCODE='23514';
        END IF;
    END $$;""")
    rewrite(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0084_setup_credential_installation")]
    operations = [
        immutable_guard_v1("stewardship_setup_credential_install"),
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunPython(forward, reverse),
    ]
