"""Initial bootstrap cannot adopt unrelated data while using a narrow SQL role."""

from django.db import migrations

FORWARD = """
-- The offline schema owner already controls these tables, but FORCE RLS would
-- otherwise hide rows even from its empty-database trigger. This SELECT-only
-- policy adds no online privilege or runtime ability to assume that identity.
CREATE POLICY stewardship_migration_initial_read ON public.stewardship_secret_request
FOR SELECT USING (current_user='pk_stewardship_migration');
CREATE POLICY stewardship_migration_initial_read
ON public.stewardship_sealed_credential_staging
FOR SELECT USING (current_user='pk_stewardship_migration');
CREATE POLICY stewardship_migration_initial_read
ON public.stewardship_credential_consumer_ack
FOR SELECT USING (current_user='pk_stewardship_migration');
CREATE FUNCTION public.stewardship_bootstrap_empty_database() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE relation record; occupied boolean;
BEGIN
    IF NEW.validation_schema <> 'bootstrap-policy-v1'
       OR NEW.predecessor_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- Framework migration/permission metadata and the guarded initial download
    -- policy are seeded by migrations, not evidence of an existing campaign.
    -- All current and future application tables must otherwise be empty.
    FOR relation IN
        SELECT n.nspname, c.relname, c.relrowsecurity FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','f')
          AND NOT (n.nspname='public' AND c.relname IN (
              'django_migrations','django_content_type','auth_permission',
              'stewardship_download_policy'))
        ORDER BY n.nspname, c.relname
    LOOP
        IF relation.relrowsecurity AND NOT (relation.nspname='public'
            AND relation.relname IN ('stewardship_secret_request',
                'stewardship_sealed_credential_staging',
                'stewardship_credential_consumer_ack')) THEN
            RAISE EXCEPTION 'Initial bootstrap requires reviewed row-security admission'
                USING ERRCODE='23514';
        END IF;
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I)',
                       relation.nspname, relation.relname) INTO occupied;
        IF occupied THEN
            RAISE EXCEPTION 'Initial bootstrap requires an empty application database'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
-- This definer can run only as a trigger. No application role receives EXECUTE,
-- a boolean probing endpoint or broader SELECT access to prove emptiness.
REVOKE ALL ON FUNCTION public.stewardship_bootstrap_empty_database() FROM PUBLIC;
CREATE TRIGGER stewardship_bootstrap_empty_database
BEFORE INSERT ON public.stewardship_configuration_version
FOR EACH ROW EXECUTE FUNCTION public.stewardship_bootstrap_empty_database();
"""

REVERSE = """
LOCK TABLE public.stewardship_configuration_version IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_configuration_version
               WHERE validation_schema='bootstrap-policy-v1') THEN
        RAISE EXCEPTION 'Bootstrap history prevents dropping initial admission'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_bootstrap_empty_database
    ON public.stewardship_configuration_version;
DROP FUNCTION public.stewardship_bootstrap_empty_database();
DROP POLICY stewardship_migration_initial_read ON public.stewardship_secret_request;
DROP POLICY stewardship_migration_initial_read
    ON public.stewardship_sealed_credential_staging;
DROP POLICY stewardship_migration_initial_read
    ON public.stewardship_credential_consumer_ack;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0035_bootstrap_recovery_schema"),
    ]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
