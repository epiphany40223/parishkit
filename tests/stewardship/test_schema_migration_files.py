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

# Every frozen file and its sha256. Add a line when adding a migration file;
# never change an existing line.
FROZEN = {
    "0002_family_engagement.sql": (
        "d029ac9aa4a5e7d6be742e4497e612c24bbffa93d6f3ce51d7d443a60c8d050a"
    ),
    "0003_refresh_tick_times.sql": (
        "906afe7be0ad16ada790a814adfa0ea83893c76956c75eef17900f4e6d286c02"
    ),
}


def function_bodies(text, *, replace):
    """Map each function name to its definition text in one SQL file.

    Definitions run from ``CREATE [OR REPLACE] FUNCTION public.name(`` to the
    ``$$;`` that closes their body; the text kept starts after the keyword, so
    the two forms compare equal.
    """
    keyword = "CREATE OR REPLACE FUNCTION" if replace else "CREATE FUNCTION"
    bodies = {}
    for match in re.finditer(rf"^{re.escape(keyword)} public\.(\w+)\(", text, re.M):
        # The body's opening $$ comes after the header; its closing $$; ends
        # the definition, whether written as "END $$;" or on its own line.
        body = text.index("$$", match.end()) + 2
        end = text.index("$$;", body) + len("$$;")
        bodies[match[1]] = text[match.start() + len(keyword) : end]
    return bodies


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
