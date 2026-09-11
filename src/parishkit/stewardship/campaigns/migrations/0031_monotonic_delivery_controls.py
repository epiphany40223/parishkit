"""Keep pause/resume history ordered, including valid pre-start production pauses."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0030_explicit_rehearsal_retention")]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_control_time_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- Runs after the existing guard has serialized runtime and campaign rows.
    IF NEW.action IN('pause','resume') AND EXISTS(
        SELECT 1 FROM stewardship_campaign WHERE id=NEW.campaign_id
        AND NEW.occurred_at<greatest(paused_at,resumed_at)) THEN
        RAISE EXCEPTION 'Delivery control time cannot regress' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_control_time_v1
BEFORE INSERT ON stewardship_campaign_control
FOR EACH ROW EXECUTE FUNCTION stewardship_control_time_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_control_time_v1 ON stewardship_campaign_control;
DROP FUNCTION stewardship_control_time_v1();
""",
        )
    ]
