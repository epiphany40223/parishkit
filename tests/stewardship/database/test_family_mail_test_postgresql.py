"""Chosen-Family Testing sends: real Admin intake, worker preparation and MAIL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts import campaign_family_test as intake
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.campaigns.cleanup_catalog import (
    CleanupCategory,
    inventory_queries,
)
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_content import (
    CODE_PLACEHOLDER,
    open_family_credentials,
)
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.family_mail_dispatch_recovery import (
    cancel_abandoned_family_test,
)
from parishkit.stewardship.jobs.family_mail_models import FamilyMailTest
from parishkit.stewardship.jobs.family_mail_test_tasks import (
    TASK_TYPE,
    family_test_handler,
    recover_pending,
)
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import _status as delivery_status
from parishkit.stewardship.jobs.outbox_validation import (
    RenderInput,
    SealedSubstitutions,
)
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from ..content_factory import content
from . import campaign_builders
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login
from .test_family_mail_dispatch_postgresql import claim
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)
KEY = b"synthetic-installed-workspace-credential"
ORIGIN = "https://parish.example.org"


@pytest.fixture
def family_test(request, monkeypatch, google):
    """Source-backed Families, an applied invitation template and public mail identity.

    The root configuration publishes the sender and the Workspace fingerprint;
    no provider credential is mounted anywhere in these tests.
    """
    original = campaign_builders.configuration_document

    def document():
        value = original()
        value["sections"]["integrations"] += [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": "sender@example.org"},
                    "credential_fingerprint": file_fingerprint(KEY),
                },
            },
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "email",
                    "settings": {
                        "sender": "sender@example.org",
                        "reply_to": "reply@example.org",
                    },
                    "credential_fingerprint": None,
                },
            },
        ]
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    harness = request.getfixturevalue("response_service")
    definition = ScheduleDefinition.objects.get()
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot="initial",
        html="<p>Hello {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
        text="Hello {{ family_name }}: {{ family_code }} {{ family_url }}",
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **template},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {
                        "template_version": template["id"],
                        "subject": template["values"]["subject"],
                    },
                },
            ],
        ).state
        == "applied"
    )
    browser, response = signed_in()
    assert response.status_code == 302
    path = f"/admin/campaign/{harness.campaign.pk}/content/test/{template['id']}"
    return harness, browser, path + "/families", path


def review(browser, path, duids):
    """Submit DUIDs for review; the response carries the signed review token."""
    with web_login():
        page = post(
            browser,
            path,
            {"action": "preview", "families": "\n".join(str(duid) for duid in duids)},
        )
    assert page.status_code == 200, page.content
    return page


def request_tickets(browser, path, duids):
    """Review then confirm through real HTTP, CSRF, session and Web SQL identity."""
    page = review(browser, path, duids)
    token = page.context["confirm"]["preview"].value()
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
    assert response.status_code == 302, response.content
    return list(FamilyMailTest.objects.order_by("created_at", "sequence")), token


def prepare_tests(harness):
    """Run the real general worker for every queued ticket; no provider is touched."""
    owner = family_test_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin=ORIGIN,
    )
    for ticket in FamilyMailTest.objects.filter(state="queued").order_by("sequence"):
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(
                ticket.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: owner},
            )
            with maintain_execution(execution):
                owner.execute(execution)
    return list(
        OutboxMessage.objects.filter(purpose="family_test")
        .select_related("render")
        .order_by("created_at")
    )


def deliver(harness, message, status=Status.ACCEPTED):
    """Submit through the restricted MAIL role with a synthetic provider outcome."""
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )

    initialize_key_inventories(harness.rings.private)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        prepared = begin_submission(
            message.pk,
            execution.claim,
            private=harness.rings.private,
            public_origin=ORIGIN,
        )
        if prepared is None:
            message.refresh_from_db()
            return None
        mail, *_ = prepared
        finish_submission(
            message.pk,
            execution.claim,
            FamilyDeliveryResult(status, len(mail.recipients)),
        )
    message.refresh_from_db()
    return mail


def age_session(delta):
    """Move the single Admin session's Google sign-in back or forward in time.

    ``authenticated_at`` is immutable to every runtime login, so this is a
    disposable schema-owner fixture edit with the row's guards suspended only
    for this statement; every application check afterwards runs fully guarded.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE stewardship_portal_session DISABLE TRIGGER USER")
        cursor.execute(
            "UPDATE stewardship_portal_session "
            "SET authenticated_at=authenticated_at-%s",
            [delta],
        )
        cursor.execute("ALTER TABLE stewardship_portal_session ENABLE TRIGGER USER")


def code_of(harness, message):
    """Open the sealed reference with the private key MAIL would use."""
    retained = message.render
    render = RenderInput(
        **{
            field: getattr(retained, field)
            for field in RenderInput.__dataclass_fields__
        }
    )
    return open_family_credentials(
        identity=delivery_status(message).identity,
        render=render,
        sealed=SealedSubstitutions(message.sealed_substitutions, None, None),
        private=harness.rings.private,
    ).code


def test_chosen_family_gets_its_own_code_and_only_the_testing_recipient(family_test):
    """Intake, preparation and dispatch reuse the Family's existing Testing credential.

    The message is bound to the ticket, so it neither creates nor satisfies the
    Family's scheduled invitation, which planning still allocates afterwards.
    """
    harness, browser, path, sample = family_test
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    with web_login():
        page = browser.get(sample)
        assert page.status_code == 200 and page.context["families_url"] == path
        page = browser.get(path)
        assert page.status_code == 200 and page.context["epoch_ready"]
    tickets, token = request_tickets(browser, path, [1])
    assert len(tickets) == 1 and tickets[0].state == "queued"
    assert tickets[0].family_id == family.pk and tickets[0].outbox_id is None
    assert TaskRun.objects.get(pk=tickets[0].task_id).task_type == TASK_TYPE
    with web_login():
        # Replaying the same review returns the original tickets, no second task.
        assert (
            post(
                browser,
                path,
                {"action": "confirm", "preview": token, "acknowledge": "on"},
            ).status_code
            == 302
        )
    assert FamilyMailTest.objects.count() == 1
    occurrences = ScheduleOccurrence.objects.count()
    (message,) = prepare_tests(harness)
    ticket = FamilyMailTest.objects.get()
    assert ticket.state == "prepared" and ticket.family_id is None
    assert ticket.outbox_id == message.pk and message.semantic_key == ticket.pk
    assert TaskRun.objects.get(pk=ticket.task_id).state == "succeeded"
    assert message.family_id == family.pk and message.state == "pending"
    assert message.render.routed_recipients == ["test@example.org"]
    assert message.render.intended_recipients == ["valid@example.org"]
    assert message.render.subject.startswith("[TEST] ")
    assert CODE_PLACEHOLDER in message.render.html
    assert harness.code not in message.render.html
    assert RehearsalCredential.objects.count() == 1
    assert code_of(harness, message) == harness.code
    assert ScheduleOccurrence.objects.count() == occurrences
    mail = deliver(harness, message)
    assert mail.recipients == ("test@example.org",)
    assert harness.code in mail.text and "/access/test." in mail.text
    assert message.state == "delivered" and message.sealed_substitutions is None
    assert ScheduleOccurrence.objects.count() == occurrences
    assert not ScheduleFulfillment.objects.exists()
    with web_login():
        page = browser.get(path)
    assert page.status_code == 200
    (item,) = page.context["items"]
    assert item["duid"] == 1 and item["message_state"] == "delivered"
    # The real scheduled invitation is still due and still allocated later.
    from .test_family_mail_preparation_postgresql import allocate

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        allocate()
    assert ScheduleOccurrence.objects.filter(
        target=f"family:{family.pk}", state="pending"
    ).exists()


def test_worker_issues_a_missing_credential_that_signs_in_only_within_dates(
    family_test,
):
    """Without an epoch the form refuses; with one the worker creates the credential."""
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
        release_rehearsal_gate,
    )
    from parishkit.stewardship.jobs.family_mail_epochs import (
        ensure_preparation_epoch,
    )
    from parishkit.stewardship.jobs.scheduler import scheduler_session

    harness, browser, path, _ = family_test
    campaign = harness.campaign
    previous = invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda _: True)
    while cleanup_rehearsal(previous):
        pass
    release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda _: True)
    assert not RehearsalCredential.objects.exists()
    page = review(browser, path, [1])
    assert not page.context["epoch_ready"] and page.context["confirm"] is None
    assert not FamilyMailTest.objects.exists()
    starts_at = campaign.active_configuration.starts_at
    with (
        campaign_clock(starts_at),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        ensure_preparation_epoch(guard, campaign.pk, uuid4())
    epoch_id = CampaignCredentialState.objects.get(campaign=campaign).rehearsal_epoch_id
    assert epoch_id is not None and epoch_id != previous
    (ticket,), _ = request_tickets(browser, path, [1])
    assert ticket.rehearsal_epoch_id == epoch_id
    (message,) = prepare_tests(harness)
    credential = RehearsalCredential.objects.get()
    assert credential.epoch_id == epoch_id
    assert message.rehearsal_epoch_id == epoch_id
    code = code_of(harness, message)
    assert code != harness.code and code.startswith("I")
    # Before the start date the portal offers no sign-in at all; the code is
    # not a bypass. Within the dates the same code opens a Testing session.
    from django.test import Client

    from parishkit.stewardship.campaigns.credential_models import FamilySession

    client = Client(enforce_csrf_checks=True)
    with campaign_clock(starts_at):
        assert client.get("/").status_code == 200
    csrf = client.cookies["pk_family_csrf"].value
    with campaign_clock(starts_at - timedelta(days=1)):
        # The portal itself is closed, and a code posted anyway is refused.
        client.get("/")
        assert (
            "pk_family_csrf" not in client.cookies
            or client.cookies["pk_family_csrf"].value == csrf
        )
        response = client.post("/", {"code": code, "csrfmiddlewaretoken": csrf})
        assert response.status_code != 302
        assert not FamilySession.objects.filter(rehearsal_epoch_id=epoch_id).exists()
    with campaign_clock(starts_at):
        _, response = login(code)
        assert response.status_code == 302
    assert FamilySession.objects.filter(rehearsal_epoch_id=epoch_id).exists()


def test_intake_refuses_production_ineligible_families_and_stale_sign_in(
    family_test, monkeypatch
):
    """Web preparation and the SQL ticket guard refuse independently."""
    harness, browser, path, sample = family_test
    page = review(browser, path, [1, 2, 999])
    labels = {
        row["duid"]: (row["eligible"], str(row["label"]))
        for row in page.context["families"]
    }
    assert labels[1][0] and not labels[2][0] and not labels[999][0]
    assert labels[999][1] == "Not a Family in this campaign"
    assert page.context["confirm"] is not None and not page.context["sendable"]
    token = page.context["confirm"]["preview"].value()
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
        assert response.status_code == 409
    # Without the acknowledgement nothing is recorded either.
    page = review(browser, path, [1])
    token = page.context["confirm"]["preview"].value()
    with web_login():
        assert (
            post(browser, path, {"action": "confirm", "preview": token}).status_code
            == 400
        )
    assert not FamilyMailTest.objects.exists()
    # A stale Google sign-in is refused by web, and a forged timestamp is
    # refused by SQL against the actual session record.
    with monkeypatch.context() as patch:
        patch.setattr(
            intake,
            "require_fresh",
            lambda request: (
                request.portal_session.authenticated_at - timedelta(minutes=1)
            ),
        )
        with web_login():
            response = post(
                browser,
                path,
                {"action": "confirm", "preview": token, "acknowledge": "on"},
            )
        assert response.status_code == 503, response.content
    assert not FamilyMailTest.objects.exists()
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()
    # A real session whose Google sign-in is older than five minutes: web
    # refuses, and SQL refuses the genuinely matching but stale timestamp too.
    age_session(timedelta(minutes=10))
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
    assert response.status_code == 403
    with monkeypatch.context() as patch:
        patch.setattr(
            intake,
            "require_fresh",
            lambda request: request.portal_session.authenticated_at,
        )
        with web_login():
            response = post(
                browser,
                path,
                {"action": "confirm", "preview": token, "acknowledge": "on"},
            )
        assert response.status_code == 503, response.content
    assert not FamilyMailTest.objects.exists()
    age_session(-timedelta(minutes=10))
    # Production offers no chosen-Family test at all.
    from .response_builders import activate_response_service

    activate_response_service(harness)
    with web_login():
        assert browser.get(path).status_code == 403
        page = browser.get(sample)
    # An unpaused Production campaign has no fictional sample either, so the
    # page is refused as stale and offers no chosen-Family link anywhere.
    assert page.status_code == 409 and b"/families" not in page.content


def test_at_most_ten_tests_may_be_in_progress_per_campaign(family_test, monkeypatch):
    """The allowance counts queued tickets and unsettled messages together."""
    harness, browser, path, _ = family_test
    for _ in range(10):
        request_tickets(browser, path, [1])
    assert FamilyMailTest.objects.filter(state="queued").count() == 10
    page = review(browser, path, [1])
    assert page.context["available"] == 0 and not page.context["sendable"]
    token = page.context["confirm"]["preview"].value()
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
    assert response.status_code == 409
    # Even if web miscounted, the SQL guard holds the line and rolls back the task.
    monkeypatch.setattr(intake, "FAMILY_TEST_LIMIT", 11)
    tasks = TaskRun.objects.filter(task_type=TASK_TYPE).count()
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
    assert response.status_code == 503
    assert FamilyMailTest.objects.count() == 10
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == tasks


def test_scheduler_settles_stale_tickets_and_worker_cancels_their_tasks(family_test):
    """A revoked Admin cancels queued tickets; a failed task marks its ticket failed."""
    from .test_taskrun_postgresql import act

    harness, browser, path, _ = family_test
    (first,), _ = request_tickets(browser, path, [1])
    (_, second), _ = request_tickets(browser, path, [1])
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 0
    status = act(_status(TaskRun.objects.get(pk=second.task_id)), "claim")
    act(status, "permanent_failure")
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    second.refresh_from_db()
    assert second.state == "failed" and second.family_id is None
    PortalUser.objects.filter(pk=first.requested_by_id).update(
        disabled=True, version=F("version") + 1
    )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    first.refresh_from_db()
    assert first.state == "cancelled" and first.family_id is None
    owner = family_test_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin=ORIGIN,
    )
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            first.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    assert TaskRun.objects.get(pk=first.task_id).state == "cancelled"
    assert not OutboxMessage.objects.filter(purpose="family_test").exists()
    # A restored Administrator sees both settled tickets without a Family.
    PortalUser.objects.filter(pk=first.requested_by_id).update(
        disabled=False, version=F("version") + 1
    )
    with web_login():
        page = browser.get(path)
    assert page.status_code == 200, page.content
    assert {item["state"] for item in page.context["items"]} == {"cancelled", "failed"}
    assert all(item["duid"] is None for item in page.context["items"])


def test_restricted_roles_cannot_write_outside_their_paths(family_test):
    """Web, worker, scheduler and MAIL each own exactly one ticket transition."""
    harness, browser, path, _ = family_test
    (ticket,), _ = request_tickets(browser, path, [1])
    with (
        web_login(),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_family_mail_test SET state='cancelled',"
            "family_id=NULL,version=version+1 WHERE id=%s",
            [ticket.pk],
        )
    assert error.value.__cause__.sqlstate == "42501"
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("INSERT INTO stewardship_family_mail_test DEFAULT VALUES")
    assert error.value.__cause__.sqlstate == "42501"
    # A worker without a live claim for this ticket cannot complete it.
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(IntegrityError, match="live preparation worker"),
        work_transaction(),
    ):
        FamilyMailTest.objects.filter(pk=ticket.pk).update(
            state="prepared",
            family_id=None,
            outbox_id=uuid4(),
            version=F("version") + 1,
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
    # The scheduler cannot settle a ticket whose scope is still live.
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        pytest.raises(IntegrityError, match="stale unsent"),
        work_transaction(),
    ):
        FamilyMailTest.objects.filter(pk=ticket.pk).update(
            state="cancelled", family_id=None, actor_id=None, version=F("version") + 1
        )
    (message,) = prepare_tests(harness)
    family = FamilyCampaign.objects.get(pk=message.family_id)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        from .test_family_mail_preparation_postgresql import allocate

        allocate()
    occurrence = ScheduleOccurrence.objects.get(target=f"family:{family.pk}")
    # A test sender under its real MAIL claim cannot move schedule state.
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        with (
            pytest.raises(IntegrityError, match="fulfillment scope"),
            work_transaction(),
        ):
            ScheduleFulfillment.objects.create(
                definition_id=definition.pk,
                mode="testing",
                target=occurrence.target,
                slot=occurrence.slot,
                disposition="delivered",
                occurrence_id=occurrence.pk,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
            )
        with (
            pytest.raises(IntegrityError, match="occurrence scope"),
            work_transaction(),
        ):
            ScheduleOccurrence.objects.filter(pk=occurrence.pk).update(
                state="running",
                task_id=execution.claim.run_id,
                worker_id=execution.claim.worker_id,
                fence=execution.claim.fence,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
                version=F("version") + 1,
            )
    occurrence.refresh_from_db()
    assert occurrence.state == "pending" and not ScheduleFulfillment.objects.exists()


def test_scope_change_cancels_unsent_test_and_cleanup_inventories_the_rest(
    family_test,
):
    """Go-live invalidation cancels an unsent test; sent ones await cleanup."""
    from parishkit.stewardship.campaigns.cleanup_preview import cleanup_preview
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    with work_transaction():
        assert cleanup_preview(harness.campaign.pk).unresolved
    with work_transaction():
        queries = inventory_queries(harness.campaign.pk)
        assert list(queries[CleanupCategory.OUTBOX].values_list("pk", flat=True)) == [
            message.pk
        ]
        assert queries[CleanupCategory.REHEARSAL].count() == 1
    invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    assert deliver(harness, message) is None
    assert message.state == "cancelled" and message.attempt == 0
    ticket = FamilyMailTest.objects.get()
    assert ticket.state == "prepared" and ticket.family_id is None
    with work_transaction():
        assert not cleanup_preview(harness.campaign.pk).unresolved


def test_revoked_admin_stops_preparation_before_the_sweep(family_test, monkeypatch):
    """The worker rechecks the Admin's authority itself; SQL rechecks it again."""
    harness, browser, path, _ = family_test
    (ticket,), _ = request_tickets(browser, path, [1])
    PortalUser.objects.filter(pk=ticket.requested_by_id).update(
        disabled=True, version=F("version") + 1
    )
    owner = family_test_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin=ORIGIN,
    )
    # Even a worker that believes the scope is live cannot write the message.
    with monkeypatch.context() as patch:
        patch.setattr(
            "parishkit.stewardship.jobs.family_mail_test_tasks.scope_live",
            lambda ticket: True,
        )
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(
                ticket.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: owner},
            )
            with (
                maintain_execution(execution),
                pytest.raises(IntegrityError, match="preparation ownership"),
            ):
                owner.execute(execution)
    assert not OutboxMessage.objects.filter(purpose="family_test").exists()
    task = TaskRun.objects.get(pk=ticket.task_id)
    assert task.state == "running"
    ticket.refresh_from_db()
    assert ticket.state == "queued" and ticket.family_id is not None
    # Unpatched, the worker recognises the lost authority and cancels its task.
    # The claim is still live; a fresh execution lifetime continues it.
    from parishkit.stewardship.jobs.dispatch import Execution

    execution = Execution(execution.claim, owner, execution.correlation_id)
    with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
        owner.execute(execution)
    assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
    assert not OutboxMessage.objects.filter(purpose="family_test").exists()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    ticket.refresh_from_db()
    assert ticket.state == "cancelled" and ticket.family_id is None


def test_temporary_gate_defers_without_charging_or_cancelling(family_test, monkeypatch):
    """Restore review holds a claimed ticket; it is retried once the gate lifts."""
    from threading import Event

    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        preparation_attempts,
    )
    from parishkit.stewardship.jobs.phases import TaskPhase

    from .campaign_builders import restored_runtime

    harness, browser, path, _ = family_test
    (ticket,), _ = request_tickets(browser, path, [1])
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_test_tasks.HELD_RETRY_SECONDS", 1
    )
    owner = family_test_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin=ORIGIN,
    )
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            ticket.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
    with restored_runtime(harness.campaign.active_configuration.starts_at):
        with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
            owner.execute(execution)
        status = _status(TaskRun.objects.get(pk=ticket.task_id))
        assert status.state == "retry_wait" and status.phase is TaskPhase.RECONCILING
        assert preparation_attempts(status) == 0
        ticket.refresh_from_db()
        assert ticket.state == "queued" and ticket.family_id is not None
        # The sweep leaves a temporarily held ticket alone, and a new claim
        # is refused while the gate holds; the scan skips it and comes back.
        with task_login(ServiceRole.SCHEDULER, exact=True):
            assert recover_pending() == 0
        Event().wait(1.1)
        with (
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(PermissionError, match="not admitted"),
        ):
            claim_hint(
                ticket.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: owner},
            )
    assert not OutboxMessage.objects.exists()
    assert TaskRun.objects.get(pk=ticket.task_id).state == "retry_wait"
    (message,) = prepare_tests(harness)
    ticket.refresh_from_db()
    assert ticket.state == "prepared" and ticket.outbox_id == message.pk
    assert TaskRun.objects.get(pk=ticket.task_id).state == "succeeded"


def test_exhausted_dispatch_preparation_cancels_the_unsent_test(
    family_test, monkeypatch, tmp_path
):
    """A test the mail worker can never prepare ends cancelled, never pending."""
    from parishkit.stewardship.accounts.key_files import write_private
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DISPATCH

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    initialize_key_inventories(harness.rings.private)
    credential = tmp_path / "workspace"
    write_private(credential, KEY)

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic rendering failure")

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch_content.current_content",
        broken,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.MAX_ATTEMPTS", 1
    )
    owner = delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin=ORIGIN,
        credential_path=credential,
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            message.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={DISPATCH: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "preparation_failed"
    assert message.attempt == 0 and message.sealed_substitutions is None
    assert TaskRun.objects.get(pk=message.task_id).state == "cancelled"
    from parishkit.stewardship.campaigns.cleanup_preview import cleanup_preview

    with work_transaction():
        assert not cleanup_preview(harness.campaign.pk).unresolved


def test_lost_template_and_lasting_ineligibility_cancel_the_test(family_test):
    """Durable losses after preparation cancel; only reconciliation waits hold."""
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    request_tickets(browser, path, [1])
    first, second = prepare_tests(harness)
    # A clean, current reconciliation that leaves the Family without a
    # deliverable address is not a wait: the Family's real mail would not go.
    data = response_source()
    data.members[3]["emailAddress"] = ""
    refresh(harness, data)
    assert deliver(harness, first) is None
    assert first.state == "cancelled" and first.reason == "family_ineligible"
    refresh(harness, response_source())
    # Replace the schedule's template and remove the original record: the
    # remaining message's ticket names a template the configuration lost.
    definition = ScheduleDefinition.objects.get()
    replacement = content(
        str(harness.campaign.pk),
        kind="email",
        slot="initial",
        html="<p>Replaced {{ family_code }} {{ family_url }}</p>",
        text="Replaced {{ family_code }} {{ family_url }}",
    )
    original = FamilyMailTest.objects.get(outbox_id=second.pk).template.record_id
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **replacement},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {
                        "template_version": replacement["id"],
                        "subject": replacement["values"]["subject"],
                    },
                },
                {"operation": "remove", "section": "content", "id": str(original)},
            ],
        ).state
        == "applied"
    )
    assert deliver(harness, second) is None
    assert second.state == "cancelled" and second.reason == "scope_replaced"
    assert FamilyMailTest.objects.filter(state="prepared").count() == 2


def abandon_until_settled(message, owner):
    """Claim, let a one-second lease expire and run real recovery until terminal.

    Lost leases, not in-process exceptions. Each round is a real claim; the
    recovery owner decides the disposition from durable evidence alone.
    """
    from threading import Event

    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DISPATCH

    from .test_taskrun_postgresql import act, expire

    rounds = 0
    while True:
        status = _status(TaskRun.objects.get(pk=message.task_id))
        if status.state in {"succeeded", "failed", "cancelled"}:
            return rounds
        if status.state != "running":
            Event().wait(1.1)  # The recovery retry delay must have elapsed.
            status = act(status, "claim")
        status = act(status, "heartbeat", lease_seconds=1)
        expire(status)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={DISPATCH: owner},
            )
        rounds += 1
        assert rounds <= 10, "recovery never settled the abandoned test"


@pytest.mark.parametrize("transient_first", [False, True])
def test_abandoned_dispatch_recovery_cancels_the_unsent_test(
    family_test, monkeypatch, transient_first
):
    """Recovery cancels a definitely unsent test once the budget is spent.

    Until then the message stays unsent and the task waits to retry; the
    last round cancels the message and settles the task, so the test can
    never block Testing cleanup with no live task left to settle it. A
    transient provider refusal before abandonment is a definite
    non-acceptance and cancels the same way.
    """
    from pathlib import Path

    from parishkit.stewardship.campaigns.cleanup_preview import cleanup_preview
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        MAX_ATTEMPTS,
        delivery_handler,
    )

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.RECOVERY_RETRY_SECONDS",
        1,
    )
    if transient_first:
        deliver(harness, message, Status.TRANSIENT)
        assert message.state == "retry_wait" and message.attempt == 1
    owner = delivery_handler(None, credential_path=Path("/unused"))
    rounds = abandon_until_settled(message, owner)
    message.refresh_from_db()
    task = TaskRun.objects.get(pk=message.task_id)
    assert message.state == "cancelled" and task.state == "cancelled"
    assert message.reason == "preparation_failed"
    assert message.attempt == (1 if transient_first else 0)
    assert message.sealed_substitutions is None
    # The provider attempt's claim is the first abandoned round.
    assert rounds == MAX_ATTEMPTS
    with work_transaction():
        assert not cleanup_preview(harness.campaign.pk).unresolved


def recovery_cancel(status, message, *, reason="preparation_failed"):
    """Attempt the raw recovery cancel the MAIL owner would issue."""
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction
    from parishkit.stewardship.jobs.outbox_storage import change_message
    from parishkit.stewardship.jobs.outbox_validation import DeliveryEvidence

    return change_message(
        message_id=message.pk,
        action=DeliveryAction.CANCEL_UNSENT,
        command_id=uuid4(),
        expected_version=message.version,
        actor_id=uuid4(),
        correlation_id=status.run_id,
        evidence=DeliveryEvidence(reason=reason),
        admit=lambda *args: True,
    )


def abandon(message, times):
    """Abandon the message's task the given number of times without recovery."""
    from threading import Event

    from .test_taskrun_postgresql import act, expire

    status = _status(TaskRun.objects.get(pk=message.task_id))
    for _ in range(times):
        if status.state == "abandoned":
            status = act(status, "recovery_retry")
            Event().wait(1.1)  # The one-second retry delay must have elapsed.
        if status.state != "running":
            status = act(status, "claim")
        status = act(status, "heartbeat", lease_seconds=1)
        status = expire(status)
    return status


@pytest.mark.parametrize("case", ["budget", "purpose", "reason", "uncertain"])
def test_mail_role_recovery_cancel_is_refused_outside_its_exact_conditions(
    family_test, case
):
    """SQL admits the recovery cancel only for an exhausted, definitely unsent test."""
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import MAX_ATTEMPTS

    harness, browser, path, _ = family_test
    if case == "purpose":
        from .test_family_mail_dispatch_postgresql import prepare

        with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
            message = prepare(harness)
    else:
        request_tickets(browser, path, [1])
        (message,) = prepare_tests(harness)
    if case == "uncertain":
        # An idempotent retry keeps its payload and outcome; nothing may cancel it.
        from parishkit.stewardship.jobs.delivery_states import DeliveryAction
        from parishkit.stewardship.jobs.outbox_storage import change_message
        from parishkit.stewardship.jobs.outbox_validation import DeliveryEvidence

        initialize_key_inventories(harness.rings.private)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin=ORIGIN,
            )
        message.refresh_from_db()
        with work_transaction():
            change_message(
                message_id=message.pk,
                action=DeliveryAction.RETRY_IDEMPOTENT,
                command_id=uuid4(),
                expected_version=message.version,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
                evidence=DeliveryEvidence(
                    evidence_digest="a" * 64,
                    evidence_note="synthetic uncertain retry",
                    reason="smtp_transient",
                ),
                retry_seconds=1,
                admit=lambda *args: True,
            )
        message.refresh_from_db()
        assert message.state == "retry_wait" and message.action == "retry_idempotent"
    status = abandon(message, 1 if case == "budget" else MAX_ATTEMPTS)
    assert status.state == "abandoned"
    message.refresh_from_db()
    # Outside the exact recovery conditions the write needs a live claim,
    # which an abandoned task no longer has.
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        pytest.raises(IntegrityError, match="live exact claim"),
        work_transaction(),
    ):
        recovery_cancel(
            status,
            message,
            reason="recovery_unknown" if case == "reason" else "preparation_failed",
        )
    message.refresh_from_db()
    assert message.state in {"pending", "retry_wait"}
    if case == "uncertain":
        with pytest.raises(PermissionError), work_transaction():
            cancel_abandoned_family_test(status, actor_id=uuid4())


def test_recovery_of_an_already_cancelled_test_only_settles_the_task(family_test):
    """A test cancelled under its claim, then abandoned, needs no second cancel."""
    from pathlib import Path

    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DISPATCH

    from .test_taskrun_postgresql import act, expire

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    initialize_key_inventories(harness.rings.private)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
    invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert (
            begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin=ORIGIN,
            )
            is None
        )
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "scope_replaced"
    version = message.version
    status = act(
        _status(TaskRun.objects.get(pk=message.task_id)), "heartbeat", lease_seconds=1
    )
    expire(status)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert recover_hint(
            message.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={
                DISPATCH: delivery_handler(None, credential_path=Path("/unused"))
            },
        )
    message.refresh_from_db()
    assert message.state == "cancelled" and message.version == version
    assert TaskRun.objects.get(pk=message.task_id).state == "cancelled"


def test_scheduler_recovery_hint_admission_writes_nothing(family_test):
    """The recovery plan is a pure predicate; the scheduler cannot write outbox rows."""
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        MAX_ATTEMPTS,
        delivery_handler,
        recover_delivery,
    )

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    status = abandon(message, MAX_ATTEMPTS)
    handler = delivery_handler(None, scheduler=True)
    assert handler.recover is recover_delivery
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        assert handler.admit("recovery_hint", status) is True
        assert handler.admit("recovery_cancel", status) is True
    message.refresh_from_db()
    assert message.state == "pending" and message.version == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "abandoned"


def test_queued_ticket_for_a_lastingly_ineligible_family_is_cancelled(family_test):
    """The worker cancels its task; the sweep records a cancellation, not a failure."""
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness, browser, path, _ = family_test
    (ticket,), _ = request_tickets(browser, path, [1])
    data = response_source()
    data.members[3]["emailAddress"] = ""
    refresh(harness, data)
    owner = family_test_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin=ORIGIN,
    )
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            ticket.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
    assert not OutboxMessage.objects.filter(purpose="family_test").exists()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    ticket.refresh_from_db()
    assert ticket.state == "cancelled" and ticket.family_id is None


def test_sample_page_survives_a_failing_family_link_query(family_test, monkeypatch):
    """The link runs in its own savepoint; the fictional sample still sends."""
    from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest

    def broken(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.campaign_family_test.families_link", broken
    )
    _, browser, _, sample = family_test
    with web_login():
        page = browser.get(sample)
        assert page.status_code == 200 and page.context["families_url"] is None
        token = page.context["form"]["preview_token"].value()
        assert post(browser, sample, {"preview_token": token}).status_code == 302
    assert CampaignMailTest.objects.count() == 1
