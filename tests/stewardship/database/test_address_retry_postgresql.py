"""A delivered email's follow-up to addresses refused for now (#806, migration 0077).

The real mail worker sends a Production invitation to a Family with two
addresses; the fake provider accepts it but refuses one address for now. The
email is delivered and is never sent again; a follow-up row and its own
``family_address_retry`` task are opened in the same transaction, and the
real follow-up handler later sends the very same email (same code and link,
same rendering) to the refused address only. These tests also prove the
guard (frozen migration 0077) refuses every other write, that an abandoned
follow-up becomes "unknown" and is never resent, that the Mail message page
reports what the follow-up did, and run the file's DO block.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.campaigns.credential_models import FamilyAccessToken
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import address_retry, family_mail_delivery_tasks
from parishkit.stewardship.jobs.address_retry_models import AddressRetry
from parishkit.stewardship.jobs.dispatch import claim_hint, recover_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
    family_owner,
)
from .test_recipient_suppressions_postgresql import refresh

pytestmark = pytest.mark.django_db(transaction=True)
FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations"
    / "0077_address_retry.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
ADDRESSES = ("valid@example.org", "zother@example.org")
FOLLOW_UPS = (
    "SELECT envelope_position, outcome"
    " FROM stewardship_delivery_address_retries_v1(%s,%s)"
)


def two_addresses(harness):
    """A Production campaign whose Family head has both ADDRESSES."""
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    source = response_source()
    source.members[3]["emailAddress"] = "; ".join(ADDRESSES)
    refresh(harness, source)
    return harness


def partly_accepted(dispatch, monkeypatch):
    """Deliver the invitation, accepted except for the second address.

    Returns the harness, the credential path, the message and the mail the
    provider was given. Follow-ups are due one second after they open.
    """
    harness, path = dispatch
    harness = two_addresses(harness)
    sent = []

    def provider(value, settings, mail, **kwargs):
        sent.append(mail)
        return FamilyDeliveryResult(Status.ACCEPTED, 2, transient=(1,))

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    monkeypatch.setattr(address_retry, "RECIPIENT_RETRY_SECONDS", (1, 1, 1, 1))
    message = prepare(harness)
    deliver(harness, path, message)
    message.refresh_from_db()
    (mail,) = sent
    return harness, path, message, mail


def follow_up_owner(harness, path):
    """The installed follow-up handler, sharing the Family handler's circuit."""
    family_owner(harness, path)
    return address_retry.retry_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
        circuit=family_mail_delivery_tasks.FAMILY_CIRCUIT,
    )


def wait_until_due(task_id, limit=10):
    """Wait until PostgreSQL's own clock says the follow-up task is due."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_sleep(GREATEST(0, extract(epoch FROM not_before"
            " - clock_timestamp()))+0.05) FROM stewardship_task_run WHERE id=%s",
            [task_id],
        )


def run_follow_up(owner, retry):
    """One mail consumer takes the follow-up's hint and runs it."""
    wait_until_due(retry.task_id)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            retry.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={address_retry.TASK_TYPE: owner},
        )
        assert execution is not None
        with maintain_execution(execution):
            owner.execute(execution)
    retry.refresh_from_db()
    return retry


def follow_ups_as_web(message_id, actor_id):
    """The Mail message page's follow-up read, as the web login."""
    with task_login(ServiceRole.WEB, exact=True), connection.cursor() as cursor:
        cursor.execute(FOLLOW_UPS, (message_id, actor_id))
        return cursor.fetchall()


def tokens():
    """Every Family link token's identity and fingerprint, to show none changed."""
    return sorted(FamilyAccessToken.objects.values_list("id", "digest", "destroyed_at"))


def test_the_refused_address_alone_gets_the_same_email_again(
    dispatch_worker,  # noqa: F811
    google,
    monkeypatch,
):
    """Accepted for one address and refused for now for the other: the
    email is delivered and never sent again; its follow-up sends the same
    email (code, link, rendering) to the refused address only, and nothing
    about the Family's credentials changes."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, first = partly_accepted(dispatch_worker, monkeypatch)
        browser, _ = signed_in()
        admin = PortalUser.objects.get(email="admin@example.org").pk
        assert first.recipients == ADDRESSES
        assert message.state == "delivered" and message.sealed_substitutions is None
        retry = AddressRetry.objects.get()
        event = OutboxEvent.objects.get(message=message, previous_state="submitting")
        assert (retry.message_id, retry.source_event_id, retry.render_id) == (
            message.pk,
            event.pk,
            message.render_id,
        )
        assert retry.positions == retry.remaining == [1] and retry.refused == []
        assert retry.state == "waiting" and retry.sealed_substitutions is not None
        task = TaskRun.objects.get(pk=retry.task_id)
        assert (task.task_type, task.state, task.domain_request_id) == (
            address_retry.TASK_TYPE,
            "queued",
            retry.pk,
        )
        assert follow_ups_as_web(message.pk, admin) == [(1, "retrying")]
        before = (tokens(), message.version, OutboxEvent.objects.count())
        again = []

        def provider(value, settings, mail, **kwargs):
            again.append(mail)
            return FamilyDeliveryResult(Status.ACCEPTED, len(mail.recipients))

        monkeypatch.setattr(address_retry, "submit_family", provider)
        retry = run_follow_up(follow_up_owner(harness, path), retry)
    (second,) = again
    assert second.recipients == (ADDRESSES[1],)
    # The same email: rendering, code and link; only the envelope differs.
    assert (second.subject, second.html, second.text, second.semantic_key) == (
        first.subject,
        first.html,
        first.text,
        first.semantic_key,
    )
    assert harness.code in second.text and "/access/" in second.text
    assert retry.state == "finished" and retry.remaining == []
    assert retry.sealed_substitutions is None and retry.attempt == 1
    assert TaskRun.objects.get(pk=retry.task_id).state == "succeeded"
    message.refresh_from_db()
    assert before == (tokens(), message.version, OutboxEvent.objects.count())
    assert (
        OutboxMessage.objects.count() == 1 and ScheduleOccurrence.objects.count() == 1
    )
    assert follow_ups_as_web(message.pk, admin) == [(1, "delivered")]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        html = browser.get(f"/admin/mail/sent/{message.pk}/").content.decode()
    assert f"To: {ADDRESSES[0]} (delivered), {ADDRESSES[1]} (delivered)</p>" in html
    assert 'id="email-reached"' not in html


def test_still_refused_waits_and_never_resends_the_accepted_address(
    dispatch_worker,  # noqa: F811
    google,
    monkeypatch,
):
    """Refused for now again: the follow-up waits and keeps only that
    address; the page says it is still being tried."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, _ = partly_accepted(dispatch_worker, monkeypatch)
        browser, _ = signed_in()
        sent = []

        def provider(value, settings, mail, **kwargs):
            sent.append(mail.recipients)
            return FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,))

        monkeypatch.setattr(address_retry, "submit_family", provider)
        owner = follow_up_owner(harness, path)
        retry = run_follow_up(owner, AddressRetry.objects.get())
        assert retry.state == "waiting" and retry.remaining == [1]
        assert retry.outcome == {
            "status": "transient",
            "permanent": [],
            "transient": [1],
        }
        assert TaskRun.objects.get(pk=retry.task_id).state == "retry_wait"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            html = browser.get(f"/admin/mail/sent/{message.pk}/").content.decode()
        assert f"{ADDRESSES[1]} (refused for now, trying again)" in html
        assert '<p id="email-reached">Delivered to 1 of 2 addresses.</p>' in html
        # The About panel explains the automatic retry.
        assert 'data-about-page="mail-message"' in html
        assert "is tried again automatically for that address only" in html
        retry = run_follow_up(owner, retry)
    assert sent == [(ADDRESSES[1],), (ADDRESSES[1],)]
    assert retry.attempt == 2 and retry.state == "waiting"


def test_a_changed_address_ends_the_follow_up_unsent(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """When the refused address is no longer the Family's, nothing is sent
    and the follow-up is cancelled."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, _, _ = partly_accepted(dispatch_worker, monkeypatch)
        source = response_source()
        source.members[3]["emailAddress"] = ADDRESSES[0]
        refresh(harness, source)

        def provider(*args, **kwargs):
            raise AssertionError("A cancelled follow-up must not send.")

        monkeypatch.setattr(address_retry, "submit_family", provider)
        retry = run_follow_up(
            follow_up_owner(harness, path), AddressRetry.objects.get()
        )
    assert (retry.state, retry.reason) == ("cancelled", "recipients_changed")
    assert retry.sealed_substitutions is None and retry.attempt == 0
    assert TaskRun.objects.get(pk=retry.task_id).state == "cancelled"


def test_an_abandoned_follow_up_is_unknown_and_never_resent(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A worker lost after "submitting" commits leaves the outcome unknown:
    recovery records that, after the provider deadline, and never sends."""
    from .test_taskrun_postgresql import act, expire

    monkeypatch.setattr(address_retry, "PROVIDER_SECONDS", 1)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, _, _ = partly_accepted(dispatch_worker, monkeypatch)
        retry = AddressRetry.objects.get()
        owner = follow_up_owner(harness, path)
        wait_until_due(retry.task_id)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim_hint(
                retry.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={address_retry.TASK_TYPE: owner},
            )
            claim = execution.claim
            with work_transaction():
                row = address_retry.bound_retry(_status(lock_task_claim(claim)))
                address_retry.retry_mail(
                    row, claim, private=harness.rings.private, public_origin="x:"
                )
                address_retry._change(
                    row,
                    actor_id=claim.worker_id,
                    correlation_id=claim.run_id,
                    state="submitting",
                    attempt=1,
                    run_id=claim.run_id,
                    task_fence=claim.fence,
                    worker_id=claim.worker_id,
                    submitted_at=address_retry.database_now(),
                    provider_deadline=address_retry.database_now()
                    + address_retry.timedelta(seconds=1),
                )
        running = act(
            _status(TaskRun.objects.get(pk=retry.task_id)), "heartbeat", lease_seconds=1
        )
        expire(running)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert recover_hint(
                retry.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={address_retry.TASK_TYPE: owner},
            )
    retry.refresh_from_db()
    assert (retry.state, retry.reason) == ("unknown", "recovery_unknown")
    assert retry.remaining == [1] and retry.sealed_substitutions is None
    assert TaskRun.objects.get(pk=retry.task_id).state == "failed"


def raises(statement, parameters=(), service=None):
    """The SQLSTATE a statement raises, as ``service``'s login or the owner."""

    def run():
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(statement, parameters)
        except DatabaseError as error:
            return error.__cause__.sqlstate
        return None

    if service is None:
        return run()
    with task_login(service, exact=True):
        return run()


def test_the_guard_refuses_every_other_write(dispatch_worker, monkeypatch):  # noqa: F811
    """Positions never grow, nothing is deleted, no login inserts a row or
    a task, and mail dispatch changes a row only under its live claim."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        _, _, _, _ = partly_accepted(dispatch_worker, monkeypatch)
    retry = AddressRetry.objects.get()
    update = (
        "UPDATE stewardship_outbox_address_retry SET version=version+1, {} WHERE id=%s"
    )
    # Even the owner cannot add a position back or delete the row.
    assert raises(update.format("remaining='[0,1]'"), [retry.pk]) == "23514"
    assert raises(update.format("positions='[0]'"), [retry.pk]) == "23514"
    assert raises(update.format("refused='[0]'"), [retry.pk]) == "23514"
    assert raises("DELETE FROM stewardship_outbox_address_retry") == "23514"
    # Mail dispatch without the work-order lock and a claim of the row's own
    # task (the guard refuses before anything else).
    assert (
        raises(
            update.format(
                "state='cancelled', reason='x', sealed_substitutions=NULL,"
                " sealed_key_id=NULL, finished_at=now()"
            ),
            [retry.pk],
            ServiceRole.MAIL_DISPATCH,
        )
        == "23514"
    )
    # No login may insert a follow-up or its task type.
    insert_task = (
        "INSERT INTO stewardship_task_run(id,root_id,version,retry_sequence,task_type,"
        "domain_request_id,initiated_by_id,actor_id,correlation_id)"
        " VALUES(%s,%s,1,0,'family_address_retry',%s,%s,%s,%s)"
    )
    identity = uuid4()
    for service in (ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.SCHEDULER):
        assert (
            raises(
                insert_task,
                [identity, identity, retry.pk, identity, identity, identity],
                service,
            )
            == "42501"
        )
    # Only mail dispatch may open one, and only under a live delivery claim.
    opening = "SELECT stewardship_address_retry_open_v1(%s,%s,1,%s,'x','k',1)"
    values = [retry.source_event_id, uuid4(), uuid4()]
    assert raises(opening, values, ServiceRole.WEB) == "42501"
    assert raises(opening, values, ServiceRole.MAIL_DISPATCH) == "23514"
    retry.refresh_from_db()
    assert retry.state == "waiting" and retry.version == 1


def test_the_migration_check_passes_installed_and_fails_otherwise():
    """The DO block accepts the installed objects and refuses a missing one."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)
    with (
        pytest.raises(DatabaseError, match="0077"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DROP FUNCTION public.stewardship_address_retry_token_v1"
            "(uuid,uuid,uuid,bigint,uuid)"
        )
        cursor.execute(CHECK)
    with (
        pytest.raises(DatabaseError, match="0077"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER FUNCTION public.stewardship_delivery_address_retries_v1"
            "(uuid,uuid) SECURITY INVOKER"
        )
        cursor.execute(CHECK)


def replica(statement, parameters=()):
    """Test setup only: one write as the owner with triggers off.

    It stands for the state another owner (a restore, a pause, a go-live or
    purge gate, a source refresh) would have written; CHECK constraints
    still apply.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(statement, parameters)


def owner_submits(retry):
    """Commit "submitting" directly as the owner; the SQLSTATE, or None.

    The owner skips the claim checks, so this exercises only the guard's
    scope check for a send, once the follow-up is due. It is rolled back
    either way.
    """
    wait_until_due(retry.task_id)
    statement = (
        "UPDATE stewardship_outbox_address_retry SET version=version+1,"
        " state='submitting', attempt=attempt+1, run_id=%s, task_fence=1,"
        " worker_id=%s, submitted_at=clock_timestamp(),"
        " provider_deadline=clock_timestamp()+interval '30 seconds' WHERE id=%s"
    )
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement, [uuid4(), uuid4(), retry.pk])
            transaction.set_rollback(True)
    except DatabaseError as error:
        assert "scope does not admit a send" in str(error.__cause__)
        return error.__cause__.sqlstate
    return None


def never_sends(monkeypatch):
    """Make any follow-up send fail the test."""

    def provider(*args, **kwargs):
        raise AssertionError("This follow-up must not send.")

    monkeypatch.setattr(address_retry, "submit_family", provider)


# Each gate, the setup write that raises it, and what the follow-up does:
# hold (wait without sending or counting a try) or end with a reason.
GATES = {
    "restore_review": (
        "UPDATE stewardship_system_configuration SET restore_review_required=true",
        None,
    ),
    "paused": (
        "UPDATE stewardship_campaign SET delivery_paused=true,"
        " paused_at=clock_timestamp(), pause_actor_id=gen_random_uuid(),"
        " pause_version=pause_version+1",
        None,
    ),
    "go_live_gate": (
        "UPDATE stewardship_campaign_credentials SET go_live_gate=true",
        None,
    ),
    "population_dirty": (
        "UPDATE stewardship_campaign_credentials SET population_dirty=true",
        None,
    ),
    "purge_gate": (
        "INSERT INTO stewardship_campaign_work_gate(id,version,request_id,state,"
        "campaign_id,correlation_id) SELECT gen_random_uuid(),1,gen_random_uuid(),"
        "'running',id,gen_random_uuid() FROM stewardship_campaign",
        None,
    ),
    "restored": (
        "UPDATE stewardship_system_configuration SET"
        " restore_activated_at=clock_timestamp()",
        "restored",
    ),
    "production_cycle": (
        "UPDATE stewardship_campaign SET production_cycle=production_cycle+1",
        "scope_replaced",
    ),
    "undeliverable": (
        "UPDATE stewardship_family_campaign SET email_deliverable=false",
        "recipients_changed",
    ),
    "token_gone": (
        "UPDATE stewardship_family_token SET destroyed_at=clock_timestamp(),"
        " ciphertext=NULL, digest=NULL",
        "credentials_changed",
    ),
}


@pytest.mark.parametrize("gate", sorted(GATES))
def test_each_gate_holds_or_ends_the_follow_up_unsent(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    gate,
):
    """The follow-up sends only where the message itself could be sent: a
    gate holds it (no send, no try counted) and the SQL guard refuses a
    send; a changed scope ends it unsent. A follow-up restored from a
    backup is never sent again, since it may have been sent after that
    backup was taken."""
    statement, ending = GATES[gate]
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, _, _ = partly_accepted(dispatch_worker, monkeypatch)
        retry = AddressRetry.objects.get()
        assert owner_submits(retry) is None
        replica(statement)
        never_sends(monkeypatch)
        if gate != "token_gone":
            assert owner_submits(retry) == "23514"
        retry = run_follow_up(follow_up_owner(harness, path), retry)
    task = TaskRun.objects.get(pk=retry.task_id)
    assert retry.attempt == 0
    if ending is None:
        assert retry.state == "waiting" and task.state == "retry_wait"
        assert retry.sealed_substitutions is not None
    else:
        assert (retry.state, retry.reason) == ("cancelled", ending)
        assert retry.sealed_substitutions is None and task.state == "cancelled"


@pytest.mark.parametrize("hold", ["stopped", "daily_cap"])
def test_the_mail_circuit_and_daily_cap_hold_the_follow_up(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    hold,
):
    """A stopped or paused circuit and the daily cap hold it like any
    Family mail, without counting a try."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, _, _ = partly_accepted(dispatch_worker, monkeypatch)
        owner = follow_up_owner(harness, path)
        never_sends(monkeypatch)
        if hold == "stopped":
            circuit = family_mail_delivery_tasks.FAMILY_CIRCUIT
            monkeypatch.setattr(circuit, "blocks_new_send", lambda: True)
        else:
            monkeypatch.setattr(address_retry, "over_daily_limit", lambda *a: True)
        retry = run_follow_up(owner, AddressRetry.objects.get())
    assert retry.state == "waiting" and retry.attempt == 0
    assert TaskRun.objects.get(pk=retry.task_id).state == "retry_wait"


def later_reminder(harness, family_id, *, accepted):
    """Another reminder to the Family, accepted or left submitting."""
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction
    from parishkit.stewardship.jobs.family_mail_results import result_evidence
    from parishkit.stewardship.jobs.outbox_storage import create_message
    from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity

    from ..test_outbox_validation import rendering
    from .test_outbox_postgresql import change, permit, submit

    key = uuid4()
    later = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family_id,
            semantic_key=key,
            mode="production",
            routing="production",
            purpose="reminder",
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=ADDRESSES,
            routed_recipients=ADDRESSES,
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    submitted = submit(later)
    if accepted:
        change(
            submitted,
            DeliveryAction.ACCEPT,
            evidence=result_evidence(
                FamilyDeliveryResult(Status.ACCEPTED, 2),
                semantic_key=key,
                with_stats=False,
            ),
        )


@pytest.mark.parametrize("accepted", [True, False])
def test_a_later_email_supersedes_or_holds_the_follow_up(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    accepted,
):
    """Once a later invitation or reminder reached the Family, the older
    email is not sent again; while another email's provider outcome is
    unresolved, the follow-up waits, as the message itself would."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, _ = partly_accepted(dispatch_worker, monkeypatch)
        later_reminder(harness, message.family_id, accepted=accepted)
        retry = AddressRetry.objects.get()
        assert owner_submits(retry) == "23514"
        never_sends(monkeypatch)
        retry = run_follow_up(follow_up_owner(harness, path), retry)
    if accepted:
        assert (retry.state, retry.reason) == ("cancelled", "superseded")
    else:
        assert retry.state == "waiting" and retry.attempt == 0
        assert TaskRun.objects.get(pk=retry.task_id).state == "retry_wait"


def test_the_token_read_answers_only_its_own_live_claim(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """The link token comes back only for the follow-up's own Family, under
    its live claim; another Family's token or a stale fence gets nothing."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, _ = partly_accepted(dispatch_worker, monkeypatch)
        retry = AddressRetry.objects.get()
        owner = follow_up_owner(harness, path)
    token = FamilyAccessToken.objects.get(
        family_id=message.family_id,
        generation_id=retry.token_generation_id,
        destroyed_at__isnull=True,
    )
    other = (
        FamilyAccessToken.objects.exclude(family_id=message.family_id)
        .values_list("id", flat=True)
        .first()
        or uuid4()
    )
    read = "SELECT count(*) FROM stewardship_address_retry_token_v1(%s,%s,%s,%s,%s)"
    wait_until_due(retry.task_id)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim_hint(
            retry.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={address_retry.TASK_TYPE: owner},
        )
        claim = execution.claim

        def rows(token_id, fence=claim.fence):
            with connection.cursor() as cursor:
                cursor.execute(
                    read, [retry.pk, token_id, claim.run_id, fence, claim.worker_id]
                )
                return cursor.fetchone()[0]

        assert rows(token.pk) == 1
        assert rows(other) == 0
        assert rows(token.pk, fence=claim.fence + 1) == 0
        assert rows(token.pk) == 1


def test_a_follow_up_that_cannot_open_leaves_the_acceptance(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    caplog,
):
    """Opening is a savepoint: a refusal is logged at ERROR, and the email
    is still recorded as delivered."""
    monkeypatch.setattr(address_retry, "RECIPIENT_RETRY_SECONDS", (0, 0, 0, 0))
    harness, path = dispatch_worker
    harness = two_addresses(harness)

    def provider(value, settings, mail, **kwargs):
        return FamilyDeliveryResult(Status.ACCEPTED, 2, transient=(1,))

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert message.state == "delivered" and not AddressRetry.objects.exists()
    assert TaskRun.objects.get(pk=message.task_id).state == "succeeded"
    assert any(
        record.levelname == "ERROR" and "refused for now" in record.getMessage()
        for record in caplog.records
    )
