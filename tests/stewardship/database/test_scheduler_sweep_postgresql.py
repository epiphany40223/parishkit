"""One scheduler scan admits every kind of due work under the real scheduler login.

Each hint is admitted by its owner's ``admit`` callback while logged in as the
scheduler role, whose grants are deliberately narrow. A single owner that reads
a column the scheduler cannot see raises "permission denied", which aborts the
whole scan and stalls every kind of background work. These tests create due
work of each kind that can exist in Testing and Production, then scan with the
same handler set the scheduler process installs.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Handler
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scanning import collect_hints

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated as daily_allocated
from .test_daily_digest_planning_postgresql import INSTANT as DAILY_INSTANT
from .test_family_mail_dispatch_postgresql import prepare as prepare_family_mail
from .test_family_mail_preparation_postgresql import (
    allocate,
    family_mail,  # noqa: F401
)
from .test_family_mail_test_postgresql import (  # noqa: F401
    family_test,
    prepare_tests,
    request_tickets,
)
from .test_operational_dispatch_postgresql import allocated as operational_allocated
from .test_operational_routing_postgresql import routing  # noqa: F401
from .test_receipt_dispatch_postgresql import receipt
from .test_security_dispatch_postgresql import allocated as security_allocated
from .test_source_requests_postgresql import command
from .test_weekly_capture_postgresql import INSTANT as WEEKLY_INSTANT
from .test_weekly_dispatch_postgresql import allocated as weekly_allocated

pytestmark = pytest.mark.django_db(transaction=True)


def scheduler_registry(store):
    """Mirror the handler set configure_background installs for the scheduler.

    Keep in step with runtime_background: the compiled metadata owners, the
    operational/security/Slack owners, setup finalization, campaign test mail
    and the shared outbox delivery owner, all in their scheduler form.
    """
    from parishkit.stewardship.accounts.campaign_mail import TASK_TYPE as CAMPAIGN
    from parishkit.stewardship.accounts.campaign_mail_tasks import (
        campaign_mail_handler,
    )
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DISPATCH
    from parishkit.stewardship.jobs.operational_collection import (
        TASK_TYPE as COLLECT,
    )
    from parishkit.stewardship.jobs.operational_collection import collection_handler
    from parishkit.stewardship.jobs.operational_fanout import TASK_TYPE as PREPARE
    from parishkit.stewardship.jobs.operational_fanout import fanout_handler
    from parishkit.stewardship.jobs.operational_slack_tasks import TASK_TYPE as SLACK
    from parishkit.stewardship.jobs.operational_slack_tasks import slack_handler
    from parishkit.stewardship.jobs.outbox_dispatch import delivery_handler
    from parishkit.stewardship.jobs.security_owner import SECURITY
    from parishkit.stewardship.runtime_background import (
        bind_authority,
        scheduler_handlers,
    )
    from parishkit.stewardship.source.setup_final_execution import (
        finalization_handler,
    )
    from parishkit.stewardship.source.setup_final_tasks import (
        TASK_TYPE as FINALIZE,
    )

    handlers = bind_authority(scheduler_handlers(), store)
    handlers[COLLECT] = collection_handler(scheduler=True)
    handlers[PREPARE] = fanout_handler(store, scheduler=True)
    handlers[SECURITY.task_type] = fanout_handler(store, scheduler=True, owner=SECURITY)
    handlers[SLACK] = slack_handler(store, scheduler=True)
    handlers[FINALIZE] = finalization_handler(store, scheduler=True)
    handlers[CAMPAIGN] = campaign_mail_handler(store, scheduler=True)
    handlers[DISPATCH] = delivery_handler(store, scheduler=True)
    assert all(isinstance(handler, Handler) for handler in handlers.values())
    return handlers


def sweep(store):
    """Scan every due page as the exact scheduler login; return hinted run IDs.

    A permission gap anywhere raises out of collect_hints, failing the test.
    """
    handlers = scheduler_registry(store)
    hinted, cursor = set(), None
    with task_login(ServiceRole.SCHEDULER, exact=True):
        while True:
            hints, cursor = collect_hints(handlers=handlers, cursor=cursor)
            hinted |= {hint.run_id for hint in hints}
            if cursor is None:
                return hinted


def due_runs():
    """Every task a scan would consider, so a test can check it saw real work."""
    return set(
        TaskRun.objects.filter(state__in=("queued", "retry_wait")).values_list(
            "pk", flat=True
        )
    )


@pytest.fixture(autouse=True)
def provider_ready(monkeypatch):
    """Tests hold no Workspace key; admission otherwise treats mail as configured."""
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.mail_authority",
        lambda store: None,
    )


def test_family_test_ticket_and_message(family_test):  # noqa: F811
    """A queued chosen-Family test and its prepared delivery are both hinted."""
    harness, browser, path, _ = family_test
    (ticket,), _ = request_tickets(browser, path, [1])
    starts_at = harness.campaign.active_configuration.starts_at
    with campaign_clock(starts_at):
        assert ticket.task_id in sweep(harness.service.store)
    (message,) = prepare_tests(harness)
    with campaign_clock(starts_at):
        hinted = sweep(harness.service.store)
    assert message.task_id in hinted
    assert due_runs() <= hinted


@pytest.mark.parametrize("production", [False, True])
def test_scheduled_family_mail_preparation_and_delivery(
    family_mail,  # noqa: F811
    production,
):
    """Scheduled invitations: the preparation task, then its outbox delivery."""
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    store = family_mail.service.store
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        ticket = allocate()
        assert ticket.task_id in sweep(store)
        message = prepare_family_mail(family_mail)
        assert message.task_id in sweep(store)


def test_testing_submission_receipt(response_service):
    """A Family's submission receipt in Testing mode."""
    message = receipt(response_service)
    assert message.task_id in sweep(response_service.service.store)


def test_production_submission_receipt(live_response_service):
    """A Family's submission receipt in Production mode."""
    message = receipt(live_response_service, production=True)
    assert message.task_id in sweep(live_response_service.service.store)


def test_daily_digest(family_mail):  # noqa: F811
    """Per-Admin daily digest deliveries."""
    with campaign_clock(DAILY_INSTANT):
        daily_allocated(family_mail)
        messages = OutboxMessage.objects.filter(purpose="daily_digest")
        assert messages.exists()
        hinted = sweep(family_mail.service.store)
    assert {message.task_id for message in messages} <= hinted


def test_weekly_digest(live_response_service):
    """Per-Admin weekly digest deliveries."""
    with campaign_clock(WEEKLY_INSTANT):
        weekly_allocated(live_response_service)
        messages = OutboxMessage.objects.filter(purpose="weekly_digest")
        assert messages.exists()
        hinted = sweep(live_response_service.service.store)
    assert {message.task_id for message in messages} <= hinted


def test_operational_and_security_alerts(routing):  # noqa: F811
    """Operational alert and security-event mail to each Administrator."""
    store = routing[0]
    operational = operational_allocated(routing)
    security = security_allocated(routing)
    hinted = sweep(store)
    for recipient in [*operational, *security]:
        assert recipient.outbox.task_id in hinted


def test_source_refresh(tmp_path):
    """A waiting ParishSoft refresh request."""
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .campaign_builders import initialized

    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    store, _, _ = initialized(tmp_path)
    receipt_ = command()
    assert receipt_.task_root_id in sweep(store)
