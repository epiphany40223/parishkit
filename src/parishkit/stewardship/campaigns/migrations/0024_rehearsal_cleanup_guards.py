"""Invalidated testing detail may be scrubbed without releasing reservations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0023_token_manifest_guards")]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_rehearsal_delete_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch
                  WHERE id=OLD.epoch_id AND state='invalidated') THEN
        RAISE EXCEPTION 'Rehearsal details require invalidation before cleanup'
            USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER stewardship_rehearsal_delete_v1 BEFORE DELETE
ON stewardship_rehearsal_credential FOR EACH ROW
EXECUTE FUNCTION stewardship_rehearsal_delete_v1();
CREATE TRIGGER stewardship_rehearsal_delete_v1 BEFORE DELETE
ON stewardship_rehearsal_code_mac FOR EACH ROW
EXECUTE FUNCTION stewardship_rehearsal_delete_v1();
CREATE FUNCTION stewardship_rehearsal_code_mac_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF TG_OP='DELETE' AND EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch
            WHERE id=OLD.epoch_id AND state='invalidated') THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'Rehearsal MACs cannot change before invalidated cleanup'
        USING ERRCODE = '23514';
END $$;
CREATE TRIGGER stewardship_rehearsal_code_mac_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_rehearsal_code_mac
FOR EACH ROW EXECUTE FUNCTION stewardship_rehearsal_code_mac_immutable_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_rehearsal_code_mac_immutable_guard_v1
ON stewardship_rehearsal_code_mac;
DROP FUNCTION stewardship_rehearsal_code_mac_immutable_v1();
DROP TRIGGER stewardship_rehearsal_delete_v1 ON stewardship_rehearsal_credential;
DROP TRIGGER stewardship_rehearsal_delete_v1 ON stewardship_rehearsal_code_mac;
DROP FUNCTION stewardship_rehearsal_delete_v1();
""",
        )
    ]
