"""Every accepted timezone name must work in PostgreSQL's own tzdata."""

import re
from importlib.resources import files

import pytest
from django.db import connection

from parishkit.stewardship.schema_primitives import timezone_names

SCHEMA = files("parishkit.stewardship").joinpath("schema")


@pytest.mark.django_db
def test_every_catalog_name_normalizes_to_a_database_zone():
    """CI runs the production PostgreSQL image, so gaps in its tzdata show here."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM unnest(%s::text[]) AS catalog(name) "
            "WHERE NOT EXISTS (SELECT 1 FROM pg_timezone_names p "
            "WHERE p.name = stewardship_timezone_name_v1(catalog.name))",
            [sorted(timezone_names())],
        )
        assert cursor.fetchall() == []


def test_stored_zone_columns_are_normalized_before_conversion():
    """A raw stored name fails for legacy aliases such as US/Eastern."""
    raw = re.compile(r"AT TIME ZONE\s+[a-z_]+\.timezone\b", re.IGNORECASE)
    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in SCHEMA.iterdir()
        if path.name.endswith(".sql")
        for match in raw.finditer(path.read_text())
    ]
    assert offenders == []
