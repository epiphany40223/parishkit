"""Reject sealed intents that cannot enter the isolated installation protocol."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0036_bootstrap_empty_database")]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_sealed_intake_admission_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME='stewardship_secret_request' THEN
        IF NEW.required_consumers<>'[]'::jsonb
           AND NEW.reauthenticated_at<statement_timestamp()-interval '5 minutes' THEN
            RAISE EXCEPTION 'Sealed intake requires fresh authentication'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS(SELECT 1 FROM stewardship_secret_request
            WHERE id=NEW.request_id AND required_consumers<>'[]'::jsonb) THEN
            RAISE EXCEPTION 'Sealed intake requires installer consumers'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_sealed_intake_admission_v1
BEFORE INSERT ON stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION stewardship_sealed_intake_admission_v1();
CREATE TRIGGER stewardship_sealed_intake_admission_v1
BEFORE INSERT ON stewardship_sealed_credential_staging
FOR EACH ROW EXECUTE FUNCTION stewardship_sealed_intake_admission_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_sealed_intake_admission_v1
ON stewardship_secret_request;
DROP TRIGGER stewardship_sealed_intake_admission_v1
ON stewardship_sealed_credential_staging;
DROP FUNCTION stewardship_sealed_intake_admission_v1();
""",
        )
    ]
