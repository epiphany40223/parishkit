"""Only run database tests with the explicit disposable PostgreSQL profile."""

import pytest
from django.conf import settings

from .auth_builders import auth_service, google, real_limiter  # noqa: F401
from .response_builders import (  # noqa: F401
    earlier_rule,
    live_response_service,
    response_service,
)
from .schema_leak_guard import SchemaGuard, schema_connection
from .schema_leak_guard import failure as schema_leak_failure


@pytest.fixture(params=["force_custom_plan", "force_generic_plan"])
def sql_plan_mode(request):
    """Exercise trigger guards under both plans and restore the caller's setting."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SHOW plan_cache_mode")
        previous = cursor.fetchone()[0]
        cursor.execute(
            "SELECT set_config('plan_cache_mode', %s, false)", [request.param]
        )
    try:
        yield request.param
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('plan_cache_mode', %s, false)", [previous]
            )


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Verify fresh-install sentinels before any test flush or repair fixture.

    Override pytest-django's setup fixture while depending on its parent setup;
    assertions therefore inspect the actual migration result, not get_or_create
    helpers that individual tests use after their disposable database flushes.
    """
    from django.db import connection

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("SELECT id,capacity,version FROM stewardship_download_policy")
        assert cursor.fetchall() == [(1, 4, 1)]
        cursor.execute(
            "SELECT singleton,generation,organization_id,snapshot_id,version "
            "FROM stewardship_source_current"
        )
        assert cursor.fetchall() == [(True, 0, None, None, 1)]
        cursor.execute(
            "SELECT singleton,task_fence,worker_id,fence,phase,owner_id,acquired_at,"
            "heartbeat_at,expires_at,external_deadline,version "
            "FROM stewardship_source_lease"
        )
        assert cursor.fetchall() == [
            (True, 0, None, 0, "idle", None, None, None, None, None, 1)
        ]


def pytest_collection_modifyitems(items):
    """Skip before pytest-django can create a database in the pure baseline."""
    if settings.SETTINGS_MODULE != "parishkit.stewardship.settings.database_test":
        from pathlib import Path

        directory = Path(__file__).parent
        for item in items:
            if item.path.is_relative_to(directory):
                item.add_marker(
                    pytest.mark.skip(
                        reason="Requires disposable PostgreSQL database_test profile"
                    )
                )


@pytest.fixture(scope="session", autouse=True)
def _flush_without_post_migrate():
    """Skip the post_migrate signal when transactional tests flush.

    pytest-django flushes every table after each transactional test and, by
    default, re-emits post_migrate so contrib apps can recreate their content
    types and permissions. Nothing here reads those rows (the session setup
    already verified the migrated database), and re-creating them doubled the
    per-test teardown cost, so flushes leave them empty instead.
    """
    from unittest import mock

    from django.core.management.commands import flush

    with mock.patch.object(flush, "emit_post_migrate_signal", lambda *a, **k: None):
        yield


_SCHEMA_GUARD = SchemaGuard()


def _transactional(request):
    """Whether pytest-django runs this test without a wrapping rollback."""
    from pytest_django.fixtures import validate_django_db

    marker = request.node.get_closest_marker("django_db")
    if marker:
        transaction, reset_sequences, *_ = validate_django_db(marker)
        if transaction or reset_sequences:
            return True
    return bool(
        {"transactional_db", "live_server", "django_db_reset_sequences"}
        & set(request.fixturenames)
    )


@pytest.fixture(autouse=True)
def _schema_leak_guard(request):
    """Fail the transactional test that commits a schema change (#1489).

    Only transactional tests can commit on the test connection; any other
    test's changes roll back with it. Requesting ``transactional_db`` here
    sets the database up before this fixture, so this teardown still has
    database access and runs after the test's own fixtures have restored
    whatever they changed.
    """
    if not _transactional(request):
        yield
        return
    request.getfixturevalue("transactional_db")
    from django.db import connection

    _SCHEMA_GUARD.start(connection)
    yield
    with schema_connection(connection) as checked:
        changes = _SCHEMA_GUARD.finish(checked)
    if changes:
        pytest.fail(schema_leak_failure(changes), pytrace=False)
