"""Closed held-message decisions preserve Family closure and exact dispatch."""

# ruff: noqa: F811 -- pytest resolves the imported setup fixture dependencies.

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.delivery_control_models import (
    HeldMessageResolution,
)
from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.schedule_models import (
    PostCloseMailResolution,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock, close_campaign
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated, begin
from .test_daily_digest_planning_postgresql import allocate
from .test_daily_digest_tasks_postgresql import execute
from .test_delivery_control_postgresql import accepted_sender_check
from .test_family_auth_postgresql import login as family_login
from .test_family_mail_dispatch_postgresql import claim
from .test_response_http_postgresql import answers_for
from .test_response_http_postgresql import post as family_post
from .test_schedule_reconciliation_postgresql import replace_schedule
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_weekly_tasks_postgresql import execute as execute_weekly
from .test_weekly_tasks_postgresql import queued as queue_weekly
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)
from .test_withdrawal_work_postgresql import future_message

pytestmark = pytest.mark.django_db(transaction=True)


def submit_while_paused(item, settings):
    """Use real restricted Family HTTP admission, forms and final submission."""
    service = item.arguments[1]
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        service.store,
        service.limiter,
        item.ring.general,
        item.ring.mac,
        item.ring.public,
    )
    family = FamilyCampaign.objects.get(campaign=item.campaign, family_duid=1)
    code = item.ring.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    with web_login():
        browser, response = family_login(code)
        assert response.status_code == 302, response.content
        response = family_post(browser, "/family/form", {"testing_acknowledged": False})
        assert response.status_code == 200, response.content
        form = response.json()["form"]
        answers = answers_for(form) | {"additional_information": "Please contact us"}
        # Setup's deliberately sparse source requires these ordinary answers;
        # the paused flow must retain validation, not silently invent defaults.
        for member in answers["members"].values():
            member.update(
                last_name="Example",
                birth_date="1980-01-01",
                gender="Unspecified",
                language="English",
            )
        response = family_post(
            browser,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
        assert response.status_code == 200, response.content
        assert response.json() == {"accepted": True}
    return SubmissionReceiptOccurrence.objects.get(submission__family=family)


def test_closed_receipt_and_weekly_skips_are_durable_not_provider_acceptance(
    scheduled, settings
):
    """One real paused response retains distinct receipt and report obligations."""
    item = scheduled
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(hours=1)
    ):
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review delivery")
            commands.confirm(*item.arguments, token=token)
        receipt = submit_while_paused(item, settings)
        assert receipt.outbox.pause_hold_id and receipt.outbox.attempt == 0
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(days=9)
    ):
        harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
        status = queue_weekly(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        weekly = list(OutboxMessage.objects.filter(purpose="weekly_digest"))
        assert weekly and all(message.pause_hold_id for message in weekly)
        close_campaign(item.campaign, uuid4())
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Cancel held receipts",
                decision="cancel",
                types=["receipt"],
            )
            command = commands.confirm(*item.arguments, token=token)
            assert command.control_id is None
        receipt.outbox.refresh_from_db()
        assert receipt.outbox.state == "cancelled" and receipt.outbox.attempt == 0
        resolved = PostCloseMailResolution.objects.get(outbox_id=receipt.outbox_id)
        assert resolved.obligation_key == f"receipt:{receipt.submission_id}"
        assert resolved.coverage == {
            "items": [
                {"kind": "submission", "id": str(receipt.submission_id), "version": 1}
            ],
            "daily_range": None,
        }
        with web_login():
            preview, token = commands.preview_resolution(
                *item.arguments,
                reason="Cancel held weekly report",
                decision="cancel",
                types=["weekly_digest"],
            )
            assert preview["selection"]["coverage"]["weekly_digest"]["items"] == len(
                weekly
            )
            command = commands.confirm(*item.arguments, token=token)
            assert command.control_id is not None
        item.campaign.refresh_from_db()
        assert item.campaign.state == "closed" and not item.campaign.delivery_paused
        resolved = PostCloseMailResolution.objects.get(occurrence__isnull=False)
        information = AdditionalInformationItem.objects.get()
        assert resolved.coverage == {
            "items": [
                {
                    "kind": "item",
                    "id": str(information.pk),
                    "version": information.version,
                }
            ],
            "daily_range": None,
        }
        assert not ScheduleFulfillment.objects.filter(
            occurrence=resolved.occurrence
        ).exists()
        with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_weekly_history_v1(%s,'production',NULL)",
                [item.campaign.pk],
            )
            history = json.loads(cursor.fetchone()[0])
        assert history["watermark"] == 1 and history["reported"] == []
        assert history["corrected"] == []
        definition = ScheduleDefinition.objects.get(kind="weekly_digest")
        assert (
            replace_schedule(item.arguments[1].store, definition, uuid4()).state
            == "applied"
        )
        definition.refresh_from_db()
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            DigestScheduleProducer(uuid4(), limit=100)(guard)
        assert not ScheduleOccurrence.objects.filter(
            revision_id=definition.current_revision_id, slot=resolved.occurrence.slot
        ).exists()
        assert PostCloseMailResolution.objects.filter(pk=resolved.pk).exists()


def test_closed_resolution_releases_only_selected_mail_and_can_cancel_without_health(
    scheduled, monkeypatch, tmp_path
):
    """Actual closed release bypasses only its exact pause, never Family closure."""
    item = scheduled
    _, family = future_message(item)
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    due = item.campaign.active_configuration.starts_at + timedelta(days=2, hours=12)
    with campaign_clock(due):
        allocated(harness)
        daily_messages = OutboxMessage.objects.filter(purpose="daily_digest")
        daily = daily_messages.order_by("id").first()
        expected_held = daily_messages.count() + 1
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Hold campaign mail"
            )
            commands.confirm(*item.arguments, token=token)
        close_campaign(item.campaign, uuid4())
    closed_at = item.campaign.active_configuration.ends_at + timedelta(hours=1)
    with campaign_clock(closed_at):
        path = f"/admin/campaign/{item.campaign.pk}/delivery"
        with web_login():
            page = item.browser.get(path)
            assert page.status_code == 200 and page.context["resolve_available"]
            assert not page.context["resume_available"]
            assert page.context["inventory"]["held"] == expected_held
            with pytest.raises(StaleRecordError):
                commands.preview_resume(*item.arguments, reason="Do not reopen")
            with pytest.raises(StaleRecordError, match="sender check"):
                commands.preview_resolution(
                    *item.arguments,
                    reason="Not healthy yet",
                    decision="release",
                    types=["daily_digest"],
                )
            assert (
                post(
                    item.browser,
                    path,
                    {
                        "action": "preview_resolve",
                        "reason": "No Family release",
                        "decision": "release",
                        "initial": "yes",
                    },
                ).status_code
                == 400
            )
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            page = post(
                item.browser,
                path,
                {
                    "action": "preview_resolve",
                    "reason": "Release completed report",
                    "decision": "release",
                    "daily_digest": "yes",
                },
            )
            assert page.status_code == 200, page.content
            token = page.context["control_token"]
            assert (
                post(
                    item.browser, path, {"action": "confirm", "preview": token}
                ).status_code
                == 302
            )
            receipt = commands.confirm(*item.arguments, token=token)
            assert receipt.control_id is None  # Family close work is still held.
        daily.refresh_from_db()
        item.campaign.refresh_from_db()
        assert daily.pause_hold_id is None and item.campaign.delivery_paused
        assert HeldMessageResolution.objects.get(message=daily).decision == "release"
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(daily)
            assert begin(daily, execution) is not None
            finish_submission(
                daily.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
        # Ordinary closed-Family admission cancels its message without a key,
        # provider call, or release exception. Resolving reports cannot reopen it.
        family_message = OutboxMessage.objects.get(pk=family.message_id)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(family_message)
            assert begin(family_message, execution) is None
        family_message.refresh_from_db()
        assert (
            family_message.state == "cancelled"
            and family_message.reason == "campaign_closed"
        )
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="All held mail resolved",
                decision="clear",
                types=[],
            )
            commands.confirm(*item.arguments, token=token)
            _, token = commands.preview_pause(
                *item.arguments, reason="Review final report"
            )
            commands.confirm(*item.arguments, token=token)
            assert not commands.health(item.campaign.pk)["ready"]
        # A newly generated final report receives a durable hold immediately;
        # no mail worker has inspected this message yet.
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            DigestScheduleProducer(uuid4(), limit=100)(guard)
        status = allocate(claim_task=False)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        held = list(
            OutboxMessage.objects.filter(purpose="daily_digest", state="pending")
        )
        assert held and all(message.pause_hold_id for message in held)
        with web_login():
            preview, token = commands.preview_resolution(
                *item.arguments,
                reason="Do not send final report",
                decision="cancel",
                types=["daily_digest"],
            )
            assert preview["selection"]["health"] is None
            receipt = commands.confirm(*item.arguments, token=token)
            assert receipt.control_id is not None
        item.campaign.refresh_from_db()
        assert not item.campaign.delivery_paused and item.campaign.state == "closed"
        for message in held:
            message.refresh_from_db()
            assert (
                message.state == "cancelled"
                and message.reason == "admin_post_close_skip"
            )
            assert HeldMessageResolution.objects.get(
                message=message, decision="cancel"
            ).pk
        resolutions = PostCloseMailResolution.objects.filter(campaign=item.campaign)
        assert resolutions.exists()
        assert all(row.occurrence.state == "skipped" for row in resolutions)
        assert all(row.coverage["daily_range"] for row in resolutions)
