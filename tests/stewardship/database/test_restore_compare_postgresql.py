"""``restore-compare``'s catalog description against real PostgreSQL (#608).

The migrated test database stands in for both sides: described as it is,
then after drift made inside the test's transaction (PostgreSQL DDL is
transactional, so the rollback undoes it). The whole command, with
pg_restore and a scratch server, is exercised by hand with the release
image (see the PR): the CI runner's own pg_restore cannot read a PostgreSQL
18 dump.
"""

import pytest
from django.db import connection

from parishkit.stewardship import restore_compare
from parishkit.stewardship.upgrade_check import disk_migrations


@pytest.mark.django_db
def test_the_migrated_schema_is_described_as_this_images():
    """Its migration records are this image's files; tables and columns of
    the public schema are listed with their facts."""
    with connection.cursor() as cursor:
        described = restore_compare.describe(cursor)
    assert described["migrations"] == set(disk_migrations())
    assert {"django_migrations", "stewardship_backup_run"} <= described["tables"]
    kind, not_null, _, identity, generated, collation = described["columns"][
        ("stewardship_backup_run", "manifest_digest")
    ]
    assert not_null is True and kind.startswith("character")
    assert (identity, generated, collation) == ("", "", "default")
    # Every new category is read from the real catalog too.
    assert "stewardship_backup_run" in described["objects"]["table_storage"]
    assert described["objects"]["types"] == {}
    # Every object category is read from the real catalog.
    objects = described["objects"]
    assert "stewardship_backup_run.backup_run_digests" in objects["constraints"]
    assert (
        "stewardship_backup_run.stewardship_backup_run_immutable_guard_v1"
        in objects["triggers"]
    )
    assert "stewardship_backup_run_immutable_v1()" in objects["functions"]
    assert len(objects["functions"]["stewardship_backup_run_immutable_v1()"]) == 64
    assert objects["indexes"] and objects["policies"] and objects["row_security"]
    assert restore_compare.is_same(restore_compare.difference(described, described))


@pytest.mark.django_db
def test_drift_made_in_the_database_is_reported_with_row_counts():
    """A dropped migration record, an added table with rows, an added and a
    dropped column and a changed type: each lands in its own list."""
    with connection.cursor() as cursor:
        image = restore_compare.describe(cursor)
        cursor.execute(
            "DELETE FROM django_migrations WHERE id=(SELECT max(id) FROM "
            "django_migrations)"
        )
        cursor.execute(
            "CREATE TABLE public.stewardship_legacy_note "
            "(id bigint PRIMARY KEY, body text NOT NULL)"
        )
        cursor.execute(
            "INSERT INTO public.stewardship_legacy_note VALUES (1,'a'),(2,'b')"
        )
        cursor.execute(
            "ALTER TABLE public.stewardship_backup_run "
            "ADD COLUMN legacy_flag boolean NOT NULL DEFAULT false"
        )
        cursor.execute(
            "ALTER TABLE public.stewardship_backup_run "
            "DROP COLUMN recipient_fingerprint"
        )
        cursor.execute(
            "ALTER TABLE public.stewardship_backup_run "
            "ALTER COLUMN application_version TYPE text"
        )
        cursor.execute("CREATE TYPE public.stewardship_legacy_kind AS ENUM ('a','b')")
        cursor.execute(
            "ALTER TABLE public.stewardship_legacy_note "
            "ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY"
        )
        cursor.execute(
            "CREATE INDEX stewardship_legacy_note_body ON "
            "public.stewardship_legacy_note (body)"
        )
        cursor.execute(
            "ALTER TABLE public.stewardship_backup_run DISABLE TRIGGER "
            "stewardship_backup_run_immutable_guard_v1"
        )
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.stewardship_backup_run_immutable_v1() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$"
        )
        backup = restore_compare.describe(cursor)
        report = restore_compare.difference(backup, image)
        rows = restore_compare.row_counts(
            cursor, restore_compare.differing_tables(report)
        )
    assert len(report["not_in_backup"]) == 1 and report["unknown_to_image"] == []
    assert report["tables_only_in_backup"] == ["stewardship_legacy_note"]
    assert report["tables_only_in_image"] == []
    assert [(c["table"], c["column"]) for c in report["columns_only_in_backup"]] == [
        ("stewardship_backup_run", "legacy_flag")
    ]
    assert report["columns_only_in_backup"][0]["default"] == "false"
    assert [(c["table"], c["column"]) for c in report["columns_only_in_image"]] == [
        ("stewardship_backup_run", "recipient_fingerprint")
    ]
    [changed] = report["columns_changed"]
    assert (changed["column"], changed["backup"]["type"]) == (
        "application_version",
        "text",
    )
    assert changed["image"]["type"] == "character varying(40)"
    assert rows == {"stewardship_backup_run": 0, "stewardship_legacy_note": 2}
    objects = report["objects"]
    # Dropping the column dropped the CHECK that named it (and, in
    # PostgreSQL 18, its NOT NULL constraint).
    assert (
        "stewardship_backup_run.backup_run_digests"
        in (objects["constraints"]["only_in_image"])
    )
    assert (
        "stewardship_legacy_note.stewardship_legacy_note_pkey"
        in (objects["constraints"]["only_in_backup"])
    )
    assert "stewardship_legacy_note_body" in objects["indexes"]["only_in_backup"]
    assert [c["name"] for c in objects["triggers"]["changed"]] == [
        "stewardship_backup_run.stewardship_backup_run_immutable_guard_v1"
    ]
    assert [c["name"] for c in objects["functions"]["changed"]] == [
        "stewardship_backup_run_immutable_v1()"
    ]
    assert objects["types"]["only_in_backup"] == ["stewardship_legacy_kind"]
    assert not restore_compare.is_same(report)


@pytest.mark.django_db
def test_the_test_server_is_not_a_scratch_server():
    """The guard refuses a cluster that holds any database of its own, as
    the deployment's cluster always does."""
    with (
        connection.cursor() as cursor,
        pytest.raises(restore_compare.RestoreCompareRefused, match="not an empty"),
    ):
        restore_compare.require_scratch_server(cursor)
