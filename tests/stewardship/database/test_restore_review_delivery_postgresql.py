"""What the ordinary planner and sender do with held emails after release (#537).

A held email is never sent unless an Administrator chose "send again"; then
the ordinary planner sends it once, coalesced with any reminder due at the
same time. "Assumed sent" lets the Family's reminders go on; an undecided
invitation keeps them back. A message prepared before the backup waits for
the decision, and is cancelled if the email is assumed sent.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError

from parishkit.stewardship.campaigns.credential_keys import initialize_key_inventories
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    RestoreHoldResolution,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.restore_review import (
    begin_review,
    list_held_emails,
    release_review,
    settle_held_email,
)
from parishkit.stewardship.campaigns.schedule_production import FamilyScheduleProducer
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import (
    FamilyDeliveryHeld,
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import (  # noqa: F401
    family_mail,
    handler,
)
from .test_family_mail_prepare_ahead_postgresql import add_reminder
from .test_restore_review_postgresql import (
    BACKUP,
    NOW,
    live_campaign,
    remove_schedule,
    restored_occurrence,
)

pytestmark = pytest.mark.django_db(transaction=True)


def restore_and_list(actor):
    """Close the site for review and hold what may have gone out (owner rights)."""
    begin_review(backup_at=BACKUP, reason="restore", correlation_id=uuid4())
    return list_held_emails(actor_id=actor, correlation_id=uuid4())


def decide(hold, state, actor):
    """One decision as the schema owner, whom the freshness check admits."""
    settle_held_email(
        hold_id=hold.pk,
        expected_version=hold.version,
        state=state,
        evidence="Compared with the provider's sent log.",
        actor_id=actor,
        session_id=uuid4(),
        authenticated_at=NOW,
        correlation_id=uuid4(),
    )


def release(actor):
    """Reopen the site after review."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    release_review(
        request_id=uuid4(),
        expected_runtime_version=SystemConfiguration.objects.get().version,
        actor_id=actor,
        session_id=uuid4(),
        authenticated_at=NOW,
        correlation_id=uuid4(),
    )


def third_family(auth_service, *, invitation):
    """Family 3 of the live campaign after review: reminders sent again,
    the invitation decided as ``invitation`` (None leaves it undecided)."""
    campaign = live_campaign(auth_service)
    actor = uuid4()
    with campaign_clock(NOW):
        complete_empty_catchup(campaign, actor)
        restore_and_list(actor)
        family = FamilyCampaign.objects.get(family_duid=3)
        for hold in RestoreDeliveryHold.objects.filter(target=f"family:{family.pk}"):
            if hold.definition.kind == "reminder":
                decide(hold, "resend_authorized", actor)
            elif invitation:
                decide(hold, invitation, actor)
        release(actor)
    return family, actor


def plan(family, actor):
    """The scheduler's ordinary planning of one Family group, now."""
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        return plan_family(guard, family_id=family.pk, worker_id=actor)


def test_send_again_plans_one_email_coalesced_with_due_reminders(auth_service):
    """The resent invitation is the one email; the two due reminders coalesce."""
    family, actor = third_family(auth_service, invitation="resend_authorized")
    first = plan(family, actor)
    assert not first.held and first.created == 3 and first.coalesced == 2
    selected = ScheduleOccurrence.objects.get(pk=first.selected)
    assert selected.definition.kind == "initial"
    # Planning again adds nothing: it is sent once.
    again = plan(family, actor)
    assert again.created == 0 and again.selected == first.selected


def test_an_invitation_decided_after_release_is_planned(auth_service):
    """Undecided at release, no invitation is planned; sent again later, it is
    (#757)."""
    family, actor = third_family(auth_service, invitation=None)
    target = f"family:{family.pk}"
    plan(family, actor)
    assert not ScheduleOccurrence.objects.filter(
        target=target, definition__kind="initial"
    ).exists()
    invitation = RestoreDeliveryHold.objects.get(
        target=target, definition__kind="initial"
    )
    with campaign_clock(NOW):
        decide(invitation, "resend_authorized", actor)
    # The invitation is the one email now; the reminder planned meanwhile is
    # coalesced into it.
    result = plan(family, actor)
    assert not result.held and result.created == 1 and result.coalesced == 1
    selected = ScheduleOccurrence.objects.get(pk=result.selected)
    assert selected.definition.kind == "initial"


def test_an_assumed_invitation_lets_reminders_go_on(auth_service):
    """Assumed sent counts as the invitation: the latest due reminder is planned."""
    family, actor = third_family(auth_service, invitation="assumed_delivered")
    result = plan(family, actor)
    assert not result.held and result.created == 2 and result.coalesced == 1
    selected = ScheduleOccurrence.objects.get(pk=result.selected)
    assert selected.definition.kind == "reminder"
    assert not ScheduleOccurrence.objects.filter(
        target=f"family:{family.pk}", definition__kind="initial"
    ).exists()
    # Assuming never counts as a provider success.
    assert not ScheduleFulfillment.objects.filter(
        target=f"family:{family.pk}", definition__kind="initial"
    ).exists()


def test_an_undecided_invitation_keeps_the_reminders_back(
    family_mail,  # noqa: F811
):
    """No invitation is planned, and the planned reminder may not be sent."""
    from django.utils import timezone

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.family_mail_dispatch import disposition
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    from .test_family_schedule_planning_postgresql import add_reminders

    harness = activate_response_service(family_mail)
    actor = uuid4()
    complete_empty_catchup(harness.campaign, actor)
    add_reminders(harness.service.store, harness.campaign, actor)
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    with campaign_clock(NOW):
        restore_and_list(actor)
        # Both due reminders are sent again; the invitation stays undecided.
        for hold in RestoreDeliveryHold.objects.filter(
            target=f"family:{family.pk}", definition__kind="reminder"
        ):
            decide(hold, "resend_authorized", actor)
        release(actor)
    result = plan(family, actor)
    assert not result.held and result.coalesced == 1
    occurrence = ScheduleOccurrence.objects.get(pk=result.selected)
    assert occurrence.definition.kind == "reminder"
    assert not ScheduleOccurrence.objects.filter(
        target=f"family:{family.pk}", definition__kind="initial"
    ).exists()
    # The sender's own pre-provider check, on a metadata-only message: no
    # rendering, task or network call is needed to reach it.
    candidate = OutboxMessage(
        id=uuid4(),
        campaign=harness.campaign,
        family=family,
        mode="production",
        semantic_key=occurrence.pk,
        purpose="reminder",
        not_before=timezone.now(),
    )
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        work_transaction(),
        pytest.raises(FamilyDeliveryHeld, match="initial recovery review"),
    ):
        disposition(candidate)


@pytest.mark.parametrize("decision", [None, "assumed_delivered", "resend_authorized"])
def test_a_message_prepared_before_the_backup_follows_the_decision(
    family_mail,  # noqa: F811
    decision,
):
    """Undecided: it waits. Assumed sent: it is cancelled. Sent again: once."""
    family_mail = activate_response_service(family_mail)
    actor = uuid4()
    complete_empty_catchup(family_mail.campaign, actor)
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due + timedelta(hours=1)):
        message = prepare(family_mail)
        begin_review(
            backup_at=due + timedelta(minutes=30),
            reason="restore",
            correlation_id=uuid4(),
        )
        # This Family's invitation, and the other Family's (held whatever
        # its restored eligibility).
        assert list_held_emails(actor_id=actor, correlation_id=uuid4()) == 2
        hold = RestoreDeliveryHold.objects.get(target=f"family:{message.family_id}")
        if decision:
            decide(hold, decision, actor)
        release(actor)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            arguments = dict(
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            if decision is None:
                with pytest.raises(FamilyDeliveryHeld, match="restore review"):
                    begin_submission(message.pk, execution.claim, **arguments)
            elif decision == "assumed_delivered":
                assert (
                    begin_submission(message.pk, execution.claim, **arguments) is None
                )
            else:
                mail, _, _, attempt = begin_submission(
                    message.pk, execution.claim, **arguments
                )
                assert attempt == 1 and mail.recipients == ("valid@example.org",)
                finish_submission(
                    message.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )
    message.refresh_from_db()
    assert (
        message.state
        == {
            None: "pending",
            "assumed_delivered": "cancelled",
            "resend_authorized": "delivered",
        }[decision]
    )
    assert ScheduleFulfillment.objects.filter(disposition="delivered").count() == (
        1 if decision == "resend_authorized" else 0
    )


def prepared(harness):
    """Plan and prepare the Family's next due email; return its message."""
    before = set(FamilyMailPreparation.objects.values_list("pk", flat=True))
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        FamilyScheduleProducer(uuid4())(guard)
    ticket = FamilyMailPreparation.objects.exclude(pk__in=before).get()
    owner = handler(harness)
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            ticket.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={PREPARE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    return OutboxMessage.objects.get(semantic_key=ticket.occurrence_id)


def submit(harness, message):
    """Claim a prepared message and begin its submission; return the claim."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        started = begin_submission(
            message.pk,
            execution.claim,
            private=harness.rings.private,
            public_origin="http://localhost:8000",
        )
    return execution.claim if started else None


def finish(message, claim_, status=Status.ACCEPTED):
    """Record the provider's outcome of a submission begun by ``submit``."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        finish_submission(message.pk, claim_, FamilyDeliveryResult(status, 1))


def live(message):
    """The SQL Family dispatch check on one message, as the sender sees it."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_family_dispatch_live_v1(%s)", [message.pk])
        return cursor.fetchone()[0]


def held_reminder(harness, *, decide_first):
    """Two reminders after a delivered invitation; the first one is held.

    The invitation reaches the Family; a restore then holds the first
    reminder (due after the backup), decided ``decide_first`` during the
    review (None leaves it undecided), and the site is released. Returns the
    Family, both reminder definitions and the actor.
    """
    harness = activate_response_service(harness)
    actor = uuid4()
    complete_empty_catchup(harness.campaign, actor)
    invitation = ScheduleDefinition.objects.get().current_revision.due_at
    first_due = add_reminder(harness, "2054-10-03")
    add_reminder(harness, "2054-10-04")
    first, second = ScheduleDefinition.objects.filter(kind="reminder").order_by(
        "current_revision__due_at"
    )
    initialize_key_inventories(harness.rings.private)
    with campaign_clock(invitation):
        message = prepared(harness)
        finish(message, submit(harness, message))
    with campaign_clock(first_due + timedelta(hours=1)):
        begin_review(
            backup_at=first_due - timedelta(minutes=30),
            reason="restore",
            correlation_id=uuid4(),
        )
        list_held_emails(actor_id=actor, correlation_id=uuid4())
        hold = RestoreDeliveryHold.objects.get(
            definition=first, target=f"family:{message.family_id}"
        )
        if decide_first:
            decide(hold, decide_first, actor)
        release(actor)
    family = FamilyCampaign.objects.get(pk=message.family_id)
    return harness, family, hold, first, second


@pytest.mark.parametrize("removed", [False, True])
def test_a_resent_reminder_is_never_sent_after_a_later_one(
    family_mail,  # noqa: F811
    removed,
):
    """Sent again, then a later reminder reaches the Family: the earlier one
    is cancelled, by the sender's check and by the SQL dispatch check, even
    once the later schedule was removed (#757)."""
    harness, family, _, first, second = held_reminder(
        family_mail, decide_first="resend_authorized"
    )
    with campaign_clock(first.current_revision.due_at + timedelta(hours=1)):
        message = prepared(harness)
        assert ScheduleOccurrence.objects.get(pk=message.semantic_key).definition == (
            first
        )
        assert live(message) is True
        # The later reminder reaches the Family before this one is sent.
        restored_occurrence(second, family)
        if removed:
            remove_schedule(second)
        assert live(message) is False
        assert submit(harness, message) is None
    message.refresh_from_db()
    assert (message.state, message.reason) == ("cancelled", "restore_superseded")
    assert not ScheduleFulfillment.objects.filter(definition=first).exists()


def test_send_again_is_refused_while_a_later_reminder_is_on_its_way(
    family_mail,  # noqa: F811
):
    """A later reminder being submitted, or with an unknown outcome, already
    makes "send again" on the earlier one out of order (#757)."""
    harness, family, hold, first, second = held_reminder(family_mail, decide_first=None)
    with campaign_clock(second.current_revision.due_at):
        # The held first reminder is skipped; the second one is planned.
        later = prepared(harness)
        assert ScheduleOccurrence.objects.get(pk=later.semantic_key).definition == (
            second
        )
        claim_ = submit(harness, later)
        later.refresh_from_db()
        assert later.state == "submitting"

        def refused():
            """Both the owner and a direct insert refuse to send it again."""
            with pytest.raises(ValueError, match="later reminder"):
                decide(hold, "resend_authorized", uuid4())
            with pytest.raises(DatabaseError, match="later reminder reached"):
                RestoreHoldResolution.objects.create(
                    hold=hold,
                    version=2,
                    state="resend_authorized",
                    evidence="Send again.",
                    actor_id=uuid4(),
                    correlation_id=uuid4(),
                )

        refused()
        finish(later, claim_, Status.UNKNOWN)
        later.refresh_from_db()
        assert later.state == "delivery_unknown"
        refused()
        hold.refresh_from_db()
        assert hold.state == "unreviewed"
        # It can still be assumed sent.
        decide(hold, "assumed_delivered", uuid4())
    hold.refresh_from_db()
    assert hold.state == "assumed_delivered"
