"""Unpin wizard-only media after, never during, selected-candidate restoration."""

from importlib import import_module

from django.db import migrations

# ruff: noqa: E501
_source = import_module(
    "parishkit.stewardship.accounts.migrations.0055_branding_guards"
).FORWARD
_start = _source.index("CREATE FUNCTION public.stewardship_branding_bundle_guard_v1()")
_end = _source.index("$$;", _start) + 3
_prior = _source[_start:_end].replace(
    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
)
_old = "WHERE a.bundle_id=NEW.id)"
_new = """WHERE a.bundle_id=NEW.id AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_config_request q
                JOIN public.stewardship_setup_config_intent i ON i.request_id=q.id
                JOIN public.stewardship_setup_config_abort b ON b.intent_id=i.id
                CROSS JOIN LATERAL (SELECT state,failure_code FROM public.stewardship_config_checkpoint c
                    WHERE c.request_id=q.id ORDER BY sequence DESC LIMIT 1) receipt
                WHERE q.candidate_version_id=p.configuration_id AND q.request_schema='initial-setup-patch-v7'
                    AND receipt.state='failed' AND receipt.failure_code='invalid_candidate'
                    AND NOT EXISTS (SELECT 1 FROM public.stewardship_config_activation WHERE request_id=q.id)))"""
if _prior.count(_old) != 1:
    raise RuntimeError("Setup branding guard predecessor is inconsistent.")
_pin = "\nALTER FUNCTION public.stewardship_branding_bundle_guard_v1() SET search_path=pg_catalog,public,pg_temp;"


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0064_setup_configuration_guards")]
    operations = [
        migrations.RunSQL(_prior.replace(_old, _new) + _pin, _prior + _pin),
        migrations.RunSQL(
            migrations.RunSQL.noop,
            """DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM public.stewardship_setup_config_abort) THEN
                    RAISE EXCEPTION 'Setup cancellation cleanup history prevents downgrade'
                        USING ERRCODE='23514';
                END IF;
            END $$;""",
        ),
    ]
