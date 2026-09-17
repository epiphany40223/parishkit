"""Weekly private allocation binds exact subsets, routing and task ownership."""

from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import TaskOwnershipLost
from parishkit.stewardship.reports.weekly_capture import capture_weekly_snapshot
from parishkit.stewardship.reports.weekly_fanout import (
    compile_weekly_page,
    load_weekly_page,
    retain_weekly_page,
)
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..content_factory import content
from ..policy_factory import address
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_weekly_capture_postgresql import INSTANT, prepare
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def messages():
    """Real Family submissions also allocate receipts, which are not weekly mail."""
    return OutboxMessage.objects.filter(purpose="weekly_digest")


def configure_content(harness, *, additional_admins=()):
    """Install the selected immutable prose and any additional exact Admin rules."""
    definition = ScheduleDefinition.objects.get(kind="weekly_digest")
    template = content(str(harness.campaign.pk), kind="email", slot="weekly_digest")
    template["id"] = definition.current_revision.values["template_version"]
    template["values"]["subject"] = definition.current_revision.values["subject"]
    store = harness.service.store
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **template}]
            + [
                {"operation": "add", "section": "login_rules", **address(email)}
                for email in additional_admins
            ],
        ).state
        == "applied"
    )


def captured(harness, *, additional_admins=()):
    """Install selected weekly prose before freezing the real recipient cohort."""
    claim = prepare(harness)
    configure_content(harness, additional_admins=additional_admins)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        snapshot = capture_weekly_snapshot(claim)
    return claim, snapshot


def detached(claim):
    """Use short owned reads and compile after releasing the work-order lock."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        page = load_weekly_page(claim)
    contents = compile_weekly_page(page, public_origin="https://parish.example")
    return page, contents


def retain(claim, page, contents):
    """Exercise the exact runtime worker role, not an unrestricted ORM write."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        return retain_weekly_page(claim, page, contents)


def test_individually_addressed_live_messages_preserve_every_selected_item(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "PRIVATE-WEEKLY-REQUEST")
    with campaign_clock(INSTANT):
        claim, snapshot = captured(harness, additional_admins=("second@example.org",))
        page, contents = detached(claim)
        assert len(page.recipients) == 2 and page.last
        assert "PRIVATE-WEEKLY-REQUEST" not in repr(page)
        assert retain(claim, page, contents).phase == "complete"
        assert WeeklyDigestRecipient.objects.count() == messages().count() == 2
        for recipient in WeeklyDigestRecipient.objects.select_related("outbox__render"):
            message, render = recipient.outbox, recipient.outbox.render
            assert message.semantic_key == recipient.pk and message.state == "pending"
            assert message.purpose == "weekly_digest" and message.family_id is None
            assert (
                render.intended_recipients
                == render.routed_recipients
                == [recipient.address]
            )
            assert recipient.information == snapshot.information
            assert recipient.covered_messages == recipient.corrections == []
            assert render.html.endswith(recipient.html) and render.text.endswith(
                recipient.text
            )
            assert "PRIVATE-WEEKLY-REQUEST" in recipient.text
        assert (
            ScheduleOccurrence.objects.get(pk=snapshot.preparation.occurrence_id).state
            == "pending"
        )
        with pytest.raises(PermissionError):
            detached(claim)


def test_empty_snapshot_completes_preparation_without_allocating_mail(response_service):
    with campaign_clock(INSTANT):
        claim, snapshot = captured(response_service)
        page, contents = detached(claim)
        assert page.recipients == contents == () and page.last
        assert retain(claim, page, contents).phase == "complete"
        assert not messages().exists()
        assert not WeeklyDigestRecipient.objects.exists()
        # A coherently empty capture completes without claiming provider delivery.
        assert (
            ScheduleOccurrence.objects.get(pk=snapshot.preparation.occurrence_id).state
            == "succeeded"
        )


def test_bounded_pages_replay_neither_prior_messages_nor_private_content(
    live_response_service, monkeypatch
):
    monkeypatch.setattr("parishkit.stewardship.reports.weekly_fanout.LIMIT", 1)
    respond(live_response_service, "Original selected request")
    with campaign_clock(INSTANT):
        claim, _ = captured(
            live_response_service, additional_admins=("second@example.org",)
        )
        first, contents = detached(claim)
        assert not first.last
        assert retain(claim, first, contents).phase == "fanout"
        original = messages().get()
        with pytest.raises(StorageInvariantError, match="coverage changed"):
            retain(claim, first, contents)
        second, contents = detached(claim)
        assert (
            second.last and second.recipients[0].address != first.recipients[0].address
        )
        assert retain(claim, second, contents).phase == "complete"
        assert messages().count() == 2
        assert OutboxMessage.objects.get(pk=original.pk).render_id == original.render_id


def test_interrupted_page_rolls_back_all_outbox_and_recipient_effects(
    live_response_service,
):
    respond(live_response_service, "Atomic page request")
    with campaign_clock(INSTANT):
        claim, _ = captured(live_response_service)
        page, contents = detached(claim)
        with pytest.raises(RuntimeError), work_transaction():
            retain(claim, page, contents)
            raise RuntimeError("Synthetic page interruption")
        assert not messages().exists() and not WeeklyDigestRecipient.objects.exists()
        assert retain(claim, page, contents).phase == "complete"


def test_compilation_must_release_database_transaction(live_response_service):
    respond(live_response_service, "Detached compilation request")
    with campaign_clock(INSTANT):
        claim, _ = captured(live_response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            page = load_weekly_page(claim)
            with pytest.raises(StorageInvariantError, match="outside transactions"):
                compile_weekly_page(page, public_origin="https://parish.example")


@pytest.mark.parametrize("last", [False, True])
def test_missing_recipient_cannot_commit_even_an_incomplete_page(
    live_response_service, monkeypatch, last
):
    monkeypatch.setattr("parishkit.stewardship.reports.weekly_fanout.LIMIT", 1)
    respond(live_response_service, "Atomic recipient request")
    with campaign_clock(INSTANT):
        claim, _ = captured(
            live_response_service,
            additional_admins=() if last else ("second@example.org",),
        )
        page, contents = detached(claim)
        with (
            patch(
                "parishkit.stewardship.reports.weekly_fanout.WeeklyDigestRecipient.objects.create"
            ),
            pytest.raises(DatabaseError, match="recipient"),
        ):
            retain(claim, page, contents)
        assert not messages().exists() and not WeeklyDigestRecipient.objects.exists()
        assert (
            WeeklyDigestPreparation.objects.get(task_id=claim.run_id).phase == "fanout"
        )


def test_stale_claim_cannot_publish_precompiled_page(live_response_service):
    respond(live_response_service, "Fenced page request")
    with campaign_clock(INSTANT):
        claim, _ = captured(live_response_service)
        page, contents = detached(claim)
        with pytest.raises(TaskOwnershipLost):
            retain(replace(claim, fence=claim.fence + 1), page, contents)
        assert not messages().exists()


@pytest.mark.parametrize("tamper", ["omit", "proof", "recipient", "body"])
def test_recipient_sql_independently_rejects_fabricated_binding(
    live_response_service, tamper
):
    respond(live_response_service, "Genuine weekly request")
    with campaign_clock(INSTANT):
        claim, _ = captured(live_response_service)
        page, contents = detached(claim)
        create = WeeklyDigestRecipient.objects.create

        def wrong(**values):
            """Alter only the final write, bypassing the Python page comparison."""
            if tamper == "omit":
                values["information"] = []
            elif tamper == "proof":
                values["covered_messages"] = [str(uuid4())]
            elif tamper == "recipient":
                values["address"] = "outsider@example.org"
            else:
                values["html"] = "<p>Different report</p>"
            return create(**values)

        with (
            patch(
                "parishkit.stewardship.reports.weekly_fanout.WeeklyDigestRecipient.objects.create",
                side_effect=wrong,
            ),
            pytest.raises(DatabaseError, match="Weekly recipient"),
        ):
            retain(claim, page, contents)
        assert not messages().exists() and not WeeklyDigestRecipient.objects.exists()


def test_outsider_has_no_coverage_projection(live_response_service):
    respond(live_response_service, "Private scope request")
    with campaign_clock(INSTANT):
        _, snapshot = captured(live_response_service)
        with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_weekly_prior_items_v1(%s,%s)",
                [snapshot.pk, "outsider@example.org"],
            )
            assert cursor.fetchone()[0] is None
