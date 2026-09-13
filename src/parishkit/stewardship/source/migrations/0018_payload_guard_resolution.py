"""Pin source guard resolution and parse each inserted canonical payload once."""

from importlib import import_module

from django.db import migrations

_old = import_module(
    "parishkit.stewardship.source.migrations.0004_source_payload_guards"
)
REVERSE = _old.FUNCTIONS.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
FORWARD = (
    REVERSE.replace("stewardship_source_", "public.stewardship_source_")
    .replace("stewardship_task_run", "public.stewardship_task_run")
    .replace("FROM %I WHERE", "FROM public.%I WHERE")
    .replace(" AS $$", " SET search_path=pg_catalog,public,pg_temp AS $$")
)


def replace_once(before, after):
    """Require the exact frozen predecessor before changing its insert-only path."""
    global FORWARD
    if FORWARD.count(before) != 1:
        raise RuntimeError("Frozen source payload guard is unavailable.")
    FORWARD = FORWARD.replace(before, after)


replace_once(
    "DECLARE field text;",
    "DECLARE field text; parsed jsonb; row_values jsonb;",
)
replace_once(
    "IF octet_length(NEW.canonical) > 1048576\n"
    "       OR jsonb_typeof(NEW.canonical::jsonb) <> 'object'",
    """IF octet_length(NEW.canonical) > 1048576 THEN
        RAISE EXCEPTION 'Source payload exceeds its bound' USING ERRCODE='23514';
    END IF;
    parsed := NEW.canonical::jsonb;
    row_values := to_jsonb(NEW);
    IF jsonb_typeof(parsed) <> 'object'""",
)
replace_once(
    "public.stewardship_source_canonical(NEW.canonical::jsonb)",
    "public.stewardship_source_canonical(parsed)",
)
replace_once("(NEW.canonical::jsonb->>field)", "(parsed->>field)")
FORWARD = FORWARD.replace("to_jsonb(NEW)->>field", "row_values->>field")


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0017_current_chair_projection")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
