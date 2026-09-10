"""Coordinate download slots across processes without expiring live ownership."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0003_phase1a_runtime")]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE TABLE stewardship_download_policy (
    id integer PRIMARY KEY CHECK (id = 1),
    capacity integer NOT NULL CHECK (capacity BETWEEN 1 AND 32),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
INSERT INTO stewardship_download_policy(id, capacity) VALUES (1, 4);
CREATE FUNCTION stewardship_download_budget_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE slot integer;
BEGIN
    IF TG_OP = 'DELETE' OR NEW.id <> OLD.id OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Download budget requires a versioned operator update'
            USING ERRCODE = '23514';
    END IF;
    -- The row lock serializes new slot claims with resizing. Session-owned
    -- slots cannot be taken over because a timer or heartbeat expired.
    IF EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory'
        AND pid=pg_backend_pid() AND classid=736222 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'A download-owning session cannot resize capacity'
            USING ERRCODE = '23514';
    END IF;
    FOR slot IN 0..31 LOOP
        IF NOT pg_try_advisory_xact_lock(736222, slot) THEN
            RAISE EXCEPTION 'Active downloads prevent capacity changes'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_download_budget_v1 BEFORE UPDATE OR DELETE
ON stewardship_download_policy FOR EACH ROW
EXECUTE FUNCTION stewardship_download_budget_v1();
""",
            reverse_sql="""
LOCK TABLE stewardship_download_policy IN ACCESS EXCLUSIVE MODE;
DO $$ DECLARE slot integer; BEGIN
    IF EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory'
        AND pid=pg_backend_pid() AND classid=736222 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'A download-owning session cannot downgrade capacity';
    END IF;
    FOR slot IN 0..31 LOOP
        IF NOT pg_try_advisory_xact_lock(736222, slot) THEN
            RAISE EXCEPTION 'Active downloads prevent schema downgrade';
        END IF;
    END LOOP;
END $$;
DROP TABLE stewardship_download_policy;
DROP FUNCTION stewardship_download_budget_v1();
""",
        )
    ]
