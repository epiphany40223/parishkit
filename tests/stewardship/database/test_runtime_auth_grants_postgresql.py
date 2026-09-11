"""HTTP identity flows must work with web's actual closed table/column grants."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants

from .test_family_auth_postgresql import family_service, login  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def web_login():
    """Create a disposable exact login, never impersonate through inherited roles."""
    role = "pk_stewardship_web"
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [role])
        assert cursor.fetchone() is None
        cursor.execute(f'CREATE ROLE "{role}" LOGIN NOINHERIT')
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            tables, columns = runtime_grants(ServiceRole.WEB)
            for table, privileges in tables.items():
                cursor.execute(
                    f'GRANT {", ".join(sorted(privileges))} ON "{table}" TO "{role}"'
                )
            for table, privileges in columns.items():
                for privilege, names in privileges.items():
                    names = ",".join(f'"{name}"' for name in sorted(names))
                    cursor.execute(
                        f'GRANT {privilege} ({names}) ON "{table}" TO "{role}"'
                    )
            cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


@pytest.mark.parametrize("method", ["code", "token"])
def test_family_exchange_under_operational_web_grants(family_service, method):  # noqa: F811
    """A real Family cookie and page prove every invoked ORM/SQL lock is admitted."""
    with web_login():
        if method == "code":
            client, response = login(family_service.code)
        else:
            client = Client(enforce_csrf_checks=True)
            response = client.get("/access/" + family_service.token)
        assert response.status_code == 302
        assert "pk_family" in client.cookies
        assert client.get("/family/").status_code == 200


def test_campaign_transaction_does_not_lock_immutable_projection(tmp_path):
    """The web role can lock runtime/campaign without UPDATE on YAML projections."""
    from parishkit.stewardship.campaigns.runtime import campaign_transaction

    from .campaign_builders import draft_campaign

    _, campaign, _ = draft_campaign(tmp_path)
    with (
        web_login(),
        campaign_transaction(campaign.pk, correlation_id=uuid4()) as (row, runtime),
    ):
        assert row.active_configuration.pk == campaign.active_configuration_id
        assert runtime.current_campaign_id == row.pk
