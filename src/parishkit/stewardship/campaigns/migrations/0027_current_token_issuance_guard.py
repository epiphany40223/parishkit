"""New tokens cannot enter stale/cancelled generations through direct SQL writes."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0026_token_activation_guards")]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_token_issuance_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE generation stewardship_family_token_generation%ROWTYPE;
    current_epoch uuid;
BEGIN
    SELECT family_link_epoch INTO current_epoch
        FROM stewardship_credential_deployment FOR SHARE;
    SELECT * INTO generation FROM stewardship_family_token_generation
        WHERE id=NEW.generation_id FOR SHARE;
    IF generation.id IS NULL OR generation.state NOT IN('building','active')
       OR generation.credential_epoch IS DISTINCT FROM current_epoch
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign
                     WHERE id=NEW.family_id AND portal_eligible FOR SHARE) THEN
        RAISE EXCEPTION 'Token issuance requires a current eligible generation'
            USING ERRCODE='23514';
    END IF;
    IF generation.state='active' AND NOT EXISTS(
        SELECT 1 FROM stewardship_campaign c
        JOIN stewardship_system_configuration s ON s.current_campaign_id=c.id
        WHERE c.id=generation.campaign_id AND c.active_token_generation_id=generation.id
          AND c.state IN('scheduled','active') AND s.mode='production'
          AND NOT s.restore_review_required
        FOR SHARE OF c,s
    ) THEN
        RAISE EXCEPTION 'Live token issuance is not admitted'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_issuance_v1 BEFORE INSERT ON stewardship_family_token
FOR EACH ROW EXECUTE FUNCTION stewardship_token_issuance_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_token_issuance_v1 ON stewardship_family_token;
DROP FUNCTION stewardship_token_issuance_v1();
""",
        )
    ]
