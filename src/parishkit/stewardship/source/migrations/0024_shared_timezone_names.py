"""Share accepted timezone alias normalization with scheduled source ticks."""

from django.db import migrations

RESOLVER = "public.stewardship_resolve_local_v1(timestamp without time zone,text)"
NORMALIZER = "public.stewardship_timezone_name_v1(text)"
TICK = "public.stewardship_refresh_tick_guard_v1()"


def definition(editor, signature):
    """Read one exact installed invoker body, retaining unrelated guard changes."""
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
        return cursor.fetchone()[0]


def replace(editor, signature, before, after):
    """Fail closed if a frozen predecessor does not match exactly once."""
    original = definition(editor, signature)
    if original.count(before) != 1:
        raise RuntimeError("Source timezone migration predecessor differs.")
    editor.execute(original.replace(before, after), params=None)


def forward(apps, editor):
    """Extract the resolver's frozen alias catalog; do not copy a drifting map."""
    original = definition(editor, RESOLVER)
    start, end = "zone := CASE zone", "ELSE zone END;"
    if original.count(start) != 1 or original.count(end) != 1:
        raise RuntimeError("Source timezone alias catalog differs.")
    aliases = original[original.index(start) : original.index(end) + len(end)]
    editor.execute(
        "CREATE FUNCTION public.stewardship_timezone_name_v1(zone text) "
        "RETURNS text LANGUAGE sql IMMUTABLE STRICT "
        "SET search_path=pg_catalog,public,pg_temp AS $$ SELECT "
        + aliases.removeprefix("zone := ").removesuffix(";")
        + " $$;",
        params=None,
    )
    replace(
        editor, RESOLVER, aliases, "zone := public.stewardship_timezone_name_v1(zone);"
    )
    replace(
        editor,
        TICK,
        "(NEW.due_at AT TIME ZONE zone)::date",
        "(NEW.due_at AT TIME ZONE public.stewardship_timezone_name_v1(zone))::date",
    )


def backward(apps, editor):
    """Re-inline the unchanged frozen catalog before removing its shared helper."""
    normalizer = definition(editor, NORMALIZER)
    start, end = "CASE zone", "ELSE zone END"
    if normalizer.count(start) != 1 or normalizer.count(end) != 1:
        raise RuntimeError("Source timezone alias successor differs.")
    aliases = normalizer[normalizer.index(start) : normalizer.index(end) + len(end)]
    replace(
        editor,
        TICK,
        "(NEW.due_at AT TIME ZONE public.stewardship_timezone_name_v1(zone))::date",
        "(NEW.due_at AT TIME ZONE zone)::date",
    )
    replace(
        editor,
        RESOLVER,
        "zone := public.stewardship_timezone_name_v1(zone);",
        "zone := " + aliases + ";",
    )
    editor.execute("DROP FUNCTION public.stewardship_timezone_name_v1(text);")


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0023_completed_setup_source_cleanup")]
    operations = [migrations.RunPython(forward, backward)]
