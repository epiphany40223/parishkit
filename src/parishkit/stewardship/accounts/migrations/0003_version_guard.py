"""Guard every SQL session update, not only calls to the mutation helper."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0002_session_integrity")]
    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION stewardship_guard_session_version()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.id IS DISTINCT FROM OLD.id OR
                       NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'Record identity and creation are immutable'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                        RAISE EXCEPTION 'Every update must advance the record version'
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.updated_at := statement_timestamp();
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_session_version_guard
                BEFORE UPDATE ON stewardship_portal_session
                FOR EACH ROW EXECUTE FUNCTION stewardship_guard_session_version();
            """,
            reverse_sql="""
                DROP TRIGGER stewardship_session_version_guard
                    ON stewardship_portal_session;
                DROP FUNCTION stewardship_guard_session_version();
            """,
        )
    ]
