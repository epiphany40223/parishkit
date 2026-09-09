"""Attribute new audit rows without rewriting or guessing legacy ownership.

The column defaults preserve old append-only rows as deployment history. The
INSERT trigger binds new rows to immutable configuration projections, including
events emitted by earlier migration-defined checkpoint/activation triggers.
Schema and trigger installation share one atomic migration.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0015_secret_request_guards"),
        ("stewardship_audit", "0005_alter_auditevent_subject_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="campaign_reference",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="ownership_scope",
            field=models.CharField(db_default="deployment", max_length=16),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="parish",
            field=models.ForeignKey(
                blank=True,
                db_default=None,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="audit_events",
                to="stewardship_accounts.parish",
            ),
        ),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("ownership_scope", "parish"), ("parish__isnull", False)),
                    models.Q(
                        ("campaign_reference__isnull", True),
                        ("ownership_scope", "deployment"),
                        ("parish__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="audit_ownership_shape",
            ),
        ),
        migrations.RunSQL(
            sql="""
    CREATE FUNCTION stewardship_audit_ownership_v1()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        configuration uuid;
        owner uuid;
    BEGIN
        IF left(NEW.event_type, 15) = 'config_request_' THEN
            SELECT base_id INTO configuration FROM stewardship_config_request
            WHERE id = NEW.subject_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit configuration request context is missing'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.event_type = 'configuration_activated' THEN
            SELECT configuration_id INTO configuration
            FROM stewardship_config_activation WHERE id = NEW.subject_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit activation context is missing'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            -- This is attribution, not authorization or a runtime readiness check.
            -- A concurrent activation may be visible on the next statement;
            -- every selected projection itself is immutable.
            SELECT active_configuration_id INTO configuration
            FROM stewardship_system_configuration;
        END IF;
        IF configuration IS NOT NULL THEN
            SELECT id INTO owner FROM stewardship_parish
            WHERE configuration_id = configuration;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit Parish projection is missing'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.ownership_scope IS NULL
           OR NEW.ownership_scope NOT IN ('deployment', 'parish')
           OR (NEW.parish_id IS NOT NULL
               AND NEW.parish_id IS DISTINCT FROM owner)
           OR (NEW.ownership_scope = 'parish' AND owner IS NULL) THEN
            RAISE EXCEPTION 'Invalid audit ownership attribution'
                USING ERRCODE = '23514';
        END IF;
        NEW.parish_id := owner;
        NEW.ownership_scope := CASE WHEN owner IS NULL THEN 'deployment'
                                   ELSE 'parish' END;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_audit_ownership_v1
    BEFORE INSERT ON stewardship_audit_event
    FOR EACH ROW EXECUTE FUNCTION stewardship_audit_ownership_v1();
            """,
            reverse_sql="""
    LOCK TABLE stewardship_audit_event IN ACCESS EXCLUSIVE MODE;
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM stewardship_audit_event
                   WHERE ownership_scope = 'parish') THEN
            RAISE EXCEPTION 'Parish audit history prevents this schema downgrade'
                USING ERRCODE = '23514';
        END IF;
    END;
    $$;
    DROP TRIGGER stewardship_audit_ownership_v1 ON stewardship_audit_event;
    DROP FUNCTION stewardship_audit_ownership_v1();
            """,
        ),
    ]
