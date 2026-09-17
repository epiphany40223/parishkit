"""Real schedule replacement preserves accepted per-Admin item/disposition proofs."""

import json
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.schedule_preview import work_summary
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.outbox_storage import _status
from parishkit.stewardship.reports.weekly_capture import capture_weekly_snapshot
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
)
from parishkit.stewardship.reports.weekly_planning import cover_dates, discover_dates

from .campaign_builders import campaign_clock
from .campaign_builders import change as configure
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, provider_evidence, submit
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit as respond_form
from .test_schedule_reconciliation_postgresql import replace_schedule
from .test_weekly_capture_postgresql import INSTANT, allocate
from .test_weekly_fanout_postgresql import captured, detached, messages, retain
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def publish(claim):
    """Allocate only private queued intents; tests explicitly synthesize acceptance."""
    page, content = detached(claim)
    assert retain(claim, page, content).phase == "complete"
    return page


def replacement():
    """Discover the replacement through exact roles and retained predecessor slots."""
    claim = allocate()
    with task_login(ServiceRole.WORKER, exact=True):
        for _ in range(15):
            row = WeeklyDigestPreparation.objects.get(task_id=claim.run_id)
            if row.phase == "capture":
                break
            with work_transaction():
                (discover_dates if row.phase == "dates" else cover_dates)(claim)
        else:
            pytest.fail("Weekly replacement did not reach capture.")
        with work_transaction():
            snapshot = capture_weekly_snapshot(claim)
    return claim, snapshot


def accepted(recipient):
    """Use the provider-free journal test seam, never invoke the mail adapter."""
    return change(
        submit(_status(recipient.outbox)),
        DeliveryAction.ACCEPT,
        evidence=provider_evidence(),
    )


def test_partial_cohort_replacement_omits_only_previously_accepted_admin(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "One genuine live request")
    with campaign_clock(INSTANT):
        claim, old = captured(harness, additional_admins=("second@example.org",))
        publish(claim)
        served = WeeklyDigestRecipient.objects.get(
            snapshot=old, address="admin@example.org"
        )
        accepted(served)
        definition = ScheduleDefinition.objects.get(kind="weekly_digest")
        summary = work_summary(harness.campaign.pk)[str(definition.pk)]
        assert summary["outboxes"] == 2 and summary["blocking"] == 0
        assert (
            replace_schedule(harness.service.store, definition, uuid4()).state
            == "applied"
        )
        other = WeeklyDigestRecipient.objects.get(
            snapshot=old, address="second@example.org"
        )
        other.outbox.refresh_from_db()
        assert other.outbox.state == "cancelled"
        claim, new = replacement()

        def omit_proof(execute, sql, params, many, context):
            """Lie to Python; SQL must still reject resending accepted rows."""
            if isinstance(sql, str) and sql.startswith(
                "SELECT stewardship_weekly_prior_items_v1("
            ):
                sql = "SELECT %s::text"
                params = [
                    json.dumps(
                        {
                            "information": new.information,
                            "corrections": new.corrections,
                            "covered_messages": [],
                        }
                    )
                ]
            return execute(sql, params, many, context)

        with (
            connection.execute_wrapper(omit_proof),
            pytest.raises(DatabaseError, match="exact owned item coverage"),
        ):
            publish(claim)
        assert not WeeklyDigestRecipient.objects.filter(snapshot=new).exists()
        page = publish(claim)
        assert page.recipients[0].document is None
        reused = WeeklyDigestRecipient.objects.get(snapshot=new, address=served.address)
        assert (
            reused.outbox_id is None and reused.information == reused.corrections == []
        )
        assert reused.covered_messages == [str(served.outbox_id)]
        remaining = WeeklyDigestRecipient.objects.get(
            snapshot=new, address=other.address
        )
        assert remaining.outbox_id and remaining.information == new.information
        assert remaining.covered_messages == []
        assert messages().count() == 3


def test_partially_covered_corrections_do_not_suppress_new_items(live_response_service):
    """New text/corrections survive partial coverage of an earlier correction."""
    harness = live_response_service
    respond(harness, "REQUEST-A")
    with campaign_clock(INSTANT):
        claim, first = captured(harness, additional_admins=("second@example.org",))
        publish(claim)
        accepted(
            WeeklyDigestRecipient.objects.get(
                snapshot=first, address="admin@example.org"
            )
        )
        definition = ScheduleDefinition.objects.get(kind="weekly_digest")
        assert (
            replace_schedule(harness.service.store, definition, uuid4()).state
            == "applied"
        )
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "REQUEST-B"
        respond_form(harness, form, answers)
        claim, second = replacement()
        publish(claim)
        second_recipient = WeeklyDigestRecipient.objects.get(
            snapshot=second, address="admin@example.org"
        )
        assert second_recipient.corrections == [[first.information[0], "superseded"]]
        accepted(second_recipient)
        store = harness.service.store
        assert (
            configure(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "update",
                        "section": "schedules",
                        "id": str(definition.pk),
                        "values": {"time": "11:00:00"},
                    }
                ],
            ).state
            == "applied"
        )
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = "REQUEST-C"
        respond_form(harness, form, answers)
        claim, third = replacement()
        page = publish(claim)
        row = WeeklyDigestRecipient.objects.get(
            snapshot=third, address="admin@example.org"
        )
        assert len(third.corrections) == 2
        assert row.corrections == [[second.information[0], "superseded"]]
        assert row.information == third.information
        assert row.covered_messages == [str(second_recipient.outbox_id)]
        assert "REQUEST-C" in row.text
        assert "REQUEST-A" not in row.text and "REQUEST-B" not in row.text
        assert len(page.recipients[0].document.corrections) == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_uncertain_or_submitting_weekly_child_blocks_schedule_replacement(
    live_response_service, unknown
):
    harness = live_response_service
    respond(harness, "Uncertain report request")
    with campaign_clock(INSTANT):
        claim, _ = captured(harness)
        publish(claim)
        submitted = submit(_status(messages().get()))
        if unknown:
            change(submitted, DeliveryAction.MARK_UNKNOWN, evidence=provider_evidence())
        definition = ScheduleDefinition.objects.get(kind="weekly_digest")
        summary = work_summary(harness.campaign.pk)[str(definition.pk)]
        assert summary["outboxes"] == 1 and summary["blocking"] == 1
        with pytest.raises(CampaignAdmissionUnavailable, match="in-flight"):
            replace_schedule(harness.service.store, definition, uuid4())
        assert messages().get().state == (
            "delivery_unknown" if unknown else "submitting"
        )
