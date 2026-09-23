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

    with campaign_clock(starts_at - timedelta(days=1)):
        client = Client(enforce_csrf_checks=True)
        client.get("/")
        csrf = client.cookies.get("pk_family_csrf")
        if csrf is not None:
            response = client.post(
                "/", {"code": code, "csrfmiddlewaretoken": csrf.value}
            )
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
    # A stale Google sign-in is refused by web, and a forged fresh timestamp
    # is refused by SQL against the actual session record.
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
    with monkeypatch.context() as patch:

        def stale(request):
            raise PermissionError("Please authenticate with Google again.")

        patch.setattr(intake, "require_fresh", stale)
        with web_login():
            response = post(
                browser,
                path,
                {"action": "confirm", "preview": token, "acknowledge": "on"},
            )
        assert response.status_code == 403
    assert not FamilyMailTest.objects.exists()
    # Production offers no chosen-Family test at all.
    from .response_builders import activate_response_service

    activate_response_service(harness)
    with web_login():
        assert browser.get(path).status_code == 403
        page = browser.get(sample)
    assert page.status_code in {200, 409}
    if page.status_code == 200:
        assert page.context["families_url"] is None


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
