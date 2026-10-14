"""Legacy time zone aliases on the production PostgreSQL image (#1286).

The catalog admits every IANA backward link (US/Eastern, Canada/Pacific,
...), but Debian's PostgreSQL image ships them only in tzdata-legacy, so
``pg_timezone_names`` lacks them. Every SQL conversion of a stored zone must
go through ``stewardship_timezone_name_v1``; 0095 fixed the one that did not,
the campaign comparison. CI runs the production image, so a gap shows here.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.archive.workbook import BAND_EDGES
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.comparison_reads import current_figures
from parishkit.stewardship.schema_primitives import timezone_links, timezone_names

from .. import configuration_factory
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_campaign_comparison_postgresql import (  # noqa: F401
    first_day,
    scenario,
    stewardship_year,
)
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0095_canonical_timezone.sql"
# 0072's body, the one 0095 replaced: it applies the stored zone as stored.
RAW = MIGRATIONS / "migrations/0072_digest_comparison.sql"
COMPARISON = "stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid)"

# An ``AT TIME ZONE`` operand: a literal, the normalizing call, or a name.
# Any other form (a parenthesized expression, a quoted identifier, $1) is
# captured by its first character, so it is reported rather than skipped.
OPERAND = re.compile(
    r"AT\s+TIME\s+ZONE\s+('[^']*'|[\w.]+\s*\(|[\w.]+|\S)", re.IGNORECASE
)
NORMALIZED = {"stewardship_timezone_name_v1(", "public.stewardship_timezone_name_v1("}
# The two functions that apply a local variable they first set from the
# normalizing call, with that assignment.
NORMALIZING = {
    "stewardship_resolve_local_v1": (
        "zone := public.stewardship_timezone_name_v1(zone);",
        "zone",
    ),
    "stewardship_browser_day_start_v1": (
        "catalog_name text:=stewardship_timezone_name_v1(zone);",
        "catalog_name",
    ),
}


def test_installed_helper_equals_the_tzdata_links_for_every_catalog_name():
    """The SQL table and Python's tzdata-derived mapping cannot drift apart.

    Every alias also names a zone this server knows, so no backward link the
    catalog admits fails in a conversion.
    """
    names = sorted(timezone_names())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name, stewardship_timezone_name_v1(name), "
            "EXISTS (SELECT 1 FROM pg_timezone_names p "
            "WHERE p.name = stewardship_timezone_name_v1(name)) "
            "FROM unnest(%s::text[]) AS catalog(name)",
            [names],
        )
        rows = cursor.fetchall()
    links = timezone_links()
    assert {name: target for name, target, _ in rows} == {
        name: links.get(name, name) for name in names
    }
    assert [name for name, _, known in rows if not known] == []
    # The whole backward set, not only US/*.
    assert len(links) >= 250
    assert {
        alias: links[alias]
        for alias in ("US/Eastern", "Canada/Pacific", "Mexico/General", "Japan")
    } == {
        "US/Eastern": "America/New_York",
        "Canada/Pacific": "America/Vancouver",
        "Mexico/General": "America/Mexico_City",
        "Japan": "Asia/Tokyo",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_timezone_names WHERE name = ANY(%s)",
            [sorted(links)],
        )
        # Why the helper exists: the image lacks the US/* links at least.
        assert cursor.fetchone()[0] < len(links)


def test_every_installed_conversion_applies_a_normalized_zone():
    """No installed function applies a stored or passed zone name as given.

    This reads the installed bodies, not the files, so a function a frozen
    migration created (as 0067 did) is checked too.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname, p.prosrc FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prosrc ~* 'AT\\s+TIME\\s+ZONE'"
        )
        bodies = cursor.fetchall()
        # Views apply no zone today; a future one is checked the same way.
        cursor.execute(
            "SELECT viewname, definition FROM pg_views WHERE schemaname = 'public' "
            "UNION ALL SELECT matviewname, definition FROM pg_matviews "
            "WHERE schemaname = 'public'"
        )
        bodies += [row for row in cursor.fetchall() if OPERAND.search(row[1])]
        # A session zone set from a name must be normalized too (0043).
        cursor.execute(
            "SELECT p.proname, m[1] FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace, "
            "regexp_matches(p.prosrc, "
            "'set_config\\(\\s*''timezone''\\s*,\\s*([\\w.]+\\s*\\(?)', 'gi') m "
            "WHERE n.nspname = 'public'"
        )
        settings = cursor.fetchall()
        assert settings, "the scan finds 0043's alert time"
        cursor.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
            "AND p.prosrc ~* '\\mtimezone\\s*\\('"
        )
        assert cursor.fetchone()[0] == 0
    offenders = []
    for name, body in bodies:
        assignment, variable = NORMALIZING.get(name, (None, None))
        if assignment is not None:
            assert assignment in body, name
        for operand in OPERAND.findall(body):
            if not (
                operand.startswith("'")
                or re.sub(r"\s+", "", operand) in NORMALIZED
                or operand == variable
            ):
                offenders.append(f"{name}: {operand}")
    offenders += [
        f"{name}: set_config {operand}"
        for name, operand in settings
        if re.sub(r"\s+", "", operand) not in NORMALIZED
    ]
    assert offenders == []
    assert "stewardship_campaign_comparison_v1" in {name for name, _ in bodies}


@pytest.fixture
def eastern(monkeypatch, request):
    """The comparison scenario with the Parish and campaign zone US/Eastern."""
    monkeypatch.setattr(configuration_factory, "PARISH_TIMEZONE", "US/Eastern")
    harness, ready, browser, actor = request.getfixturevalue("scenario")
    assert harness.campaign.active_configuration.timezone == "US/Eastern"
    return harness, ready, browser, actor


def test_a_us_eastern_campaign_reads_its_comparison(eastern):
    """The page's and the digest's logins read the figures; the page shows.

    0072's body, put back inside a rolled-back transaction, refuses the
    same call: the Production failure this migration ends.
    """
    harness, ready, browser, _ = eastern
    fact = first_day(ready)
    for role in (ServiceRole.WEB, ServiceRole.WORKER):
        with task_login(role, exact=True, reconnect=True), transaction.atomic():
            found = current_figures(ready.pk, fact, None)
        assert found["responded"] == fact.cumulative_responses
    opened = harness.campaign.active_configuration.starts_at
    with (
        campaign_clock(opened + timedelta(days=2, hours=12)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = get(browser, reverse("admin:campaign_comparison"))
    assert response.status_code == 200, body
    assert "Day 2 of" in body.decode()
    text = RAW.read_text(encoding="utf-8")
    start = text.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_campaign_comparison_v1("
    )
    old = text[start : text.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(old)
        with (
            pytest.raises(DatabaseError, match="US/Eastern"),
            transaction.atomic(),
        ):
            cursor.execute(
                "SELECT public.stewardship_campaign_comparison_v1"
                "(%s,%s,%s::numeric[],NULL)",
                [ready.pk, fact.local_date, list(BAND_EDGES)],
            )
        transaction.set_rollback(True)


@pytest.mark.parametrize("zone", ["US/Eastern", "Canada/Pacific", "Japan"])
def test_local_boundaries_accept_aliases(zone):
    """The shared local-time helpers resolve an alias as its zone."""
    canonical = timezone_links()[zone]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_resolve_local_v1('2026-03-08 12:00', %s) = "
            "stewardship_resolve_local_v1('2026-03-08 12:00', %s), "
            "stewardship_browser_day_start_v1('2026-11-01', %s) = "
            "stewardship_browser_day_start_v1('2026-11-01', %s)",
            [zone, canonical, zone, canonical],
        )
        assert cursor.fetchone() == (True, True)


def test_migration_check_refuses_a_raw_zone_or_changed_function():
    """0095's DO block passes as installed and refuses each regression."""
    text = FROZEN.read_text(encoding="utf-8")
    capture = text[text.index("SELECT set_config(") : text.index("true);") + 6]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    raw = RAW.read_text(encoding="utf-8")
    start = raw.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_campaign_comparison_v1("
    )
    old = raw[start : raw.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in (
            (old, "not installed as declared"),
            (f"ALTER FUNCTION public.{COMPARISON} SECURITY INVOKER", "not installed"),
            (
                f"GRANT EXECUTE ON FUNCTION public.{COMPARISON} TO PUBLIC",
                "not installed",
            ),
            (
                "CREATE ROLE pk_s1286_probe NOLOGIN; "
                f"GRANT EXECUTE ON FUNCTION public.{COMPARISON} TO pk_s1286_probe",
                "grants",
            ),
        ):
            with (
                pytest.raises(DatabaseError, match=f"Migration 0095.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
        cursor.execute(check)
        transaction.set_rollback(True)
