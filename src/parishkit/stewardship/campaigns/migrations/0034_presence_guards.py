"""Presence is rate-bounded observation, never session-renewal authority."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION public.stewardship_family_presence_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE instant timestamptz := clock_timestamp();
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.presence_at IS NOT NULL OR NEW.presence_section<>'' THEN
            RAISE EXCEPTION 'Presence starts after session authentication'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.presence_at, NEW.presence_section) IS NOT DISTINCT FROM
       (OLD.presence_at, OLD.presence_section) THEN
        RETURN NEW;
    END IF;
    IF NEW.presence_at IS NULL OR NEW.revoked_at IS NOT NULL
       OR (NEW.last_activity_at, NEW.last_keepalive_at, NEW.expires_at)
          IS DISTINCT FROM
          (OLD.last_activity_at, OLD.last_keepalive_at, OLD.expires_at)
       OR instant >= OLD.expires_at
       OR instant >= OLD.last_activity_at + interval '30 minutes'
       OR (OLD.presence_at IS NOT NULL
           AND instant < OLD.presence_at + interval '30 seconds') THEN
        RAISE EXCEPTION 'Presence cannot renew or outlive its Family session'
            USING ERRCODE='23514';
    END IF;
    NEW.presence_at := instant;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_family_presence_v1
BEFORE INSERT OR UPDATE ON public.stewardship_family_session
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_presence_v1();
"""
REVERSE = """
DROP TRIGGER stewardship_family_presence_v1 ON public.stewardship_family_session;
DROP FUNCTION public.stewardship_family_presence_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0033_family_presence")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
