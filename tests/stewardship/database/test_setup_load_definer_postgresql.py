"""Background setup-attempt updates under generic plans (#2163, migration 0177).

0174's setup load organization check, a trigger on the setup attempt, reads
the ParishSoft organization lookup table, which only web and the ParishSoft
installer may read. Under a generic plan PostgreSQL checks SELECT on every
table the trigger's condition names before it runs, whatever the condition's
value, so the check must run with its owner's rights or every scheduler and
worker update of a setup attempt fails.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.sessions import end_admin
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_staging import begin_setup
from parishkit.stewardship.jobs.models import TaskRun

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_expiry_postgresql import sweep
from .test_setup_loading_postgresql import pages, prepared, run
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)

GENERIC = "force_generic_plan"
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0177_setup_load_definer.sql"
OLD = MIGRATIONS / "0174_parishsoft_organization_choice.sql"
FUNCTION = "public.stewardship_setup_load_organization_v1()"
NAME = f"ALTER FUNCTION {FUNCTION}"


@pytest.fixture
def generic_plans(monkeypatch):
    """Plan every statement generically, on this and every new connection.

    The current connection gets the setting directly; ``PGOPTIONS`` covers
    the connections the worker's renewal and reconnects open (libpq reads
    it at connect time). The caller's setting comes back afterwards.
    """
    monkeypatch.setenv("PGOPTIONS", f"-c plan_cache_mode={GENERIC}")
    with connection.cursor() as cursor:
        cursor.execute("SHOW plan_cache_mode")
        previous = cursor.fetchone()[0]
        cursor.execute("SELECT set_config('plan_cache_mode', %s, false)", [GENERIC])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('plan_cache_mode', %s, false)", [previous]
            )


def test_the_check_runs_with_its_owners_rights(db):
    """0177 installed the check as SECURITY DEFINER, closed to PUBLIC."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT prosecdef, has_function_privilege('public', oid, 'EXECUTE') "
            "FROM pg_proc WHERE oid=to_regprocedure("
            "'public.stewardship_setup_load_organization_v1()')"
        )
        assert cursor.fetchone() == (True, False)


def test_scheduler_expires_a_setup_under_generic_plans(setup_service, generic_plans):
    """The scheduler's expiry sweep updates the abandoned attempt."""
    request = login(setup_service)
    begin_setup(request, setup_service)
    end_admin(request)
    assert sweep() == 1
    with connection.cursor() as cursor:
        cursor.execute("SHOW plan_cache_mode")
        assert cursor.fetchone()[0] == GENERIC
    assert SetupAttempt.objects.get().state == "expired"


def test_worker_finishes_a_setup_load_under_generic_plans(
    setup_service, monkeypatch, generic_plans
):
    """The worker's load completion moves the attempt back to collecting."""
    values = prepared(setup_service)
    fake_provider(monkeypatch, pages())
    run(*values, complete=True)
    assert TaskRun.objects.get().state == "succeeded"
    assert SetupAttempt.objects.get().state == "collecting"


def test_the_do_block_passes_installed_and_refuses_each_missing_part(db):
    """0177's closing block passes as installed and refuses 0174's old check.

    Each run is in a transaction that rolls back, so the installed
    definition stays. 0179 (#2166) has since replaced the body again, with
    its operator qualified, so each first puts 0177's own body back.
    """
    text = FROZEN.read_text(encoding="utf-8")
    block = text[text.index("DO $check$") :]
    start = text.index(f"CREATE OR REPLACE FUNCTION {FUNCTION}")
    own = text[start : text.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(own)
        cursor.execute(block)
        # Keep 0179's body: roll the restore back.
        transaction.set_rollback(True)
    # 0174's caller's-rights definition, as 0174 created it.
    old = OLD.read_text(encoding="utf-8")
    start = old.index("CREATE FUNCTION public.stewardship_setup_load_organization_v1()")
    old_body = old[start : old.index("END $$;", start) + len("END $$;")]
    old_body = old_body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    for undo in (
        old_body,
        # 0174's body with 0177's rights still differs from 0177's body.
        f"{old_body}\n{NAME} SECURITY DEFINER",
        f"{NAME} SECURITY INVOKER",
        f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO PUBLIC",
        f"{NAME} RESET search_path",
        "ALTER TABLE public.stewardship_setup_attempt "
        "DISABLE TRIGGER stewardship_setup_load_organization",
    ):
        with (
            pytest.raises(DatabaseError, match="Migration 0177"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(own)
            cursor.execute(undo)
            cursor.execute(block)
