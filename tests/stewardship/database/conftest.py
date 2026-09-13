"""Only run database tests with the explicit disposable PostgreSQL profile."""

import pytest
from django.conf import settings

from .auth_builders import auth_service, google  # noqa: F401


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
