"""Only pending patches selecting an asset hold its otherwise disposable bundle."""

from importlib import import_module

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations

_previous = (
    import_module(
        "parishkit.stewardship.accounts.migrations.0065_aborted_setup_branding"
    )
    .Migration.operations[0]
    .sql
)
_old = """IF EXISTS (SELECT 1 FROM public.stewardship_config_request r
            WHERE coalesce((SELECT c.state FROM public.stewardship_config_checkpoint c
                WHERE c.request_id=r.id ORDER BY c.sequence DESC LIMIT 1),'pending')
                NOT IN('applied','failed','cancelled')) THEN"""
_new = "IF public.stewardship_branding_pending_v1(NEW.id) THEN"
if _previous.count(_old) != 1:
    raise RuntimeError("Branding cleanup predecessor differs.")

PENDING = """
CREATE FUNCTION public.stewardship_branding_pending_v1(identifier uuid)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_branding_asset a
        JOIN public.stewardship_config_request r ON r.patch @>
            jsonb_build_array(jsonb_build_object('section','parish','values',
                jsonb_build_object('branding',jsonb_build_object(a.label,a.id::text))))
        WHERE a.bundle_id=identifier AND coalesce((
            SELECT c.state FROM public.stewardship_config_checkpoint c
            WHERE c.request_id=r.id ORDER BY c.sequence DESC LIMIT 1
        ),'pending') NOT IN ('applied','failed','cancelled')
    );
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0091_campaign_mail_guards")]
    operations = [
        migrations.AddIndex(
            model_name="configurationchangerequest",
            index=GinIndex(
                fields=["patch"],
                name="config_request_patch_lookup",
                opclasses=["jsonb_path_ops"],
            ),
        ),
        migrations.RunSQL(
            PENDING + _previous.replace(_old, _new),
            _previous + "\nDROP FUNCTION public.stewardship_branding_pending_v1(uuid);",
        ),
    ]
