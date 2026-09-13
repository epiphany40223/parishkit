"""A setup abort can restore only its exact, never-applied bootstrap successor.

No runtime grants or completion owner are introduced here. Setup activation
remains closed until its source/Family/marker transaction is implemented.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

# ruff: noqa: E501
_CAMPAIGN_JOURNAL = """EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b
                            JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id WHERE i.request_id=NEW.request_id)"""
_BOTH_JOURNALS = (
    "("
    + _CAMPAIGN_JOURNAL
    + """ OR EXISTS (
    SELECT 1 FROM public.stewardship_setup_config_abort b
    JOIN public.stewardship_setup_config_intent i ON i.id=b.intent_id
    WHERE i.request_id=NEW.request_id))"""
)


def _checkpoint(editor, before, after):
    """Extend one retained predicate, keeping every existing checkpoint fence."""
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('public.stewardship_request_checkpoint_v2()'::regprocedure)"
        )
        original = cursor.fetchone()[0]
    if original.count(before) != 1:
        raise RuntimeError("Setup abort checkpoint predecessor is inconsistent.")
    editor.execute(original.replace(before, after), params=None)


def forward_checkpoint(apps, editor):
    """Permit a failed selected candidate only when its exact abort is retained."""
    _checkpoint(editor, _CAMPAIGN_JOURNAL, _BOTH_JOURNALS)


def reverse_checkpoint(apps, editor):
    """Do not remove the only recovery path for a retained setup decision."""
    editor.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.stewardship_setup_config_intent) THEN
            RAISE EXCEPTION 'Setup finalization history prevents downgrade'
                USING ERRCODE='23514';
        END IF;
    END $$;""")
    _checkpoint(editor, _BOTH_JOURNALS, _CAMPAIGN_JOURNAL)


FORWARD = """
CREATE FUNCTION public.stewardship_setup_config_intent_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE a public.stewardship_setup_attempt%ROWTYPE;
    q public.stewardship_config_request%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup intent requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO r FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO a FROM public.stewardship_setup_attempt WHERE id=NEW.attempt_id FOR UPDATE;
    SELECT * INTO q FROM public.stewardship_config_request WHERE id=NEW.request_id FOR UPDATE;
    IF a.id IS NULL OR q.id IS NULL OR a.state<>'frozen'
        OR a.version IS DISTINCT FROM NEW.attempt_version
        OR a.owner_id IS DISTINCT FROM NEW.actor_id OR a.owner_id IS DISTINCT FROM q.actor_id
        OR q.authority<>'admin' OR q.request_schema<>'initial-setup-patch-v7'
        OR q.base_id IS DISTINCT FROM a.base_id OR r.active_configuration_id IS DISTINCT FROM a.base_id
        OR r.mode<>'testing' OR r.restore_review_required OR r.current_campaign_id IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_configuration_version
            WHERE id=a.base_id AND validation_schema='bootstrap-policy-v1')
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1) IS DISTINCT FROM 'staged'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session s
            JOIN public.stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
            JOIN public.stewardship_address_rule p ON p.configuration_id=a.base_id
                AND p.email=u.email AND p.roles @> '["administrator"]'::jsonb
            WHERE s.id=a.session_id AND s.principal_id=a.owner_id AND s.revoked_at IS NULL
                AND s.expires_at>clock_timestamp()
                AND s.last_activity_at>clock_timestamp()-interval '30 minutes') THEN
        RAISE EXCEPTION 'Setup intent requires its frozen original Admin attempt'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_config_intent_admission
BEFORE INSERT ON public.stewardship_setup_config_intent
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_intent_v1();

CREATE FUNCTION public.stewardship_setup_request_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.request_schema='initial-setup-patch-v7' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_config_intent WHERE request_id=NEW.id) THEN
        RAISE EXCEPTION 'Setup request requires its atomic original-attempt binding'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_setup_request_binding
AFTER INSERT ON public.stewardship_config_request DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_request_binding_v1();

CREATE FUNCTION public.stewardship_setup_config_abort_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE a public.stewardship_setup_attempt%ROWTYPE;
    q public.stewardship_config_request%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup abort requires configuration serialization' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT s.* INTO a FROM public.stewardship_setup_attempt s
        JOIN public.stewardship_setup_config_intent i ON i.attempt_id=s.id
        WHERE i.id=NEW.intent_id;
    SELECT request.* INTO q FROM public.stewardship_config_request request
        JOIN public.stewardship_setup_config_intent i ON i.request_id=request.id
        WHERE i.id=NEW.intent_id FOR UPDATE OF request;
    IF a.id IS NULL OR q.id IS NULL OR a.state<>'expired'
        OR NEW.reason IS DISTINCT FROM a.expiry_reason
        OR (NEW.actor_id IS NOT NULL AND NEW.actor_id IS DISTINCT FROM a.owner_id)
        OR (NEW.reason='cancelled' AND NEW.actor_id IS DISTINCT FROM a.owner_id)
        OR r.active_configuration_id IS DISTINCT FROM a.base_id
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_configuration_version v
            WHERE v.id=q.candidate_version_id AND v.digest=q.candidate_digest
                AND v.predecessor_id=q.base_id)
        OR EXISTS (SELECT 1 FROM public.stewardship_config_activation WHERE request_id=q.id)
        OR coalesce((SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1)
            NOT IN ('validating','prepared','yaml_activated'),true) THEN
        RAISE EXCEPTION 'Setup abort requires its expired never-applied candidate'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_config_abort_admission
BEFORE INSERT ON public.stewardship_setup_config_abort
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_abort_v1();

CREATE FUNCTION public.stewardship_setup_activation_owner_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_config_request
        WHERE id=NEW.request_id AND request_schema='initial-setup-patch-v7') THEN
        RAISE EXCEPTION 'Setup activation requires its atomic configured-marker owner'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER aab_stewardship_setup_activation_owner
BEFORE INSERT ON public.stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_activation_owner_v1();
"""

REVERSE = """
DROP TRIGGER aab_stewardship_setup_activation_owner ON public.stewardship_config_activation;
DROP FUNCTION public.stewardship_setup_activation_owner_v1();
DROP TRIGGER stewardship_setup_config_abort_admission ON public.stewardship_setup_config_abort;
DROP FUNCTION public.stewardship_setup_config_abort_v1();
DROP TRIGGER stewardship_setup_request_binding ON public.stewardship_config_request;
DROP FUNCTION public.stewardship_setup_request_binding_v1();
DROP TRIGGER stewardship_setup_config_intent_admission ON public.stewardship_setup_config_intent;
DROP FUNCTION public.stewardship_setup_config_intent_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0063_setup_configuration_journal"),
        ("stewardship_campaigns", "0036_family_presence_idle"),
    ]
    operations = [
        immutable_guard_v1("stewardship_setup_config_intent"),
        immutable_guard_v1("stewardship_setup_config_abort"),
        migrations.RunSQL(FORWARD, REVERSE),
        migrations.RunPython(forward_checkpoint, reverse_checkpoint),
    ]
