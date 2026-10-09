"""Admin recovery revokes Admin sessions through column grants only (#389 L6).

The offline admin-recovery login's only use of ``stewardship_portal_session``
is the recovery activation trigger's revocation of every live session. Its
declared grants (``runtime_database.offline_columns``) are those columns, and
migration 0027 removes the whole-table grants an upgraded database still
holds, since ``database-grants`` refuses rather than revokes them.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_database import offline_columns, offline_grants
from parishkit.stewardship.runtime_grants import admit_columns

from .campaign_builders import initialized
from .test_background_grants_postgresql import task_login
from .test_recovery_postgresql import arguments, session

pytestmark = pytest.mark.django_db(transaction=True)

LOGIN = "pk_stewardship_admin_recovery"
FROZEN = (
    Path(__file__).parents[3]
    / "src/parishkit/stewardship/schema/migrations/0027_recovery_session_grants.sql"
)


def admit():
    """The offline login's column admission, as ``admit_offline_database``."""
    admit_columns(
        connection,
        offline_grants(ServiceRole.ADMIN_RECOVERY),
        offline_columns(ServiceRole.ADMIN_RECOVERY),
    )


def test_recovery_revokes_live_sessions_as_its_own_login(tmp_path):
    """The real recovery, run as the login, revokes a live Admin session."""
    store, _, _ = initialized(tmp_path)
    portal = session()
    kwargs = arguments()
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        assert recover_admin(store, **kwargs).state == "applied"
        # It cannot read the session key or rewrite a binding.
        for statement in (
            "SELECT session_id FROM stewardship_portal_session",
            "UPDATE stewardship_portal_session SET expires_at=expires_at",
        ):
            with (
                pytest.raises(DatabaseError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
    portal.refresh_from_db()
    assert portal.revoked_at is not None and portal.correlation_id is not None


def test_the_migration_removes_the_old_whole_table_grants():
    """An upgraded login's whole-table grants are refused until 0027 runs."""
    with task_login(ServiceRole.ADMIN_RECOVERY):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            # What the previous release's database-grants gave the login.
            cursor.execute(
                f"GRANT SELECT, UPDATE ON stewardship_portal_session TO {LOGIN}"
            )
            cursor.execute(f"SET SESSION AUTHORIZATION {LOGIN}")
        with pytest.raises(ConfigError, match="excessive"):
            admit()
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            with transaction.atomic():
                cursor.execute(FROZEN.read_text(encoding="utf-8"))
            # Running it again changes nothing: the check still passes.
            with transaction.atomic():
                cursor.execute(FROZEN.read_text(encoding="utf-8"))
            cursor.execute(f"SET SESSION AUTHORIZATION {LOGIN}")
        admit()
