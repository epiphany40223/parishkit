"""Original-login intake, target isolation and atomic terminal ciphertext scrub."""

from importlib import import_module

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

_context = import_module(
    "parishkit.stewardship.accounts.migrations.0053_provider_context_guards"
)
# Reuse only the frozen closed public-scope checks, not live request admission.
_checks = _context.FORWARD.split("    SELECT array_agg(key ORDER BY key)", 1)[1]
_checks = (
    "    SELECT array_agg(key ORDER BY key)"
    + _checks.split("    NEW.created_at :=", 1)[0]
)

FORWARD = (
    r"""
CREATE FUNCTION public.stewardship_setup_secret_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        stamp timestamptz:=clock_timestamp(); keys text[]; item jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup credential tombstones are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup credentials require ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup credentials cannot be repopulated'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed')
           OR NEW.ciphertext IS NOT NULL OR NEW.actor_id IS NOT NULL
           OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '30 minutes'
            AND login.authenticated_at>stamp-interval '5 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup credential requires its original freshly signed-in Admin'
            USING ERRCODE='23514';
    END IF;
    IF NEW.ciphertext IS NULL OR length(NEW.ciphertext)=0
       OR octet_length(NEW.ciphertext)>2097152
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid sealed setup credential shape' USING ERRCODE='23514';
    END IF;
"""
    + _checks
    + r"""
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_secret_admission
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_setup_sealed_credential
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_secret_guard_v1();

CREATE FUNCTION public.stewardship_setup_scrub_secrets_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_sealed_credential SET ciphertext=NULL,
            settings='{}'::jsonb,
            scrubbed_at=clock_timestamp(), actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_scrub_secrets
AFTER UPDATE ON public.stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_secrets_v1();

ALTER TABLE public.stewardship_setup_sealed_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.stewardship_setup_sealed_credential FORCE ROW LEVEL SECURITY;
CREATE POLICY setup_secret_read ON public.stewardship_setup_sealed_credential
FOR SELECT USING (current_user IN
    ('pk_stewardship_web','pk_stewardship_scheduler','pk_stewardship_migration')
    OR current_user='pk_stewardship_credential_' || target);
CREATE POLICY setup_secret_intake ON public.stewardship_setup_sealed_credential
FOR INSERT WITH CHECK (current_user='pk_stewardship_web');
CREATE POLICY setup_secret_change ON public.stewardship_setup_sealed_credential
FOR UPDATE USING (current_user IN ('pk_stewardship_web','pk_stewardship_scheduler'))
WITH CHECK (current_user IN ('pk_stewardship_web','pk_stewardship_scheduler'));

CREATE FUNCTION public.stewardship_setup_secret_audit_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'setup_credential_staged'
            ELSE 'setup_credential_scrubbed' END,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',
            CASE WHEN NEW.scrubbed_at IS NULL THEN 'changed' ELSE 'cancelled' END));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_secret_audit
AFTER INSERT OR UPDATE ON public.stewardship_setup_sealed_credential
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_secret_audit_v1();
"""
)

BACKWARD = """
LOCK TABLE public.stewardship_setup_sealed_credential IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential) THEN
        RAISE EXCEPTION 'Setup credential history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_secret_audit
    ON public.stewardship_setup_sealed_credential;
DROP FUNCTION public.stewardship_setup_secret_audit_v1();
DROP POLICY setup_secret_change ON public.stewardship_setup_sealed_credential;
DROP POLICY setup_secret_intake ON public.stewardship_setup_sealed_credential;
DROP POLICY setup_secret_read ON public.stewardship_setup_sealed_credential;
ALTER TABLE public.stewardship_setup_sealed_credential DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.stewardship_setup_sealed_credential NO FORCE ROW LEVEL SECURITY;
DROP TRIGGER stewardship_setup_scrub_secrets ON public.stewardship_setup_attempt;
DROP FUNCTION public.stewardship_setup_scrub_secrets_v1();
DROP TRIGGER stewardship_setup_secret_admission
    ON public.stewardship_setup_sealed_credential;
DROP FUNCTION public.stewardship_setup_secret_guard_v1();
"""

_bootstrap = _context._bootstrap.replace(_context._before, _context._after)
_before = "'stewardship_provider_context')) THEN"
_after = "'stewardship_provider_context', 'stewardship_setup_sealed_credential')) THEN"
if _bootstrap.count(_before) != 1:
    raise RuntimeError("Frozen bootstrap row-security admission is unavailable.")


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0066_setup_sealed_credentials"),
        ("stewardship_audit", "0012_setup_credential_events"),
    ]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_sealed_credential",
            frozen_fields=("attempt_id", "target"),
            write_once_fields=("scrubbed_at",),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunSQL(_bootstrap.replace(_before, _after), _bootstrap),
    ]
