"""Bind closed provider scope at intake; isolated installers only read their target."""

from importlib import import_module

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = r"""
CREATE FUNCTION public.stewardship_provider_context_insert_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt public.stewardship_secret_request%ROWTYPE;
        keys text[];
        item jsonb;
BEGIN
    SELECT * INTO receipt FROM public.stewardship_secret_request
        WHERE id=NEW.request_id FOR SHARE;
    IF NOT FOUND OR receipt.target IS DISTINCT FROM NEW.target
       OR receipt.state <> 'staged' OR receipt.expires_at <= clock_timestamp()
       OR jsonb_array_length(receipt.required_consumers)=0
       OR NEW.actor_id IS DISTINCT FROM receipt.requested_by_id
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;
    -- Same transaction as intake: context cannot be attached after another
    -- process could have claimed a request which originally had no context.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_secret_request
        WHERE id=NEW.request_id
          AND xmin::text::numeric=
              mod(pg_current_xact_id()::text::numeric,4294967296)) THEN
        RAISE EXCEPTION 'Provider context requires original intake transaction'
            USING ERRCODE='23514';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO keys
        FROM jsonb_object_keys(NEW.settings) key;
    IF NEW.target='parishsoft' THEN
        IF keys IS DISTINCT FROM ARRAY['organization_id']
           OR jsonb_typeof(NEW.settings->'organization_id') <> 'number'
           OR (NEW.settings->>'organization_id') !~ '^[1-9][0-9]{0,9}$'
           OR (NEW.settings->>'organization_id')::numeric >= 2147483648 THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='slack' THEN
        IF keys IS DISTINCT FROM ARRAY['channel_id']
           OR jsonb_typeof(NEW.settings->'channel_id') <> 'string'
           OR (NEW.settings->>'channel_id') !~ '^[CG][A-Z0-9]{1,63}$' THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='google_workspace' THEN
        IF keys IS DISTINCT FROM
            ARRAY['delegated_email','recipient','reply_to','sender'] THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT value FROM jsonb_each(NEW.settings) LOOP
            IF jsonb_typeof(item) <> 'string' OR length(item#>>'{}')>254
               OR (item#>>'{}') <> lower(item#>>'{}')
               OR (item#>>'{}') ~ '[[:space:][:cntrl:]]'
               OR (item#>>'{}') !~ '^[^@]+@[^@]+$' THEN
                RAISE EXCEPTION 'Invalid provider validation context'
                    USING ERRCODE='23514';
            END IF;
        END LOOP;
    ELSE
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_provider_context_insert_v1
BEFORE INSERT ON public.stewardship_provider_context
FOR EACH ROW EXECUTE FUNCTION public.stewardship_provider_context_insert_v1();
ALTER TABLE public.stewardship_provider_context ENABLE ROW LEVEL SECURITY;
CREATE POLICY stewardship_provider_context_read ON public.stewardship_provider_context
FOR SELECT USING (current_user='pk_stewardship_web'
    OR current_user='pk_stewardship_credential_' || target
    OR current_user='pk_stewardship_migration');
CREATE POLICY stewardship_provider_context_intake ON public.stewardship_provider_context
FOR INSERT WITH CHECK (current_user='pk_stewardship_web');
"""

BACKWARD = """
LOCK TABLE public.stewardship_provider_context IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_provider_context) THEN
        RAISE EXCEPTION 'Provider validation history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP POLICY stewardship_provider_context_intake ON public.stewardship_provider_context;
DROP POLICY stewardship_provider_context_read ON public.stewardship_provider_context;
ALTER TABLE public.stewardship_provider_context DISABLE ROW LEVEL SECURITY;
DROP TRIGGER stewardship_provider_context_insert_v1
    ON public.stewardship_provider_context;
DROP FUNCTION public.stewardship_provider_context_insert_v1();
"""

_previous = import_module(
    "parishkit.stewardship.accounts.migrations.0051_handoff_public_guards"
)
_bootstrap = _previous._bootstrap.replace(_previous._before, _previous._after)
_before = "'stewardship_public_credential_handoff')) THEN"
_after = (
    "'stewardship_public_credential_handoff', 'stewardship_provider_context')) THEN"
)
if _bootstrap.count(_before) != 1:
    raise RuntimeError("Frozen bootstrap row-security admission is unavailable.")


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0052_providervalidationcontext")]
    operations = [
        immutable_guard_v1("stewardship_provider_context"),
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunSQL(_bootstrap.replace(_before, _after), _bootstrap),
    ]
