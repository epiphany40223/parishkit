"""Sealed requests cannot omit a service that mounts the replacement credential."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0031_credential_installer_guards")]

    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_complete_consumers_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.required_consumers<>'[]'::jsonb
       AND NOT NEW.required_consumers @>
           stewardship_credential_consumers_v1(NEW.target) THEN
        RAISE EXCEPTION 'Credential consumer inventory must be complete'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_complete_consumers_v1
BEFORE INSERT ON stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION stewardship_complete_consumers_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_complete_consumers_v1 ON stewardship_secret_request;
DROP FUNCTION stewardship_complete_consumers_v1();
""",
        ),
    ]
