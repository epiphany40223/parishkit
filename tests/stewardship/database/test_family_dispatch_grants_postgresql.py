"""Real SQL permissions keep private dispatch separate from other runtimes."""

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "service,statement",
    [
        (
            ServiceRole.SCHEDULER,
            "SELECT sealed_substitutions FROM stewardship_outbox_message",
        ),
        (
            ServiceRole.SCHEDULER,
            "SELECT routed_recipients FROM stewardship_outbox_render",
        ),
        (ServiceRole.SCHEDULER, "SELECT ciphertext FROM stewardship_family_token"),
        (
            ServiceRole.MAIL_DISPATCH,
            "SELECT code_ciphertext FROM stewardship_family_campaign",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "SELECT code_ciphertext FROM stewardship_rehearsal_credential",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "UPDATE stewardship_family_campaign SET email_deliverable=false",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "UPDATE stewardship_family_token SET ciphertext=NULL",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "INSERT INTO stewardship_recipient_resolution DEFAULT VALUES",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "UPDATE stewardship_recipient_refusal SET address='forged@example.org'",
        ),
        (ServiceRole.MAIL_DISPATCH, "DELETE FROM stewardship_outbox_event"),
    ],
)
def test_dispatch_permission_denials(service, statement):
    """A valid mail outcome does not imply general household or credential writes."""
    with (
        task_login(service, exact=True),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"


def test_refusal_effect_is_a_non_callable_scoped_trigger():
    """Only validated immutable refusal insertion can derive Family deliverability."""
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT prosecdef, proconfig, "
            "has_function_privilege(current_user, oid, 'EXECUTE') "
            "FROM pg_proc WHERE "
            "oid='public.stewardship_refusal_family_effect_v1()'::regprocedure"
        )
        definer, configuration, executable = cursor.fetchone()
        assert definer and not executable
        assert configuration == ["search_path=pg_catalog, public, pg_temp"]
