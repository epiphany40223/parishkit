"""Use the same exact window derivation for request intake and read attempts."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.source.migrations.0009_refresh_request_guards"
)
BACKWARD = _previous.FORWARD.split(
    "CREATE TRIGGER stewardship_refresh_request_insert", 1
)[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
_start = "    IF NEW.campaign_id IS NOT NULL THEN\n"
_end = "    IF octet_length(NEW.window_canonical) > 1048576\n"
if BACKWARD.count(_start) != 1 or BACKWARD.count(_end) != 1:
    raise RuntimeError("Frozen refresh window guard is unavailable.")
_window = BACKWARD[BACKWARD.index(_start) : BACKWARD.index(_end)]
FORWARD = (
    BACKWARD.replace(
        _window,
        "    expected := "
        "public.stewardship_source_current_window_v1(NEW.campaign_id);\n",
    )
    .replace("expected jsonb;", "expected text;")
    .replace("stewardship_source_canonical(expected)", "expected")
    .replace(
        "        campaign stewardship_campaign%ROWTYPE;\n"
        "        values jsonb;\n"
        "        periods jsonb := '[]'::jsonb;\n",
        "",
    )
    .replace(
        "RETURNS trigger LANGUAGE plpgsql AS $$",
        "RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog,public,pg_temp AS $$",
    )
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0018_payload_guard_resolution")]
    operations = [
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunSQL(
            "ALTER FUNCTION public.stewardship_source_current_window_v1(uuid) "
            "SET search_path=pg_catalog,public,pg_temp;",
            "ALTER FUNCTION public.stewardship_source_current_window_v1(uuid) "
            "RESET search_path;",
        ),
    ]
