"""The current download-capacity schema protects policy and active readers."""

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.stewardship.campaigns.read_guards import DOWNLOAD_NAMESPACE

pytestmark = pytest.mark.django_db(transaction=True)


def test_capacity_delete_has_constraint_sqlstate():
    """Deletion is deliberately rejected, not an unassigned-record SQL error."""
    with (
        pytest.raises(IntegrityError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_download_policy")
    assert error.value.__cause__.sqlstate == "23514"


def test_capacity_resize_rejects_the_same_backend_owning_a_slot():
    """Advisory locks are reentrant, so explicitly reject self-owned downloads."""
    other = connections["default"].copy(alias="capacity-owner-probe")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s,0)", [DOWNLOAD_NAMESPACE])
            with pytest.raises(IntegrityError, match="download-owning session"):
                cursor.execute(
                    "UPDATE stewardship_download_policy "
                    "SET capacity=3, version=version+1"
                )
        with connection.cursor() as cursor:
            cursor.execute("SELECT capacity FROM stewardship_download_policy")
            assert cursor.fetchone()[0] == 4
    finally:
        other.close()
