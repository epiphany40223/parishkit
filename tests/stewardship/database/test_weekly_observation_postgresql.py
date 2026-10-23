"""Real live submissions, and the least-privilege reads of additional information.

The Weekly digest's capture retired with its pipeline (#1834); other suites
import ``respond`` from here to make a real submission with a request.
"""

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def respond(harness, text, *, live=True):
    """Create a real final submission and its transactionally derived item."""
    form, answers = form_and_answers(harness)
    answers.update(additional_information=text)
    return submit(harness, form, answers).submission


@pytest.mark.parametrize("role", [ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH])
def test_metadata_and_transport_roles_cannot_read_raw_requests(response_service, role):
    with (
        task_login(role, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT text FROM stewardship_additional_information")
    assert error.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT answers FROM stewardship_submission",
        "SELECT follow_up_needed FROM stewardship_additional_information",
        "UPDATE stewardship_additional_information SET text='changed'",
        "UPDATE stewardship_additional_information SET disposition='withdrawn'",
        "DELETE FROM stewardship_additional_information",
    ],
)
def test_worker_capture_grants_do_not_include_extra_private_columns_or_writes(
    response_service, statement
):
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"
