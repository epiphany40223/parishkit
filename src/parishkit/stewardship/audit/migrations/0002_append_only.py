"""Enforce append-only audit history even when callers bypass the ORM.

TRUNCATE is intentionally not covered: migration/test database owners can reset
disposable databases. Runtime database grants must not include TRUNCATE; the
least-privilege runtime role installation is owned by OPS-02/OPS-04.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0001_initial")]
    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION stewardship_reject_audit_mutation()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'Historical audit records are append-only';
                END;
                $$;
                CREATE TRIGGER stewardship_audit_append_only
                BEFORE UPDATE OR DELETE ON stewardship_audit_event
                FOR EACH ROW EXECUTE FUNCTION stewardship_reject_audit_mutation();
            """,
            reverse_sql="""
                DROP TRIGGER stewardship_audit_append_only ON stewardship_audit_event;
                DROP FUNCTION stewardship_reject_audit_mutation();
            """,
        )
    ]
