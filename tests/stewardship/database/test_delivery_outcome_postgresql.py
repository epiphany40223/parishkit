"""The Mail message page's outcome panel against real admission (#1563).

``read_detail`` names why sending an email again is unavailable only after
``stewardship_delivery_retry_admitted_v1`` refused it, from columns the web
login reads: these check, on real emails, that an answer is allowed exactly
when the admission admits it, and that each refusal is named by its cause.
"""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus
from parishkit.stewardship.jobs.delivery_reads import read_detail

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery, retry_admitted
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_outbox_boundaries_postgresql import control

pytestmark = pytest.mark.django_db(transaction=True)


def detail(message):
    """The page's read of ``message``, as the web login reads it."""
    with task_login(ServiceRole.WEB, exact=True):
        return read_detail(message.pk, QueryDict())


def sending(data):
    """The send-again answer of a read, by its choice."""
    (row,) = [
        row for row in data["choices"] if row["action"] in {"resend", "retry_failed"}
    ]
    return row


def live(family_mail):  # noqa: F811
    """The Production campaign the emails below belong to."""
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    return harness


@pytest.mark.parametrize(
    "status", [FamilyDeliveryStatus.UNKNOWN, FamilyDeliveryStatus.PERMANENT]
)
def test_an_admitted_send_is_an_allowed_answer(family_mail, status):  # noqa: F811
    """While the admission admits a send, its answer is allowed, and every
    other answer of the question too."""
    harness = live(family_mail)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness, status)
        assert retry_admitted(message) is True
        data = detail(message)
    assert data["outcome"] == "needs_resolution"
    assert all(row["unavailable"] is None for row in data["choices"])
    assert {row["action"] for row in data["choices"]} <= set(data["actions"])


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (FamilyDeliveryStatus.PERMANENT, "paused"),
        (FamilyDeliveryStatus.UNKNOWN, None),
    ],
)
def test_a_paused_campaign_fences_a_retry_but_not_a_resend(  # noqa: F811
    family_mail,  # noqa: F811
    status,
    reason,
):
    """A pause refuses sending a failed email again, named "paused"; the
    resend of one that may have arrived is still admitted and allowed."""
    harness = live(family_mail)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness, status)
        control(harness.campaign, "pause")
        assert retry_admitted(message) is (reason is None)
        data = detail(message)
    assert sending(data)["unavailable"] == reason
    assert data["paused"] is True


def test_an_ended_campaign_names_why_it_is_not_sent_again(family_mail):  # noqa: F811
    """After the campaign's end the resend is refused and named
    "campaign_ended"; recording what was found is still allowed."""
    harness = live(family_mail)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness)
    harness.campaign.refresh_from_db()
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        assert retry_admitted(message) is False
        data = detail(message)
    reasons = {row["choice"]: row["unavailable"] for row in data["choices"]}
    assert reasons == {
        "sent": None,
        "resend": "campaign_ended",
        "not_sent": None,
        "unsure": None,
    }
    assert data["last_attempt_at"] is not None
