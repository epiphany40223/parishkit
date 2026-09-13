"""Cache typed membership query plans without weakening per-row lease checks."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.source.migrations.0018_payload_guard_resolution"
)
_start = "CREATE OR REPLACE FUNCTION public.stewardship_source_membership_guard()"
BACKWARD = _start + _previous.FORWARD.split(_start, 1)[1].split("$$;", 1)[0] + "$$;"
_lookup = (
    "    EXECUTE format('SELECT source_key,organization_id FROM public.%I "
    "WHERE id=$1', TG_ARGV[0])\n"
    "        INTO payload_key,payload_org USING NEW.payload_id;"
)
if BACKWARD.count(_lookup) != 1:
    raise RuntimeError("Frozen membership payload lookup is unavailable.")

# These are frozen migration identifiers, never provider-controlled table names.
# Static branches let PL/pgSQL reuse plans while every inserted membership still
# checks its task, source lease, wall clock and typed payload identity.
_branches = "\n".join(
    f"    WHEN 'stewardship_source_{kind}' THEN\n"
    "        SELECT source_key,organization_id INTO payload_key,payload_org\n"
    f"        FROM public.stewardship_source_{kind} WHERE id=NEW.payload_id;"
    for kind in (
        "family",
        "member",
        "contact",
        "address",
        "ministry",
        "roster",
        "fund",
        "pledge",
        "contribution",
    )
)
FORWARD = BACKWARD.replace(
    _lookup,
    "    CASE TG_ARGV[0]\n" + _branches + "\n"
    "    ELSE\n"
    "        RAISE EXCEPTION 'Unknown membership payload type' "
    "USING ERRCODE='23514';\n"
    "    END CASE;",
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0019_shared_refresh_window")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
