"""Public discovery cannot grant web decryption or cross-target publication."""

from importlib import import_module

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION public.stewardship_public_handoff_insert_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user <> session_user
       OR current_user <> 'pk_stewardship_credential_' || NEW.target
       OR octet_length(NEW.public_key) <> 32
       OR NEW.actor_id IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid public handoff publication' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_public_handoff_insert_v1
BEFORE INSERT ON public.stewardship_public_credential_handoff
FOR EACH ROW EXECUTE FUNCTION public.stewardship_public_handoff_insert_v1();

ALTER TABLE public.stewardship_public_credential_handoff ENABLE ROW LEVEL SECURITY;
CREATE POLICY stewardship_public_handoff_read
ON public.stewardship_public_credential_handoff FOR SELECT
USING (current_user='pk_stewardship_web'
       OR current_user='pk_stewardship_credential_' || target);
CREATE POLICY stewardship_public_handoff_publish
ON public.stewardship_public_credential_handoff FOR INSERT
WITH CHECK (current_user='pk_stewardship_credential_' || target);
CREATE POLICY stewardship_public_handoff_initial_read
ON public.stewardship_public_credential_handoff FOR SELECT
USING (current_user='pk_stewardship_migration');
"""

BACKWARD = """
LOCK TABLE public.stewardship_public_credential_handoff IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_public_credential_handoff) THEN
        RAISE EXCEPTION 'Published handoff history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP POLICY stewardship_public_handoff_initial_read
    ON public.stewardship_public_credential_handoff;
DROP POLICY stewardship_public_handoff_publish
    ON public.stewardship_public_credential_handoff;
DROP POLICY stewardship_public_handoff_read
    ON public.stewardship_public_credential_handoff;
ALTER TABLE public.stewardship_public_credential_handoff DISABLE ROW LEVEL SECURITY;
DROP TRIGGER stewardship_public_handoff_insert_v1
    ON public.stewardship_public_credential_handoff;
DROP FUNCTION public.stewardship_public_handoff_insert_v1();
"""

_bootstrap = import_module(
    "parishkit.stewardship.accounts.migrations.0045_pristine_source_bootstrap"
).FORWARD
_before = "'stewardship_credential_consumer_ack')) THEN"
_after = (
    "'stewardship_credential_consumer_ack', "
    "'stewardship_public_credential_handoff')) THEN"
)
if _bootstrap.count(_before) != 1:
    raise RuntimeError("Frozen bootstrap row-security admission is unavailable.")


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0050_credential_handoff_public")]
    operations = [
        immutable_guard_v1("stewardship_public_credential_handoff"),
        migrations.RunSQL(FORWARD, BACKWARD),
        # Reviewed visibility is not an exemption: published rows still make
        # a deployment nonempty and prevent bootstrap from adopting it.
        migrations.RunSQL(_bootstrap.replace(_before, _after), _bootstrap),
    ]
