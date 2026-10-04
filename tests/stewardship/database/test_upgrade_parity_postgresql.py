"""An upgraded database ends in the same catalog as a fresh install (#487).

Production runs the previous release and reaches each new release through
forward migrations, while CI, the local environment and a restore-drill host
install the current tree from scratch. The two paths must agree. This test
builds a database with the previous release's tree (the latest annotated
``v*`` tag reachable from HEAD, exported with ``git archive`` and migrated in
a subprocess so none of the current tree is imported), migrates it with the
current tree, and compares every catalog object the schema inventory
fingerprints, the applied-migration list and the seed rows with a fresh
install of the current tree in a third database. Any difference fails with
the two definitions side by side. Both databases are named after the
pytest-django test database and dropped afterwards; the session database
itself is not used, because earlier tests toggle triggers and grants in it.

What this does not catch:

- An error present on both paths: the baseline plus every migration *is* the
  fresh install, so a wrong definition shared by both passes. The baseline
  fingerprint and the function-text test cover that side.
- Behaviour on real data. Only the seed rows exist before the upgrade; every
  business table is guarded by audit-context triggers, so a representative
  load belongs to the operator's backup-and-upgrade rehearsal, not here.
- Objects the inventory does not read: types, extensions, comments and
  anything outside ``public.stewardship_*``.
- Upgrades from anything but the latest release. When HEAD is itself the
  tagged commit and the checkout holds that tag as annotated, ``git
  describe`` names it and the comparison is trivially equal; a checkout that
  holds the tag only as a lightweight ref (as a shallow refetch can) falls
  back to the previous annotated tag and compares a real upgrade.
"""

import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import psycopg
import pytest
from django.db import connection

from parishkit.stewardship.schema_inventory import QUERIES, inventory

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "src/parishkit/stewardship/schema/seed.sql"
MIGRATE_TIMEOUT = 300
# Seed values generated at insert time differ between any two databases.
GENERATED_VALUES = {"gen_random_uuid()", "CURRENT_TIMESTAMP"}
# The subprocess settings import the disposable profile from whichever tree
# PYTHONPATH names, prove that is the tree the test meant, and retarget the
# database name; everything else (loopback host, test port, test user) stays.
SETTINGS = '''\
"""Disposable test profile aimed at an upgrade-parity database (generated)."""

import os
from pathlib import Path

import parishkit.stewardship
from parishkit.stewardship.settings.database_test import *  # noqa: F401,F403

_loaded = Path(parishkit.stewardship.__file__).resolve()
_source = os.environ["UPGRADE_PARITY_SOURCE"]
if not _loaded.is_relative_to(_source):
    raise RuntimeError(f"imported parishkit from {_loaded}, not {_source}")
DATABASES["default"]["NAME"] = os.environ["UPGRADE_PARITY_DATABASE"]
'''


def previous_release():
    """Name the latest annotated ``v*`` tag reachable from HEAD, or fail clearly.

    ``git describe`` without ``--tags`` considers annotated tags only, which is
    how releases are tagged. A shallow checkout cannot walk back to the tag,
    so CI's database shards check out the full history.
    """
    result = subprocess.run(
        ["git", "describe", "--abbrev=0", "--match", "v*"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        pytest.fail(
            "No previous release tag is reachable from HEAD; the upgrade-parity "
            "test needs an annotated v* tag in the checkout's history (a shallow "
            "clone must fetch tags and enough depth to reach one): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def export_release(tag, directory):
    """Extract the tag's ``src`` tree into a directory and return that tree."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", tag, "src"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(directory, filter="data")
    return directory / "src"


def migrate(source, settings_directory, database, label):
    """Run Django's ``migrate`` from one source tree against one database.

    The tree goes first on PYTHONPATH so it shadows any installed copy of the
    package, and the generated settings module refuses to run if it did not.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(settings_directory), str(source)])
    environment["DJANGO_SETTINGS_MODULE"] = "upgrade_parity_settings"
    environment["UPGRADE_PARITY_SOURCE"] = str(source)
    environment["UPGRADE_PARITY_DATABASE"] = database
    command = [sys.executable, "-m", "django", "migrate", "--noinput", "-v", "0"]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=settings_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=MIGRATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"migrate with the {label} tree against {database} was killed after "
            f"exceeding its {MIGRATE_TIMEOUT}s limit "
            f"(elapsed {time.monotonic() - started:.0f}s)"
        )
    if result.returncode:
        pytest.fail(
            f"migrate with the {label} tree against {database} failed "
            f"(exit {result.returncode}):\n{result.stdout}{result.stderr}"
        )


def database_parameters(name):
    """Connection keywords for one database on the disposable test cluster."""
    live = connection.settings_dict
    return {
        "host": live["HOST"],
        "port": live["PORT"],
        "user": live["USER"],
        "password": live["PASSWORD"],
        "dbname": name,
        "connect_timeout": 5,
    }


def drop_database(maintenance, name):
    """Drop a parity database, disconnecting any session still attached."""
    maintenance.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def definitions(connection):
    """Map each inventoried object to its JSON-encoded definition, per kind.

    ``inventory`` hashes these definitions; the text itself is what makes a
    failure readable.
    """
    result = {}
    with connection.cursor() as cursor:
        for kind, query in QUERIES.items():
            cursor.execute(query)
            result[kind] = {
                name: json.dumps(definition, separators=(",", ":"))
                for name, *definition in cursor.fetchall()
            }
    return result


def applied_migrations(connection):
    """The ``(app, name)`` pairs Django recorded as applied."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        return set(cursor.fetchall())


def seed_columns():
    """Map each seeded table to the seed columns whose values are literal.

    ``seed.sql`` is one ``INSERT INTO public.table (columns) VALUES (values)``
    per sentinel row; columns whose value is generated at insert time are
    skipped, every other column's value must survive the upgrade unchanged.
    """
    tables = {}
    text = SEED.read_text()
    pattern = r"^INSERT INTO public\.(\w+) \(([^)]*)\) VALUES \((.*)\);$"
    for table, columns, values in re.findall(pattern, text, re.M):
        # Splitting on ", " is only right while no quoted value contains one.
        assert not re.search(r"'[^']*,[^']*'", values), f"{table}: quoted comma"
        assert table not in tables, f"{table}: one sentinel row per table"
        tables[table] = [
            column
            for column, value in zip(
                columns.split(", "), values.split(", "), strict=True
            )
            if value not in GENERATED_VALUES
        ]
    # Every INSERT must have matched, so a reformatted statement cannot
    # silently drop its table from the comparison.
    assert len(tables) == len(re.findall(r"^INSERT INTO", text, re.M)) > 0
    return tables


def seed_rows(connection):
    """The literal seed values of every sentinel row, per table, in key order."""
    rows = {}
    with connection.cursor() as cursor:
        for table, columns in seed_columns().items():
            names = ", ".join(columns)
            cursor.execute(f"SELECT {names} FROM {table} ORDER BY {names}")
            rows[table] = cursor.fetchall()
    return rows


def describe_differences(upgraded, fresh):
    """List every object that exists on one side only or differs between them."""
    lines = []
    for kind in QUERIES:
        for name in sorted(upgraded[kind].keys() - fresh[kind].keys()):
            lines.append(f"{kind}: only after upgrade: {name}")
        for name in sorted(fresh[kind].keys() - upgraded[kind].keys()):
            lines.append(f"{kind}: only in fresh install: {name}")
        for name in sorted(upgraded[kind].keys() & fresh[kind].keys()):
            if upgraded[kind][name] != fresh[kind][name]:
                lines.append(
                    f"{kind}: differs: {name}\n"
                    f"  upgraded: {upgraded[kind][name]}\n"
                    f"  fresh:    {fresh[kind][name]}"
                )
    return lines


@pytest.mark.django_db
def test_previous_release_upgraded_matches_fresh_install(tmp_path):
    """Previous release, then the current tree's migrations, equals a fresh install."""
    tag = previous_release()
    previous = export_release(tag, tmp_path / "previous")
    current = ROOT / "src"
    settings_directory = tmp_path / "settings"
    settings_directory.mkdir()
    (settings_directory / "upgrade_parity_settings.py").write_text(SETTINGS)
    test_database = connection.settings_dict["NAME"]
    upgraded_name = f"{test_database}_upgraded"
    fresh_name = f"{test_database}_fresh"
    with psycopg.connect(
        **database_parameters(test_database), autocommit=True
    ) as maintenance:
        try:
            for name in (upgraded_name, fresh_name):
                drop_database(maintenance, name)
                maintenance.execute(f'CREATE DATABASE "{name}"')
            migrate(previous, settings_directory, upgraded_name, tag)
            migrate(current, settings_directory, upgraded_name, "current")
            migrate(current, settings_directory, fresh_name, "current")
            with (
                psycopg.connect(**database_parameters(upgraded_name)) as upgraded,
                psycopg.connect(**database_parameters(fresh_name)) as fresh,
            ):
                assert applied_migrations(upgraded) == applied_migrations(fresh)
                differences = describe_differences(
                    definitions(upgraded), definitions(fresh)
                )
                assert not differences, (
                    f"database upgraded from {tag} differs from a fresh install:\n"
                    + "\n".join(differences)
                )
                assert inventory(upgraded) == inventory(fresh)
                assert seed_rows(upgraded) == seed_rows(fresh)
        finally:
            for name in (upgraded_name, fresh_name):
                drop_database(maintenance, name)
