"""Frozen forward-migration SQL files stay frozen and in step with the baseline.

A released forward migration installs ``schema/migrations/NNNN_*.sql`` whole.
Its text must never change afterwards (a Production database already ran it),
so each file's digest is pinned here. The four-digit prefixes form one
repository-wide sequence, so the files sort in apply order even though Django
numbers migrations per app. When such a file re-creates a function the
fresh-install baseline also defines, the latest migration's copy must be the
baseline's current body: a fresh install runs the baseline and then the
migrations, so the two texts are what a fresh install and an upgraded database
each end up with. A later change to a replaced function needs a new numbered
migration file, which then becomes "latest" here. A function an earlier
migration file created (plain ``CREATE FUNCTION``) is migration-owned: every
install path runs the migration that replaces it, so no baseline copy exists
or is compared. Views and constraints have no text check here; the
upgrade-parity database test catches their drift.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src/parishkit/stewardship/schema"
MIGRATIONS = SCHEMA / "migrations"
# A baseline statement making a function SECURITY DEFINER: its name and its
# identity arguments (pg_get_function_identity_arguments, as pg_dump writes
# them). Shared by the attribute test and its self-test.
DEFINER_ALTER = re.compile(
    r"^ALTER FUNCTION public\.(\w+)\(([^)]*)\) SECURITY DEFINER;", re.M
)

# Every frozen file and its sha256. Add a line when adding a migration file;
# never change an existing line.
FROZEN = {
    "0002_family_engagement.sql": (
        "d029ac9aa4a5e7d6be742e4497e612c24bbffa93d6f3ce51d7d443a60c8d050a"
    ),
    "0003_refresh_tick_times.sql": (
        "906afe7be0ad16ada790a814adfa0ea83893c76956c75eef17900f4e6d286c02"
    ),
    "0004_automation_sessions.sql": (
        "e0b01ea24cad2ca148a774d51b81828331bdcefde857adf34a8e3897ec5fdf2b"
    ),
    "0005_occurrence_prepare_ahead.sql": (
        "12bc2e9c5c21af7da9ff2d3867f3421d9be5a0b63a6c58383931c764304e2987"
    ),
    "0006_due_work_context.sql": (
        "f9ac6212009daec6ccaeb2bdfa6df7aa07ac44ff27487d519dbba3dc75147e13"
    ),
    "0007_system_health_records.sql": (
        "cbea01108beda94c69f14b5d76c0817ab3a6a50df3bc552c6b1cf1fcc74a0943"
    ),
    "0008_log_detail.sql": (
        "14fc3b72cfaf129d5a8799bd66abaaefcae1f4a5b1a1be49abc0aa657edb1dd8"
    ),
    "0009_unchanged_snapshots.sql": (
        "206f09701bc2f4b034ec5a180831d6bc3bafcc2ef3d6a4d0647c4c1e659e0e4d"
    ),
    "0010_task_type_index.sql": (
        "5326954fb557cff286c211caceb1e283c1b340fcbdbcfe4d5daa5037be6c66e1"
    ),
    "0011_refresh_schedule.sql": (
        "5701c8cad983e9c1c1d7343f2e603f1a50fe4139cc35af0d4c04096ad9eb2b1b"
    ),
}


def function_bodies(text, *, replace):
    """Map each function name to its definition text in one SQL file.

    Definitions run from ``CREATE [OR REPLACE] FUNCTION public.name(`` to the
    closing dollar quote (``$$`` or a tagged ``$_$``) followed by ``;``; the
    text kept starts after the keyword, so the two forms compare equal.
    """
    keyword = "CREATE OR REPLACE FUNCTION" if replace else "CREATE FUNCTION"
    bodies = {}
    for match in re.finditer(rf"^{re.escape(keyword)} public\.(\w+)\(", text, re.M):
        # The header ends at "AS" and the body's opening dollar quote, which
        # pg_dump tags ($_$) when the body itself contains a dollar sign; the
        # same tag followed by ";" ends the definition, whether written as
        # "END $$;" or on its own line.
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
        body = quote.end()
        end = text.index(quote[1] + ";", body) + len(quote[1]) + 1
        bodies[match[1]] = text[match.start() + len(keyword) : end]
    return bodies


def test_function_bodies_end_at_the_tag_that_opened_them():
    """A pg_dump-tagged body ($_$) containing "$$;" is read to its own end."""
    text = (
        "CREATE FUNCTION public.tagged() RETURNS text\n"
        "    LANGUAGE sql\n"
        "    AS $_$ SELECT '$$;' || '^a$' $_$;\n"
        "CREATE FUNCTION public.plain() RETURNS void\n"
        "    LANGUAGE plpgsql\n"
        "    AS $$\nBEGIN\nEND $$;\n"
    )
    bodies = function_bodies(text, replace=False)
    assert bodies["tagged"].endswith("|| '^a$' $_$;")
    assert bodies["plain"].endswith("END $$;")
    replaced = function_bodies(
        text.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"), replace=True
    )
    assert replaced == bodies


def test_every_frozen_migration_file_is_pinned_and_unchanged():
    files = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert files == sorted(FROZEN), "pin every schema/migrations/*.sql digest"
    prefixes = [name[:4] for name in files]
    assert all(prefix.isdigit() for prefix in prefixes), files
    # The baseline is 0001; frozen files continue from 0002 without gaps.
    assert [int(prefix) for prefix in prefixes] == list(range(2, 2 + len(prefixes))), (
        "frozen files form one consecutive repository-wide sequence from 0002"
    )
    for name, digest in FROZEN.items():
        text = (MIGRATIONS / name).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == digest, name
        assert (
            text.startswith("--") and "SET LOCAL check_function_bodies = false;" in text
        )
        # Every frozen file is part of the image.
        for ignore in (
            ROOT / ".dockerignore",
            ROOT / "deploy/stewardship/Dockerfile.dockerignore",
        ):
            assert (
                f"!src/parishkit/stewardship/schema/migrations/{name}"
                in ignore.read_text()
            )


def test_latest_migration_copy_of_each_replaced_function_equals_the_baseline():
    """The fresh-install files already carry what the latest migration installs."""
    baseline = {}
    for path in SCHEMA.glob("*.sql"):
        baseline.update(
            function_bodies(path.read_text(encoding="utf-8"), replace=False)
        )
    latest = {}
    migration_owned = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        migration_owned |= function_bodies(text, replace=False).keys()
        latest.update(function_bodies(text, replace=True))
    assert latest, "no replaced functions found"
    for name, body in latest.items():
        if name in migration_owned:
            # Created by a frozen file, so every install path runs the
            # migration that replaces it; there is no baseline copy to match.
            continue
        assert name in baseline, (
            f"{name} is replaced but never created: a new function uses plain "
            "CREATE FUNCTION"
        )
        assert body == baseline[name], f"{name}: latest migration differs from baseline"


def test_frozen_file_migrations_apply_in_file_order():
    """Each frozen-file migration depends on the one before it, so the database
    applies the files in prefix order whatever their apps' own numbering.

    Django's loader reads the migration modules from disk without a database;
    a module that installs a frozen file names it in ``FROZEN_SQL``.
    """
    import sys

    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    frozen = {}
    for key, migration in loader.disk_migrations.items():
        module = sys.modules[migration.__module__]
        if hasattr(module, "FROZEN_SQL"):
            frozen[Path(module.FROZEN_SQL).name] = key
    assert sorted(frozen) == sorted(FROZEN), "every frozen file has one migration"
    ordered = [frozen[name] for name in sorted(frozen)]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        # forwards_plan lists every migration that must apply before current.
        assert previous in loader.graph.forwards_plan(current), (
            f"{current} must depend on {previous}, directly or transitively"
        )


def test_replaced_functions_keep_the_baselines_altered_attributes():
    """A replaced function is altered again as the baseline alters it.

    ``CREATE OR REPLACE FUNCTION`` keeps a function's owner and grants but
    resets every attribute the command does not name. The baseline makes
    some guards ``SECURITY DEFINER`` with a separate ``ALTER FUNCTION``, so a
    frozen file that replaces one must repeat that statement, or the guard
    would silently start running with its caller's rights (BG-12 found
    this for the occurrence guard). The baseline names each function with
    its identity arguments (``pg_get_function_identity_arguments``, as
    pg_dump writes them), and the frozen file must name the same ones.
    """
    altered = {}
    for path in SCHEMA.glob("*.sql"):
        for name, arguments in DEFINER_ALTER.findall(path.read_text(encoding="utf-8")):
            altered.setdefault(name, set()).add(arguments)
    assert "" in altered["stewardship_occurrence_guard_v1"]
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for name in function_bodies(text, replace=True).keys() & altered.keys():
            created = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
            for arguments in altered[name]:
                statement = (
                    f"ALTER FUNCTION public.{name}({arguments}) SECURITY DEFINER;"
                )
                assert statement in text, f"{path.name} must restore {name}'s rights"
                assert text.index(statement) > created


def test_altered_attribute_scan_reads_identity_arguments():
    """A function with arguments is matched with its full identity."""
    text = (
        "ALTER FUNCTION public.example_v1(uuid, text) SECURITY DEFINER;\n"
        "ALTER FUNCTION public.other_v1() SECURITY DEFINER;\n"
    )
    assert DEFINER_ALTER.findall(text) == [
        ("example_v1", "uuid, text"),
        ("other_v1", ""),
    ]
