"""Version nightly parish-local refresh settings without reinterpreting history."""

from django.db import migrations, models

RECOVERY_SCHEMAS = [
    "operator-recovery-patch-v1",
    "operator-recovery-patch-v2",
    "operator-recovery-bootstrap-v1",
    "operator-recovery-ministry-v4",
    "operator-recovery-content-v5",
    "operator-recovery-cadence-v8",
]


def predicates(editor, *, reverse=False):
    """Extend the installed exact-projection guards, preserving all other clauses."""
    for name in ("policy", "campaign", "ministry", "content"):
        for purpose in ("projection", "complete"):
            signature = f"public.stewardship_{name}_{purpose}_v1()"
            with editor.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_functiondef(%s::regprocedure)", [signature]
                )
                definition = cursor.fetchone()[0]
            if name == "content":
                if purpose == "projection":
                    before = "v.validation_schema='campaign-content-v5'"
                    after = (
                        "v.validation_schema IN "
                        "('campaign-content-v5', 'source-cadence-v8')"
                    )
                else:
                    before = "NEW.validation_schema <> 'campaign-content-v5'"
                    after = (
                        "NEW.validation_schema NOT IN "
                        "('campaign-content-v5', 'source-cadence-v8')"
                    )
            else:
                before = "'campaign-content-v5')"
                after = "'campaign-content-v5', 'source-cadence-v8')"
            if reverse:
                before, after = after, before
            if definition.count(before) != 1:
                raise RuntimeError("Source cadence projection predecessor differs.")
            editor.execute(definition.replace(before, after), params=None)


def forward(apps, editor):
    """Admit the new setting under every existing normalized projection family."""
    predicates(editor)


def backward(apps, editor):
    """Retained cadence configurations or intents prevent weakening their schema."""
    editor.execute("""
LOCK TABLE public.stewardship_configuration_version, public.stewardship_config_request
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_configuration_version
        WHERE validation_schema='source-cadence-v8')
        OR EXISTS (SELECT 1 FROM public.stewardship_config_request
            WHERE request_schema IN (
            'source-cadence-patch-v8','operator-recovery-cadence-v8',
            'integration-credential-cadence-v8')) THEN
        RAISE EXCEPTION 'Cadence history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
    """)
    predicates(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0095_setup_completion_consumer_freshness")
    ]
    operations = [
        migrations.RemoveConstraint(
            "appliedconfigurationversion", "configuration_validation_schema"
        ),
        migrations.RemoveConstraint(
            "configurationchangerequest", "config_request_actor"
        ),
        migrations.RemoveConstraint(
            "configurationchangerequest", "config_request_schema"
        ),
        migrations.AddConstraint(
            "appliedconfigurationversion",
            models.CheckConstraint(
                condition=models.Q(
                    validation_schema__in=[
                        "parish-integrations-v1",
                        "foundation-policy-v2",
                        "campaign-foundation-v3",
                        "bootstrap-policy-v1",
                        "ministry-activity-v4",
                        "campaign-content-v5",
                        "source-cadence-v8",
                    ]
                ),
                name="configuration_validation_schema",
            ),
        ),
        migrations.AddConstraint(
            "configurationchangerequest",
            models.CheckConstraint(
                condition=models.Q(
                    request_schema__in=[
                        "parish-integrations-patch-v1",
                        "foundation-policy-patch-v2",
                        "operator-recovery-patch-v1",
                        "operator-recovery-patch-v2",
                        "operator-recovery-bootstrap-v1",
                        "campaign-foundation-patch-v3",
                        "ministry-activity-patch-v4",
                        "operator-recovery-ministry-v4",
                        "campaign-content-patch-v5",
                        "operator-recovery-content-v5",
                        "integration-credential-patch-v6",
                        "initial-setup-patch-v7",
                        "source-cadence-patch-v8",
                        "operator-recovery-cadence-v8",
                        "integration-credential-cadence-v8",
                    ]
                ),
                name="config_request_schema",
            ),
        ),
        migrations.AddConstraint(
            "configurationchangerequest",
            models.CheckConstraint(
                condition=(
                    models.Q(
                        authority="admin",
                        actor_id__isnull=False,
                        operator_name__isnull=True,
                        operator_reason__isnull=True,
                        confirmed_deployment_id__isnull=True,
                        recovery_target__isnull=True,
                    )
                    & ~models.Q(request_schema__in=RECOVERY_SCHEMAS)
                )
                | models.Q(
                    authority="operator_recovery",
                    actor_id__isnull=True,
                    operator_name__isnull=False,
                    operator_reason__isnull=False,
                    confirmed_deployment_id__isnull=False,
                    recovery_target__isnull=False,
                    request_schema__in=RECOVERY_SCHEMAS,
                ),
                name="config_request_actor",
            ),
        ),
        migrations.RunPython(forward, backward),
    ]
