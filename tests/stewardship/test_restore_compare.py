"""``restore-compare``: a backup's schema against this image's, in scratch.

No PostgreSQL here: the scratch server is a fake connection that records
each statement and answers the catalog queries, and pg_restore is a stand-in
on PATH that accepts only the exact arguments the load passes (anything else
exits 64). The catalog description itself runs against real PostgreSQL in
``database/test_restore_compare_postgresql.py``.
"""

import json
import logging
import os
import shutil
import stat

import pytest

from parishkit.stewardship import observability, restore_compare
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.cli import main

M1, M2 = ("stewardship_jobs", "0001_initial"), ("stewardship_jobs", "0002_more")


def col(kind, not_null=True, default=None, identity="", generated="", collation=None):
    """One column's facts, as ``describe`` reads them."""
    return (kind, not_null, default, identity, generated, collation)


TEXT = col("text", collation="default")


def description(tables, columns, migrations):
    """A ``describe`` result: tables, {(table, column): facts}, migrations."""
    return {"tables": set(tables), "columns": columns, "migrations": set(migrations)}


BASE = description(
    {"django_migrations", "stewardship_a", "stewardship_b"},
    {
        ("stewardship_a", "id"): col("bigint"),
        ("stewardship_a", "note"): TEXT,
        ("stewardship_b", "id"): col("bigint"),
    },
    {M1},
)


def test_identical_descriptions_have_no_differences():
    """Every list in the report is empty."""
    report = restore_compare.difference(BASE, BASE)
    assert restore_compare.is_same(report)
    assert set(report["objects"]) == set(restore_compare.OBJECTS)
    assert restore_compare.differing_tables(report) == []


def test_every_object_category_is_compared_by_name_and_definition():
    """A function changed in place, a constraint only in the set, an index
    only in the image, a changed policy: each in its own category, and the
    schemas are no longer the same (#792 review, M3)."""
    image = {
        **BASE,
        "objects": {
            "functions": {"stewardship_guard_v1()": "a" * 64},
            "indexes": {"stewardship_a_new": "CREATE INDEX ..."},
            "policies": {"stewardship_a.scope": "PERMISSIVE ALL {public} USING x"},
        },
    }
    backup = {
        **BASE,
        "objects": {
            "functions": {"stewardship_guard_v1()": "b" * 64},
            "constraints": {"stewardship_a.old_check": "c CHECK (true)"},
            "policies": {"stewardship_a.scope": "PERMISSIVE ALL {public} USING y"},
        },
    }
    report = restore_compare.difference(backup, image)
    objects = report["objects"]
    assert objects["functions"]["changed"] == [
        {"name": "stewardship_guard_v1()", "backup": "b" * 64, "image": "a" * 64}
    ]
    assert objects["constraints"]["only_in_backup"] == ["stewardship_a.old_check"]
    assert objects["indexes"]["only_in_image"] == ["stewardship_a_new"]
    assert [c["name"] for c in objects["policies"]["changed"]] == [
        "stewardship_a.scope"
    ]
    assert objects["views"] == {
        "only_in_backup": [],
        "only_in_image": [],
        "changed": [],
    }
    assert not restore_compare.is_same(report)
    # Tables, columns and migrations alone still match.
    assert not any(value for key, value in report.items() if key != "objects")


def test_a_statement_stopped_at_its_limit_is_logged_and_refused(caplog):
    """The statement limit is named with its seconds and the step's elapsed
    time; other errors pass through unchanged (#792 review, L4)."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")

    class Diag:
        message_primary = "canceling statement due to statement timeout"

    class QueryCanceled(Exception):
        sqlstate, diag = "57014", Diag()

    class Wrapped(Exception):
        """Django's wrapper, with the driver error as its cause."""

    with (
        pytest.raises(restore_compare.RestoreCompareRefused, match="time limit"),
        restore_compare.statement_limit(restore_compare.MIGRATE_SECONDS),
    ):
        raise Wrapped() from QueryCanceled()
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if "timeout" in line.get("extra", {})]
    assert line["extra"]["timeout"] == "statement_timeout"
    assert line["extra"]["limit_seconds"] == restore_compare.MIGRATE_SECONDS
    with pytest.raises(RuntimeError), restore_compare.statement_limit(1):
        raise RuntimeError("not a timeout")


def test_every_kind_of_difference_is_reported():
    """Migrations both ways, tables both ways, columns both ways, changes.

    A table on one side only is reported once, not column by column.
    """
    image = description(
        {"django_migrations", "stewardship_a", "stewardship_c"},
        {
            ("stewardship_a", "id"): col("bigint"),
            ("stewardship_a", "note"): col(
                "character varying(40)", False, "''::text", collation="default"
            ),
            ("stewardship_a", "added"): col("integer", default="0"),
            ("stewardship_c", "id"): col("bigint"),
        },
        {M1, M2},
    )
    backup = description(
        BASE["tables"],
        {**BASE["columns"], ("stewardship_a", "gone"): TEXT},
        {M1, ("stewardship_old", "0001_initial")},
    )
    report = restore_compare.difference(backup, image)
    assert report["not_in_backup"] == [list(M2)]
    assert report["unknown_to_image"] == [["stewardship_old", "0001_initial"]]
    assert report["tables_only_in_backup"] == ["stewardship_b"]
    assert report["tables_only_in_image"] == ["stewardship_c"]
    assert report["columns_only_in_backup"] == [
        {
            "table": "stewardship_a",
            "column": "gone",
            "type": "text",
            "not_null": True,
            "default": None,
            "identity": "",
            "generated": "",
            "collation": "default",
        }
    ]
    assert [c["column"] for c in report["columns_only_in_image"]] == ["added"]
    assert report["columns_changed"] == [
        {
            "table": "stewardship_a",
            "column": "note",
            "backup": {
                "type": "text",
                "not_null": True,
                "default": None,
                "identity": "",
                "generated": "",
                "collation": "default",
            },
            "image": {
                "type": "character varying(40)",
                "not_null": False,
                "default": "''::text",
                "identity": "",
                "generated": "",
                "collation": "default",
            },
        }
    ]
    assert restore_compare.differing_tables(report) == [
        "stewardship_a",
        "stewardship_b",
    ]


class FakeCursor:
    """Records statements and answers the few queries the command makes."""

    def __init__(self, server, database):
        self.server, self.database, self.rows = server, database, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.server.statements.append((self.database, " ".join(sql.split())))
        answers = self.server.answers.get(self.database, {})
        for key, rows in answers.items():
            if key in sql:
                self.rows = rows
                return
        if "pg_database" in sql:
            self.rows = [(name,) for name in self.server.databases]
        elif "rolsuper" in sql:
            self.rows = [(self.server.superuser,)]
        elif "count(*)" in sql:
            self.rows = [(7,)]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeServer:
    """A scratch server: its databases, and each database's catalog answers."""

    def __init__(self):
        self.databases = {"postgres", "template0", "template1"}
        self.superuser = True
        self.statements = []
        self.answers = {}

    def catalog(self, database, value):
        """Answer ``describe``'s three queries for one database."""
        self.answers[database] = {
            "pg_attribute": [
                (table, column, *facts)
                for (table, column), facts in value["columns"].items()
            ],
            "django_migrations": [tuple(m) for m in value["migrations"]],
            "SELECT c.relname FROM": [(t,) for t in value["tables"]],
        }

    def connect(self, host, port, password, database):
        """Stand in for restore_compare._connect."""
        assert (host, port, password) == ("scratch-pg", 5432, "pw")
        server = self

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return FakeCursor(server, database)

        return Connection()


@pytest.fixture
def server(monkeypatch):
    """A fake scratch server whose image database is BASE and backup differs."""
    fake = FakeServer()
    fake.catalog(restore_compare.IMAGE_DATABASE, BASE)
    fake.catalog(
        restore_compare.BACKUP_DATABASE,
        description(
            BASE["tables"] | {"stewardship_old"},
            {**BASE["columns"], ("stewardship_old", "id"): col("bigint")},
            BASE["migrations"],
        ),
    )
    monkeypatch.setattr(restore_compare, "_connect", fake.connect)
    monkeypatch.setattr(restore_compare, "load_dump", lambda dump, **kw: None)
    return fake


def run(**more):
    """compare() with the fake server and a recorded migrate."""
    migrated = []
    code, report = restore_compare.compare(
        "dump",
        host="scratch-pg",
        port=5432,
        password="pw",
        migrate=lambda *a: migrated.append(a),
        copy=lambda **kw: migrated.append(("copy", kw["host"])),
        **more,
    )
    return code, report, migrated


def statements(server, word):
    """The statements of one kind, in order."""
    return [sql for _, sql in server.statements if sql.startswith(word)]


def test_a_comparison_creates_loads_migrates_describes_and_drops(server):
    """Both scratch databases are created, then dropped; the report counts
    rows of the backup-only table only."""
    code, report, migrated = run()
    assert code == restore_compare.DIFFERENT and report["result"] == "different"
    assert report["tables_only_in_backup"] == ["stewardship_old"]
    assert report["rows"] == {"stewardship_old": 7}
    assert migrated == [("scratch-pg", 5432, "pw"), ("copy", "scratch-pg")]
    names = ["pk_restore_backup", "pk_restore_image", "pk_restore_migrated"]
    assert statements(server, "CREATE DATABASE") == [
        f'CREATE DATABASE "{name}"' for name in names
    ]
    assert statements(server, "DROP DATABASE") == [
        f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)' for name in names
    ]
    assert "kept_databases" not in report


def test_identical_schemas_exit_zero(server):
    """The same description on both sides: same, exit 0."""
    server.catalog(restore_compare.BACKUP_DATABASE, BASE)
    code, report, _ = run()
    assert (code, report["result"], report["rows"]) == (0, "same", {})


def test_keep_leaves_both_databases_and_names_them(server):
    """--keep: only the migrated original is dropped, and the report says
    what was kept."""
    _, report, _ = run(keep=True)
    assert statements(server, "DROP DATABASE") == [
        'DROP DATABASE IF EXISTS "pk_restore_migrated" WITH (FORCE)'
    ]
    assert report["kept_databases"] == ["pk_restore_backup", "pk_restore_image"]


def test_a_failed_load_still_drops_the_scratch_databases(server, monkeypatch):
    """A failure after creation drops both databases, even with --keep."""

    def fail(dump, **kw):
        raise restore_compare.RestoreCompareRefused("The dump could not be loaded.")

    monkeypatch.setattr(restore_compare, "load_dump", fail)
    with pytest.raises(restore_compare.RestoreCompareRefused):
        run(keep=True)
    assert len(statements(server, "DROP DATABASE")) == 3


@pytest.mark.parametrize("extra", [{"stewardship"}, {"pk_restore_backup"}])
def test_a_server_that_is_not_empty_is_refused_before_anything(server, extra):
    """The deployment's database, or a leftover scratch database: refused,
    and nothing is created or dropped."""
    server.databases |= extra
    with pytest.raises(restore_compare.RestoreCompareRefused, match="not an empty"):
        run()
    assert statements(server, "CREATE") == statements(server, "DROP") == []


def test_a_login_that_is_not_the_superuser_is_refused(server):
    """The scratch login must be its server's superuser."""
    server.superuser = False
    with pytest.raises(restore_compare.RestoreCompareRefused, match="superuser"):
        run()
    assert statements(server, "CREATE") == []


def test_a_database_without_migration_records_is_refused(server):
    """A dump of something that is not a Stewardship database."""
    server.answers[restore_compare.BACKUP_DATABASE]["SELECT c.relname FROM"] = [
        ("stewardship_a",)
    ]
    with pytest.raises(restore_compare.RestoreCompareRefused, match="no migration"):
        run()
    assert len(statements(server, "DROP DATABASE")) == 3


# The load gives pg_restore only these environment variables, so the
# stand-in's paths are written into it; its behaviour is chosen by files:
# SLEEP holds seconds to sleep, FAIL makes it fail. It also checks that the
# password arrives in PGPASSWORD and nowhere in its arguments.
STAND_IN = """#!/bin/sh
expected="--no-owner --no-acl --no-subscriptions --no-publications"
expected="$expected --exit-on-error --single-transaction"
expected="$expected --host scratch-pg --port 5432 --username postgres"
expected="$expected --dbname pk_restore_backup {dump}"
[ "$#" -eq 15 ] && [ "$*" = "$expected" ] || exit 64
[ "$PGPASSWORD" = pw ] && [ "$PGSSLMODE" = disable ] || exit 65
[ ! -e '{state}/SLEEP' ] || exec sleep "$(cat '{state}/SLEEP')"
[ ! -e '{state}/FAIL' ] || exit 1
touch '{state}/LOADED'
"""


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """The stand-in pg_restore on PATH; returns the dump path."""
    directory = tmp_path / "bin"
    directory.mkdir()
    dump = tmp_path / "database.pgdump"
    dump.write_bytes(b"PGDMP")
    script = directory / "pg_restore"
    script.write_text(STAND_IN.format(dump=dump, state=tmp_path))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    # The Compose test container mounts /tmp noexec: the stand-in is not
    # executable there, so PATH would find the image's real pg_restore. The
    # other CI jobs still run these tests.
    if shutil.which("pg_restore") != str(script):
        pytest.skip("the temporary directory cannot run the stand-in pg_restore")
    return dump


def load(dump, **more):
    """load_dump() against the stand-in's expected connection."""
    restore_compare.load_dump(dump, host="scratch-pg", port=5432, password="pw", **more)


def test_the_load_passes_exactly_its_arguments(stand_in, tmp_path):
    """One transaction, no owners or privileges, password in the environment."""
    load(stand_in)
    assert (tmp_path / "LOADED").exists()


def test_a_failed_or_mis_invoked_load_is_refused(stand_in, tmp_path):
    """pg_restore failing, or given another dump (the stand-in's 64)."""
    (tmp_path / "FAIL").touch()
    with pytest.raises(restore_compare.RestoreCompareRefused, match="not be loaded"):
        load(stand_in)
    (tmp_path / "FAIL").unlink()
    other = tmp_path / "other.pgdump"
    other.write_bytes(b"PGDMP")
    with pytest.raises(restore_compare.RestoreCompareRefused, match="not be loaded"):
        load(other)


def test_a_load_past_its_limit_is_killed_and_logged(stand_in, tmp_path, caplog):
    """The kill is logged with what, the limit and the elapsed time."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    (tmp_path / "SLEEP").write_text("30")
    with pytest.raises(restore_compare.RestoreCompareRefused, match="too long"):
        load(stand_in, seconds=1)
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if "timeout" in line.get("extra", {})]
    assert line["level"] == "WARNING"
    assert line["extra"]["timeout"] == "restore_compare_load"
    assert line["extra"]["limit_seconds"] == 1
    assert line["extra"]["elapsed_seconds"] in (1, 2)


def test_a_missing_pg_restore_is_refused(tmp_path, monkeypatch):
    """The release image ships pg_restore; another environment may not."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(restore_compare.RestoreCompareRefused, match="not installed"):
        load(tmp_path / "database.pgdump")


@pytest.fixture
def command(tmp_path, monkeypatch, capsys):
    """Run ``pk-stewardship restore-compare`` with a recorded compare()."""
    monkeypatch.setattr(restore_compare, "configure_logging", lambda: None)
    password = tmp_path / "private" / "password"
    password.parent.mkdir(mode=0o700)
    write_private(password, b"pw\n")
    calls = []

    def compare(dump, **kwargs):
        calls.append((dump, kwargs))
        return 3, {"result": "different"}

    monkeypatch.setattr(restore_compare, "compare", compare)

    def invoke(*more, host="scratch-pg"):
        arguments = ["restore-compare", "--dump", "d.pgdump"]
        if host is not None:
            arguments += ["--scratch-host", host]
        code = main([*arguments, "--scratch-password-file", str(password), *more])
        return code, capsys.readouterr(), calls

    return invoke


def test_the_command_passes_its_options_and_prints_the_report(command):
    """Port default 5432, --keep, the password from its owner-only file."""
    code, captured, calls = command()
    assert code == 3 and json.loads(captured.out) == {"result": "different"}
    assert calls == [
        (
            "d.pgdump",
            {"host": "scratch-pg", "port": 5432, "password": "pw", "keep": False},
        )
    ]
    code, _, calls = command("--scratch-port", "6543", "--keep")
    assert calls[-1][1]["port"] == 6543 and calls[-1][1]["keep"] is True


@pytest.mark.parametrize(
    "host,more,reason",
    [
        ("postgres", (), "not a scratch server"),
        ("bad host!", (), "not a scratch server"),
        (None, (), "scratch host are required"),
        ("scratch-pg", ("--scratch-port", "0"), "not a port number"),
        ("scratch-pg", ("--scratch-port", "x"), "not a port number"),
    ],
)
def test_the_command_refuses_bad_options_with_a_fixed_reason(
    command, host, more, reason
):
    """The deployment's own database host is refused by name; nothing runs."""
    code, captured, calls = command(*more, host=host)
    assert code == 2 and captured.out == "" and calls == []
    assert reason in captured.err


def test_a_password_file_that_is_not_owner_only_is_refused_by_name(command, tmp_path):
    """read_private's refusal (CryptographicError) gets its own fixed reason,
    not the generic line (#792 review, L2)."""
    password = tmp_path / "private" / "password"
    password.chmod(0o644)
    code, captured, calls = command()
    assert code == 2 and calls == []
    assert "scratch password file is unreadable" in captured.err


def test_a_generic_failure_names_no_host_or_path(command, monkeypatch):
    """An unexpected error (say, a connection refusal naming the host) gets
    a fixed line."""

    def compare(dump, **kwargs):
        raise OSError("could not connect to scratch-pg at /secret/path")

    monkeypatch.setattr(restore_compare, "compare", compare)
    code, captured, _ = command()
    assert code == 2
    assert "scratch-pg" not in captured.err and "/secret" not in captured.err
    assert "could not be used" in captured.err


def test_the_command_takes_only_its_own_options(capsys):
    """--keep belongs to restore-compare alone."""
    with pytest.raises(SystemExit):
        main(["restore-check", "--manifest", "m", "--keep"])
    assert "--keep" in capsys.readouterr().err


def test_identity_generated_and_collation_changes_are_column_changes():
    """A column that became an identity, a generated column, or changed
    collation is ``columns_changed`` even with the same type (#792 review)."""
    image = {
        **BASE,
        "columns": {
            **BASE["columns"],
            ("stewardship_a", "id"): col("bigint", identity="a"),
            ("stewardship_a", "note"): col("text", collation="C"),
            ("stewardship_b", "id"): col("bigint", generated="s"),
        },
    }
    report = restore_compare.difference(BASE, image)
    changed = {(c["table"], c["column"]): c["image"] for c in report["columns_changed"]}
    assert changed[("stewardship_a", "id")]["identity"] == "a"
    assert changed[("stewardship_a", "note")]["collation"] == "C"
    assert changed[("stewardship_b", "id")]["generated"] == "s"
    assert not restore_compare.is_same(report)


# Stand-ins for the copy's pg_dump | pg_restore pipe. pg_dump checks its
# exact arguments and writes a marker; pg_restore checks its own, reads the
# whole pipe and records what it got. Files choose failure and slowness.
DUMP_STAND_IN = """#!/bin/sh
expected="--format=custom --host scratch-pg --port 5432 --username postgres"
expected="$expected --dbname pk_restore_migrated"
[ "$#" -eq 9 ] && [ "$*" = "$expected" ] && [ "$PGPASSWORD" = pw ] || exit 64
[ ! -e '{state}/DUMP_SLEEP' ] || sleep "$(cat '{state}/DUMP_SLEEP')"
printf 'PGDMP-copy'
[ ! -e '{state}/DUMP_FAIL' ] || exit 1
"""
RESTORE_STAND_IN = """#!/bin/sh
expected="--no-owner --no-acl --no-subscriptions --no-publications"
expected="$expected --exit-on-error --single-transaction --host scratch-pg"
expected="$expected --port 5432 --username postgres --dbname pk_restore_image"
[ "$#" -eq 14 ] && [ "$*" = "$expected" ] || exit 64
cat > '{state}/RESTORED'
[ ! -e '{state}/RESTORE_FAIL' ] || exit 1
"""


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    """Both stand-ins on PATH; returns the state directory."""
    directory = tmp_path / "pipe-bin"
    directory.mkdir()
    for name, text in (("pg_dump", DUMP_STAND_IN), ("pg_restore", RESTORE_STAND_IN)):
        script = directory / name
        script.write_text(text.format(state=tmp_path))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def copy(**more):
    """copy_migrated() against the stand-ins' expected connection."""
    restore_compare.copy_migrated(host="scratch-pg", port=5432, password="pw", **more)


def test_the_copy_pipes_the_dump_into_the_image_database(pipe):
    """What pg_dump writes is exactly what pg_restore reads."""
    copy()
    assert (pipe / "RESTORED").read_text() == "PGDMP-copy"


@pytest.mark.parametrize("failing", ["DUMP_FAIL", "RESTORE_FAIL"])
def test_either_side_failing_refuses_the_comparison(pipe, failing):
    """A failed pg_dump or pg_restore is the same fixed refusal."""
    (pipe / failing).touch()
    with pytest.raises(restore_compare.RestoreCompareRefused, match="not be copied"):
        copy()


def test_a_copy_past_its_limit_kills_both_and_is_logged(pipe, caplog):
    """Both processes are killed at the limit, and the kill is logged."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    (pipe / "DUMP_SLEEP").write_text("30")
    with pytest.raises(restore_compare.RestoreCompareRefused, match="too long"):
        copy(seconds=1)
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if "timeout" in line.get("extra", {})]
    assert line["extra"]["timeout"] == "restore_compare_load"
    assert line["extra"]["limit_seconds"] == 1


def test_a_missing_pg_dump_is_refused(tmp_path, monkeypatch):
    """The release image ships pg_dump; another environment may not."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(restore_compare.RestoreCompareRefused, match="pg_dump"):
        copy()
