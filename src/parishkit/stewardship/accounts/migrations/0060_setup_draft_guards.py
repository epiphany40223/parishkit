"""Session-owned public drafts and transactional expiry scrubbing."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_setup_draft_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE attempt stewardship_setup_attempt%ROWTYPE; stamp timestamptz:=clock_timestamp();
        expected text[]; actual text[];
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup draft tombstones are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup drafts require ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM stewardship_setup_attempt WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup cannot be repopulated' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed') OR NEW.values<>'{}'::jsonb
           OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Setup scrubbing needs a terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration runtime
        JOIN stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '30 minutes'
        JOIN stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup draft requires its original live Admin'
            USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.values)<>'object' OR octet_length(NEW.values::text)>65536 THEN
        RAISE EXCEPTION 'Invalid public setup shape' USING ERRCODE='23514';
    END IF;
    expected=CASE NEW.step
        WHEN 'parish' THEN ARRAY['name','phone','timezone','website']
        WHEN 'branding' THEN ARRAY['bundle_id']
        WHEN 'access' THEN ARRAY['admin_addresses','ministry_addresses',
            'ministry_domains','staff_addresses','staff_domains']
        WHEN 'mail' THEN ARRAY['delegated_email','reply_to','sender']
        WHEN 'slack' THEN ARRAY['channel_id','enabled']
        WHEN 'testing' THEN ARRAY['testing_recipient'] END;
    SELECT array_agg(key ORDER BY key) INTO actual
        FROM jsonb_object_keys(NEW.values) key;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Only declared public setup fields can be staged'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_draft_admission
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_setup_draft_section
FOR EACH ROW EXECUTE FUNCTION stewardship_setup_draft_guard_v1();

CREATE FUNCTION stewardship_setup_scrub_drafts_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE stewardship_setup_draft_section SET values='{}'::jsonb,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_setup_scrub_drafts
AFTER UPDATE ON stewardship_setup_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_setup_scrub_drafts_v1();
"""

REVERSE = """
LOCK TABLE stewardship_setup_draft_section IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_setup_draft_section) THEN
        RAISE EXCEPTION 'Setup draft history prevents downgrade' USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_scrub_drafts ON stewardship_setup_attempt;
DROP FUNCTION stewardship_setup_scrub_drafts_v1();
DROP TRIGGER stewardship_setup_draft_admission ON stewardship_setup_draft_section;
DROP FUNCTION stewardship_setup_draft_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0059_setup_draft_sections")]
    operations = [
        mutable_guard_v1(
            "stewardship_setup_draft_section",
            frozen_fields=("attempt_id", "step"),
            write_once_fields=("scrubbed_at",),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
