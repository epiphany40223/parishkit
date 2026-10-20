"""Report email builders for PostgreSQL tests (#1786, delivery plan item 4).

A report is added through the real configuration installer, as an
Administrator's change would add it; its sends are allocated by the real
scheduler producer, prepared by the real worker handler and delivered by
the real mail dispatch handler with a fake provider.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.report_email_finalization import (
    TASK_TYPE as FINALIZE,
)
from parishkit.stewardship.reports.report_email_finalization import (
    ReportEmailFinalizeProducer,
    finalization_handler,
)
from parishkit.stewardship.reports.report_email_ownership import (
    TASK_TYPE,
    ReportEmailProducer,
)
from parishkit.stewardship.reports.report_email_tasks import report_email_handler

from ..test_report_schedule import report
from .campaign_builders import change
from .test_background_grants_postgresql import task_login

ORIGIN = "https://parish.example"
WEEKLY_BLOCKS = [
    {"block": "additional_information", "version": 1, "options": {}},
    {"block": "response_funnel", "version": 1, "options": {}},
]


def report_values(campaign, **values):
    """Valid report values for ``campaign``, ``values`` replacing the defaults."""
    return report(**({"campaign_id": str(campaign.pk)} | values))


def users_form(store):
    """Convert the fixture's sign-in rules to users first, as Production is.

    Report emails exist only in the users form (``campaign-series-v12``).
    """
    from parishkit.stewardship.accounts.users_schema import uses_users

    from .user_builders import convert

    if not uses_users(store.active().document()):
        convert(store)


def add_report(store, campaign, **values):
    """Add one report email by an applied configuration change; its ID."""
    users_form(store)
    identifier = str(uuid4())
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "schedules",
                "id": identifier,
                "values": report_values(campaign, **values),
            }
        ],
    )
    assert result.state == "applied"
    return UUID(identifier)


def edit_report(store, identifier, **values):
    """Change one report's values by an applied configuration change."""
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "schedules",
                "id": str(identifier),
                "values": values,
            }
        ],
    )
    assert result.state == "applied"


def produce():
    """One scheduler pass of the report producer: the task statuses it made."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return ReportEmailProducer(uuid4())(guard)


def finalize():
    """One scheduler pass of the finalizer producer: the tasks it made."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return ReportEmailFinalizeProducer(uuid4())(guard)


def execute(status, *, task_type=TASK_TYPE):
    """Run one claimed report task with the worker's real maintained lifetime."""
    owner = (
        report_email_handler(public_origin=ORIGIN)
        if task_type == TASK_TYPE
        else finalization_handler()
    )
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            status.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={task_type: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)


def prepare_due():
    """Allocate and run every report send now due; the statuses run."""
    done = []
    for _ in range(25):
        allocated = produce()
        if not allocated:
            return done
        for status in allocated:
            execute(status)
            done.append(status)
    pytest.fail("Report production did not settle.")


def run_finalizers():
    """Allocate and run every metadata finalizer now needed."""
    for status in finalize():
        execute(status, task_type=FINALIZE)


def messages():
    """The report email messages."""
    return OutboxMessage.objects.filter(purpose="admin_report").order_by("id")


@contextmanager
def fake_provider(monkeypatch, *, status=Status.ACCEPTED):
    """Capture every report message the provider would send."""
    sent = []

    def provider(value, settings, mail, *, seconds, check):
        """Record the mail and answer ``status``."""
        check()
        sent.append(mail)
        return FamilyDeliveryResult(status, 1)

    for name in ("submit_digest", "submit_weekly"):
        monkeypatch.setattr(
            f"parishkit.stewardship.jobs.family_mail_delivery_tasks.{name}", provider
        )
    yield sent


def deliver_all(harness, path):
    """Deliver every pending report message with the real mail dispatch."""
    from .test_family_mail_worker_postgresql import deliver

    owner = None
    for message in messages().filter(state="pending"):
        owner = deliver(harness, path, message, owner)


@contextmanager
def digests_admitted(monkeypatch):
    """Let a test seed the Daily and Weekly digests the conversion replaces.

    No change may add a digest any more (``campaigns.configuration``); a
    deployment upgraded to this release holds them from before it.
    """
    from parishkit.stewardship.campaigns import configuration

    with monkeypatch.context() as patch:
        patch.setattr(configuration, "DIGEST_KINDS", frozenset())
        yield


def seed_digests(store, campaign, monkeypatch, *, daily_html="", weekly_html=None):
    """The campaign's Daily (07:00) and Weekly (Mondays 08:00) digests.

    Each sends the default email template of its slot, saved as content
    holds it, unless ``daily_html`` or ``weekly_html`` replaces the intro.
    Returns the Daily's and the Weekly's definition IDs.
    """
    from parishkit.stewardship.accounts.content_defaults import EMAILS
    from parishkit.stewardship.web.content import prepare_content

    from ..campaign_factory import schedule
    from ..content_factory import content

    users_form(store)
    patch, identifiers = [], []
    for kind, extra, html in (
        ("daily_digest", {"time": "07:00:00", "weekday": None}, daily_html),
        (
            "weekly_digest",
            {"time": "08:00:00", "weekday": 0},
            EMAILS["weekly_digest"].html if weekly_html is None else weekly_html,
        ),
    ):
        prepared = prepare_content(html)
        template = content(
            str(campaign.pk),
            kind="email",
            slot=kind,
            subject=EMAILS[kind].subject,
            html=prepared.html,
            text=prepared.text,
        )
        row = schedule(
            str(campaign.pk),
            kind=kind,
            date=None,
            subject=EMAILS[kind].subject,
            template_version=template["id"],
            **extra,
        )
        patch += [
            {"operation": "add", "section": "content", **template},
            {"operation": "add", "section": "schedules", **row},
        ]
        identifiers.append(UUID(row["id"]))
    with digests_admitted(monkeypatch):
        result = change(store, store.active(), uuid4(), patch)
    assert result.state == "applied"
    return tuple(identifiers)


def convert_reports():
    """Run the offline conversion as its admin-recovery login does."""
    from contextlib import nullcontext

    from parishkit.stewardship.accounts.authority import AuthorityStore  # noqa: F401
    from parishkit.stewardship.accounts.reports_upgrade import upgrade_reports
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    def run(store):
        """Convert once; the receipt, or None."""
        return upgrade_reports(
            store,
            deployment_id=SystemConfiguration.objects.get().pk,
            correlation_id=uuid4(),
            offline_interlock=nullcontext,
        )

    return run


@contextmanager
def triggers_off():
    """Write pre-conversion digest history directly, as an upgrade finds it.

    The rewritten guards no longer admit digest writes, so history from the
    release before is written with the triggers off (the test database's
    owner may), exactly as those guards once admitted it.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET session_replication_role = DEFAULT")


def digest_history(definition_id, slot, due_at, *, state="succeeded", disposition):
    """One past digest send: its occurrence and, when settled, its fulfillment."""
    from parishkit.stewardship.campaigns.schedule_models import (
        ScheduleDefinition,
        ScheduleFulfillment,
        ScheduleOccurrence,
    )
    from parishkit.stewardship.campaigns.schedules import occurrence_key

    definition = ScheduleDefinition.objects.get(pk=definition_id)
    runtime_mode = "production" if definition.campaign.state != "draft" else "testing"
    with triggers_off():
        occurrence = ScheduleOccurrence.objects.create(
            definition=definition,
            revision_id=definition.current_revision_id,
            mode=runtime_mode,
            routing="production"
            if runtime_mode == "production"
            else "testing_override",
            target="admins",
            slot=slot,
            due_at=due_at,
            occurrence_key=occurrence_key(
                definition.current_revision_id,
                runtime_mode,
                "admins",
                slot,
                production_cycle=definition.campaign.production_cycle
                if runtime_mode == "production"
                else 0,
            ),
            production_cycle=definition.campaign.production_cycle
            if runtime_mode == "production"
            else 0,
            state=state,
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
        if disposition is not None:
            ScheduleFulfillment.objects.create(
                definition=definition,
                mode=runtime_mode,
                target="admins",
                slot=slot,
                disposition=disposition,
                occurrence=occurrence,
                actor_id=uuid4(),
                correlation_id=uuid4(),
            )
    return occurrence


# The instants the digest pipeline's suites used, kept for the suites that
# now send report emails shaped as the converted Daily and Weekly (#1786):
# a morning of the fixture campaign (October 1 to 31, 2054) and a later one.
DAILY_INSTANT = datetime(2054, 10, 10, tzinfo=UTC)
WEEKLY_INSTANT = datetime(2054, 10, 29, tzinfo=UTC)


def add_digest(store, campaign, *, weekly=False, time="00:15:00", comparison=False):
    """A report shaped as the converted Daily or Weekly, added as a change.

    The conversion table's values (``reports_conversion.converted_values``)
    for a Daily at ``time`` or a Weekly on Wednesdays at ``time``, the
    digest the suites added before report emails replaced them. Its ID.
    """
    from parishkit.stewardship.accounts.reports_conversion import converted_values

    values = converted_values(
        {
            "campaign_id": str(campaign.pk),
            "kind": "weekly_digest" if weekly else "daily_digest",
            "time": time,
            "weekday": 2 if weekly else None,
            "subject": "",
            "comparison": comparison,
        },
        None,
    )
    return add_report(store, campaign, **values)


def allocated(harness, *, weekly=False, identifier=None):
    """Make the report's due send, through fanout: its preparation.

    Adds a report shaped as the converted Daily (or Weekly) unless
    ``identifier`` names one, then runs the real producer and worker at
    the caller's clock until the send's messages exist.
    """
    from parishkit.stewardship.reports.report_email_models import (
        ReportEmailPreparation,
    )

    from .campaign_builders import complete_empty_catchup

    store, campaign = harness.service.store, harness.campaign
    if identifier is None:
        identifier = add_digest(store, campaign, weekly=weekly)
    campaign.refresh_from_db()
    if campaign.state == "active":
        # A live campaign's sends wait for its activation catch-up.
        complete_empty_catchup(campaign, uuid4())
    prepare_due()
    return (
        ReportEmailPreparation.objects.filter(definition_id=identifier)
        .order_by("-created_at")
        .first()
    )


def begin(message, execution):
    """A report message's submission: no Family key or portal link."""
    from parishkit.stewardship.jobs.family_mail_dispatch import begin_submission

    return begin_submission(
        message.pk,
        execution.claim,
        private=None,
        public_origin=ORIGIN,
    )


def revoke_admin(harness, address):
    """Remove one Administrator's user by a real configuration change."""
    store = harness.service.store
    record = next(
        row
        for row in store.active().document()["sections"]["login_rules"]
        if row["values"]["email"] == address
    )
    patch = [{"operation": "remove", "section": "login_rules", "id": record["id"]}]
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def weekly_allocated(harness, **values):
    """``allocated`` for a report shaped as the converted Weekly."""
    return allocated(harness, weekly=True, **values)


def fail_preparation(harness):
    """A report send's preparation task, claimed and failed permanently.

    Adds a report shaped as the converted Daily and lets the real
    producer allocate its send at the caller's clock.
    """
    from .test_taskrun_postgresql import act

    add_digest(harness.service.store, harness.campaign)
    (status,) = produce()
    return act(act(status, "claim"), "permanent_failure")


def set_user(store, email, role, *, name="Reader"):
    """Add ``email`` as a users-form user with ``role``, or change its role.

    Report emails exist only in the users form, so suites that send one add
    and change their readers as users, by the Users page's own planner.
    """
    from parishkit.stewardship.accounts.user_changes import plan_changes

    records = store.active().document()["sections"]["login_rules"]
    found = next((row for row in records if row["values"]["email"] == email), None)
    entry = (
        {"email": email, "name": name, "role": role}
        if found is None
        else {"id": found["id"], "role": role}
    )
    _, patch = plan_changes(records, [entry])
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def settle_messages():
    """Deliver every pending report email under the mail dispatch role.

    For suites about what follows a delivered send (Send now waits for an
    earlier send's email): each message is claimed and its provider
    accepts it, through the real dispatch submission.
    """
    from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission

    from .test_family_mail_dispatch_postgresql import claim

    for message in messages().filter(state="pending"):
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is not None
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
