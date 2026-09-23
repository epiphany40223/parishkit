"""Closed held-message decisions preserve Family closure and exact dispatch."""

# ruff: noqa: F811 -- pytest resolves the imported setup fixture dependencies.

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
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
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.digest_models import DailyDigestRecipient
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.reports.weekly_manual import request_manual_report
from parishkit.stewardship.reports.weekly_models import WeeklyDigestRecipient
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock, close_campaign
from .campaign_builders import change as configure
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated, begin
from .test_daily_digest_planning_postgresql import allocate
from .test_daily_digest_tasks_postgresql import execute
from .test_delivery_control_postgresql import accepted_sender_check
from .test_family_auth_postgresql import login as family_login
from .test_family_mail_dispatch_postgresql import claim
from .test_outbox_postgresql import change as change_delivery
from .test_outbox_postgresql import provider_evidence
from .test_response_http_postgresql import answers_for
from .test_response_http_postgresql import post as family_post
from .test_schedule_reconciliation_postgresql import replace_schedule
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_weekly_capture_postgresql import allocate as allocate_weekly
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


def assert_current_resolution(resolved, *, current):
    """Observe only the opaque scheduling proof under its actual read-only role."""
    with task_login(ServiceRole.SCHEDULER, exact=True), connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM stewardship_postclose_current WHERE id=%s", [resolved.pk]
        )
        assert cursor.fetchone() == ((resolved.pk,) if current else None)


def assert_manual_does_not_resolve(item, resolved):
    """A genuinely completed manual cohort never discharges an automatic skip."""
    with web_login():
        status = request_manual_report(
            item.arguments[1].store,
            item.arguments[0].portal_session.principal_id,
            item.campaign.pk,
            command_id=uuid4(),
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
        )
    with task_login(ServiceRole.WORKER, exact=True):
        execute_weekly(status)
    recipients = list(
        WeeklyDigestRecipient.objects.filter(
            snapshot__preparation__task_id=status.run_id
        ).select_related("outbox", "snapshot__preparation")
    )
    assert len(recipients) == 2
    for recipient in recipients:
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(recipient.outbox)
            assert begin(recipient.outbox, execution) is not None
            finish_submission(
                recipient.outbox_id,
                execution.claim,
                FamilyDeliveryResult(Status.ACCEPTED, 1),
            )
    assert ScheduleFulfillment.objects.filter(
        occurrence_id=recipients[0].snapshot.preparation.occurrence_id,
        disposition="delivered",
    ).exists()
    assert_current_resolution(resolved, current=False)


def test_inflight_retries_acquire_hold_atomically_and_keep_uncertainty(scheduled):
    """A provider result after pause cannot slip through a closed empty-clear."""
    item = scheduled
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(days=2)
    ):
        allocated(harness)
        messages = list(
            OutboxMessage.objects.filter(purpose="daily_digest").order_by("id")
        )
        assert len(messages) == 2
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            executions = [claim(message) for message in messages]
            for message, execution in zip(messages, executions, strict=True):
                assert begin(message, execution) is not None
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Stop in-flight retries"
            )
            commands.confirm(*item.arguments, token=token)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            finish_submission(
                messages[0].pk,
                executions[0].claim,
                FamilyDeliveryResult(Status.TRANSIENT, 1),
            )
            unknown = finish_submission(
                messages[1].pk,
                executions[1].claim,
                FamilyDeliveryResult(Status.UNKNOWN, 1),
            )
        # SMTP deliberately never claims provider idempotency. Exercise this
        # storage-only reconciliation branch through the journal's test owner,
        # without widening the actual MAIL role's authority to invent it.
        with (
            task_login(ServiceRole.MAIL_DISPATCH, exact=True),
            pytest.raises(DatabaseError),
        ):
            change_delivery(
                unknown,
                DeliveryAction.RETRY_IDEMPOTENT,
                retry_seconds=30,
                evidence=provider_evidence(),
            )
        change_delivery(
            unknown,
            DeliveryAction.RETRY_IDEMPOTENT,
            retry_seconds=30,
            evidence=provider_evidence(),
        )
        for message in messages:
            message.refresh_from_db()
            assert message.state == "retry_wait" and message.pause_hold_id is not None
        close_campaign(item.campaign, uuid4())
    with (
        campaign_clock(item.campaign.active_configuration.ends_at + timedelta(hours=1)),
        web_login(),
    ):
        inventory = commands.inventory(item.campaign.pk)
        assert inventory["held"] == 2 and inventory["unknown"] == 1
        with pytest.raises(StaleRecordError):
            commands.preview_resolution(
                *item.arguments,
                reason="No implicit release",
                decision="clear",
                types=[],
            )
        with pytest.raises(StaleRecordError, match="uncertain"):
            commands.preview_resolution(
                *item.arguments,
                reason="Not safely cancellable",
                decision="cancel",
                types=["daily_digest"],
            )


def submit_while_paused(item, settings, *, text="Please contact us"):
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
        answers = answers_for(form) | {"additional_information": text}
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
    return (
        SubmissionReceiptOccurrence.objects.filter(submission__family=family)
        .order_by("-submission__campaign_sequence")
        .first()
    )


def test_selective_receipt_and_weekly_release_loses_permission_on_new_pause(
    scheduled, settings, monkeypatch, tmp_path
):
    """Real MAIL admission honors only the exact message and pause revision."""
    item = scheduled
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(hours=1)
    ):
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Review outbound mail"
            )
            commands.confirm(*item.arguments, token=token)
        receipt = submit_while_paused(item, settings)
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(days=9)
    ):
        harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
        status = queue_weekly(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        weekly = list(
            OutboxMessage.objects.filter(purpose="weekly_digest").order_by("id")
        )
        assert len(weekly) == 2
        close_campaign(item.campaign, uuid4())
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Release only weekly reports",
                decision="release",
                types=["weekly_digest"],
            )
            assert commands.confirm(*item.arguments, token=token).control_id is None
        receipt.outbox.refresh_from_db()
        assert receipt.outbox.pause_hold_id
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(weekly[0])
            assert begin(weekly[0], execution) is not None
            finish_submission(
                weekly[0].pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Release receipts too",
                decision="release",
                types=["receipt"],
            )
            assert commands.confirm(*item.arguments, token=token).control_id is not None
            _, token = commands.preview_pause(
                *item.arguments, reason="Pause again before remaining sends"
            )
            commands.confirm(*item.arguments, token=token)
        with (
            task_login(ServiceRole.MAIL_DISPATCH, exact=True),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT stewardship_delivery_message_released_v1(%s)", [weekly[1].pk]
            )
            assert cursor.fetchone()[0] is False
            execution = claim(receipt.outbox)
            assert begin(receipt.outbox, execution) is None
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Release the held receipt",
                decision="release",
                types=["receipt"],
            )
            assert commands.confirm(*item.arguments, token=token).control_id is None
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert begin(receipt.outbox, execution) is not None
            finish_submission(
                receipt.outbox_id,
                execution.claim,
                FamilyDeliveryResult(Status.ACCEPTED, 1),
            )
        weekly[1].refresh_from_db()
        assert weekly[1].pause_hold_id and weekly[1].state == "pending"


@pytest.mark.parametrize("later_response", [False, True])
def test_closed_receipt_and_weekly_skips_are_durable_not_provider_acceptance(
    scheduled, settings, later_response
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
        original_item = AdditionalInformationItem.objects.get()
        frozen_version = original_item.version
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
        if later_response:
            submit_while_paused(
                item, settings, text="A new request after report capture"
            )
        close_campaign(item.campaign, uuid4())
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        # Snapshot first: neither cancel below may add fulfillment bookkeeping.
        before_cancel = set(ScheduleFulfillment.objects.values_list("pk", flat=True))
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
        assert resolved.coverage == {
            "items": [
                {
                    "kind": "item",
                    "id": str(original_item.pk),
                    "version": frozen_version,
                }
            ],
            "daily_range": None,
        }
        # Cancelling held mail records nothing as fulfilled, on any calendar.
        # The fixture's campaign starts relative to the real date, so on some
        # weekdays an earlier weekly slot has already elapsed by the capture
        # above; planning then correctly coalesces that missed slot into this
        # occurrence. Exactly that capture-time bookkeeping may exist: one
        # coalesced record per predecessor occurrence, and none on calendars
        # with a single elapsed slot.
        assert set(ScheduleFulfillment.objects.values_list("pk", flat=True)) == (
            before_cancel
        )
        assert sorted(
            ScheduleFulfillment.objects.filter(
                occurrence=resolved.occurrence
            ).values_list("disposition", "slot")
        ) == sorted(
            ("coalesced", slot)
            for slot in ScheduleOccurrence.objects.filter(
                state="coalesced", replacement=resolved.occurrence
            ).values_list("slot", flat=True)
        )
        with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_weekly_history_v1(%s,'production',NULL)",
                [item.campaign.pk],
            )
            history = json.loads(cursor.fetchone()[0])
        assert history["watermark"] == (0 if later_response else 1)
        assert history["reported"] == []
        assert history["corrected"] == []
        # Staff workflow edits must not resurrect a resolved digest obligation.
        # Family replacement above still invalidates its frozen semantic token.
        from parishkit.stewardship.responses.information import update_information

        original_item.refresh_from_db()
        with web_login():
            update_information(
                item.arguments[1].store,
                item.arguments[0].portal_session.principal_id,
                original_item.pk,
                expected_version=original_item.version,
                request_key=uuid4(),
                follow_up_needed=True,
                followed_up=True,
                confirm_clear=False,
                notes="Staff completed the contact after the digest was resolved.",
            )
        assert_current_resolution(resolved, current=not later_response)
        with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_weekly_history_v1(%s,'production',NULL)",
                [item.campaign.pk],
            )
            assert json.loads(cursor.fetchone()[0]) == history
        if later_response:
            assert_manual_does_not_resolve(item, resolved)
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
        assert (
            ScheduleOccurrence.objects.filter(
                revision_id=definition.current_revision_id,
                slot=resolved.occurrence.slot,
            ).exists()
            is later_response
        )
        assert PostCloseMailResolution.objects.filter(pk=resolved.pk).exists()
        if later_response:
            # The later automatic report consumes all due slots and accepts the
            # newer input. Its current proof also discharges the older slot;
            # another revision must not continually revive that old skip.
            status = allocate_weekly(claim_task=False)
            with task_login(ServiceRole.WORKER, exact=True):
                execute_weekly(status)
            recipients = WeeklyDigestRecipient.objects.filter(
                snapshot__preparation__task_id=status.run_id
            ).select_related("outbox", "snapshot__preparation")
            assert recipients.count() == 2
            assert_current_resolution(resolved, current=False)
            for index, recipient in enumerate(recipients):
                with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                    execution = claim(recipient.outbox)
                    assert begin(recipient.outbox, execution) is not None
                    finish_submission(
                        recipient.outbox_id,
                        execution.claim,
                        FamilyDeliveryResult(Status.ACCEPTED, 1),
                    )
                if index == 0:
                    assert_current_resolution(resolved, current=False)
            assert ScheduleFulfillment.objects.filter(
                occurrence_id=recipients.first().snapshot.preparation.occurrence_id,
                disposition="delivered",
            ).exists()
            assert_current_resolution(resolved, current=True)
            previous_revision = definition.current_revision_id
            store = item.arguments[1].store
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
            definition.refresh_from_db()
            assert definition.current_revision_id != previous_revision
            with (
                task_login(ServiceRole.SCHEDULER, exact=True),
                scheduler_session() as guard,
            ):
                DigestScheduleProducer(uuid4(), limit=100)(guard)
            assert not ScheduleOccurrence.objects.filter(
                revision_id=definition.current_revision_id,
                slot=resolved.occurrence.slot,
            ).exists()
            assert_current_resolution(resolved, current=True)


@pytest.mark.parametrize("accept_first", [False, True])
def test_closed_weekly_resolution_unions_only_cancelled_recipient_subsets(
    scheduled, settings, accept_first
):
    """Different real per-Admin subsets produce one exact semantic skip, not two."""
    item = scheduled
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(days=9)
    ):
        submit_while_paused(item, settings, text="Original request")
        status = queue_weekly(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        original = WeeklyDigestRecipient.objects.order_by("address").first()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(original.outbox)
            assert begin(original.outbox, execution) is not None
            finish_submission(
                original.outbox_id,
                execution.claim,
                FamilyDeliveryResult(Status.ACCEPTED, 1),
            )
        definition = ScheduleDefinition.objects.get(kind="weekly_digest")
        assert (
            replace_schedule(item.arguments[1].store, definition, uuid4()).state
            == "applied"
        )
        submit_while_paused(item, settings, text="Replacement request")
        status = allocate_weekly(claim_task=False)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        correction = WeeklyDigestRecipient.objects.select_related("outbox").get(
            snapshot__preparation__task_id=status.run_id, address=original.address
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(correction.outbox)
            assert begin(correction.outbox, execution) is not None
            finish_submission(
                correction.outbox_id,
                execution.claim,
                FamilyDeliveryResult(Status.ACCEPTED, 1),
            )
        store = item.arguments[1].store
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
        submit_while_paused(
            item, settings, text="Third request after partial correction"
        )
        status = allocate_weekly(claim_task=False)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        recipients = list(
            WeeklyDigestRecipient.objects.filter(
                snapshot__preparation__task_id=status.run_id
            )
            .select_related("outbox", "snapshot__preparation")
            .order_by("address")
        )
        assert len(recipients) == 2
        assert recipients[0].corrections != recipients[1].corrections
        if accept_first:
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(recipients[0].outbox)
                assert begin(recipients[0].outbox, execution) is not None
                finish_submission(
                    recipients[0].outbox_id,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )
        cancelled = recipients[1:] if accept_first else recipients
        expected = sorted(
            {
                (kind, key)
                for recipient in cancelled
                for kind, key in [("item", key) for key in recipient.information]
                + [("correction", key) for key, _ in recipient.corrections]
            }
        )
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Resolve differing weekly subsets"
            )
            commands.confirm(*item.arguments, token=token)
        close_campaign(item.campaign, uuid4())
    with (
        campaign_clock(item.campaign.active_configuration.ends_at + timedelta(hours=1)),
        web_login(),
    ):
        _, token = commands.preview_resolution(
            *item.arguments,
            reason="Cancel remaining exact weekly contents",
            decision="cancel",
            types=["weekly_digest"],
        )
        commands.confirm(*item.arguments, token=token)
    resolution = PostCloseMailResolution.objects.get(
        occurrence_id=recipients[0].snapshot.preparation.occurrence_id
    )
    assert resolution.coverage == {
        "daily_range": None,
        "items": [
            {
                "kind": kind,
                "id": key,
                "version": recipients[0].snapshot.item_versions[key],
            }
            for kind, key in expected
        ],
    }
    assert OutboxMessage.objects.get(pk=recipients[0].outbox_id).state == (
        "delivered" if accept_first else "cancelled"
    )
    assert OutboxMessage.objects.get(pk=recipients[1].outbox_id).state == "cancelled"
    from parishkit.stewardship.reports.information import (
        InformationQuery,
        information_page,
    )

    with web_login():
        report = information_page(
            item.campaign.pk, InformationQuery(disposition="superseded")
        )
    assert report["total"] == 2
    assert all(row["correction_resolved"] for row in report["rows"])


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
        daily.refresh_from_db()
        delivered_version = daily.version
        delivered_occurrence = DailyDigestRecipient.objects.get(
            outbox=daily
        ).ready.snapshot.preparation.occurrence_id
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
        assert resolutions.filter(occurrence_id=delivered_occurrence).exists()
        daily.refresh_from_db()
        assert daily.state == "delivered" and daily.version == delivered_version
        # The remaining cohort is explicitly skipped; the accepted child's
        # outbox remains the provider-success record, not whole-slot fulfillment.
        assert not ScheduleFulfillment.objects.filter(
            occurrence_id=delivered_occurrence, disposition="delivered"
        ).exists()


@pytest.mark.parametrize("decision", ["clear", "cancel"])
def test_closed_resolution_cancels_a_family_message_with_no_task_left(
    scheduled, decision
):
    """A held invitation with no task left cannot keep a closed pause forever.

    Preparation can exhaust its attempts while delivery is paused, leaving the
    held message unsent with no task. Once the campaign closes, no worker will
    claim it to apply the close policy and an unsent retry is refused, so any
    closed resolution (an empty clear, or cancelling held reports) cancels it
    as campaign_closed and the pause clears.
    """
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_taskrun_postgresql import act

    item = scheduled
    _, family = future_message(item)
    message = OutboxMessage.objects.get(pk=family.message_id)
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    due = item.campaign.active_configuration.starts_at + timedelta(days=2, hours=12)
    with campaign_clock(due):
        if decision == "cancel":
            allocated(harness)
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Hold campaign mail"
            )
            commands.confirm(*item.arguments, token=token)
        # Exhausted preparation while paused: the task fails, the message
        # stays unsent and held. The campaign is open, so nothing is stranded.
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            claim(message)
            act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        message.refresh_from_db()
        assert message.state == "pending" and message.pause_hold_id is not None
        assert commands.inventory(item.campaign.pk)["stranded"] == 0
        close_campaign(item.campaign, uuid4())
    closed_at = item.campaign.active_configuration.ends_at + timedelta(hours=1)
    with campaign_clock(closed_at):
        inventory = commands.inventory(item.campaign.pk)
        assert inventory["stranded"] == 1 and inventory["unknown"] == 0
        with web_login():
            page = item.browser.get(f"/admin/campaign/{item.campaign.pk}/delivery")
            assert page.status_code == 200
            assert "no remaining delivery task" in page.content.decode()
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Resolve closed mail",
                decision=decision,
                types=[] if decision == "clear" else ["daily_digest"],
            )
            receipt = commands.confirm(*item.arguments, token=token)
        assert receipt.control_id is not None
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused and item.campaign.state == "closed"
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "campaign_closed"
    assert message.pause_hold_id is None
    # The cancellation's command ID names the resolution that made it.
    assert message.command_id == UUID(
        hashlib.sha256(
            f"postclose_stranded:{receipt.pk}:{message.pk}".encode()
        ).hexdigest()[:32]
    )
    occurrence = ScheduleOccurrence.objects.get(pk=message.semantic_key)
    assert occurrence.state == "skipped" and occurrence.reason == "campaign_closed"
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"
    if decision == "cancel":
        assert not OutboxMessage.objects.filter(
            purpose="daily_digest", state="pending"
        ).exists()


def test_closed_resolution_never_strands_an_uncertain_family_message(scheduled):
    """An uncertain idempotent retry with no task left stays unknown.

    The outbox guard keeps its payload and forbids cancelling it, so it must
    never count as stranded: clearing stays refused, and a report resolution
    still succeeds, leaves it untouched and does not clear the pause.
    """
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_outbox_postgresql import submit
    from .test_taskrun_postgresql import act

    item = scheduled
    _, family = future_message(item)
    message = OutboxMessage.objects.get(pk=family.message_id)
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    due = item.campaign.active_configuration.starts_at + timedelta(days=2, hours=12)
    with campaign_clock(due):
        allocated(harness)
        # Storage-only provider boundary, as the in-flight test uses: the
        # attempt ends in an idempotent retry whose outcome stays uncertain.
        status = submit(
            SimpleNamespace(
                message_id=message.pk,
                version=message.version,
                worker_id=None,
                task_id=message.task_id,
            )
        )
        change_delivery(
            status,
            DeliveryAction.RETRY_IDEMPOTENT,
            retry_seconds=30,
            evidence=provider_evidence(),
        )
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Hold campaign mail"
            )
            commands.confirm(*item.arguments, token=token)
        close_campaign(item.campaign, uuid4())
    message.refresh_from_db()
    assert message.state == "retry_wait" and message.pause_hold_id is not None
    version = message.version
    closed_at = item.campaign.active_configuration.ends_at + timedelta(hours=1)
    with campaign_clock(closed_at), web_login():
        inventory = commands.inventory(item.campaign.pk)
        assert inventory["unknown"] == 1 and inventory["stranded"] == 0
        with pytest.raises(StaleRecordError):
            commands.preview_resolution(
                *item.arguments, reason="Clear", decision="clear", types=[]
            )
        _, token = commands.preview_resolution(
            *item.arguments,
            reason="Cancel reports",
            decision="cancel",
            types=["daily_digest"],
        )
        receipt = commands.confirm(*item.arguments, token=token)
    assert receipt.control_id is None
    item.campaign.refresh_from_db()
    assert item.campaign.delivery_paused
    message.refresh_from_db()
    assert message.state == "retry_wait" and message.version == version


def test_closed_paused_receipt_resend_follows_the_held_resolution(
    scheduled, settings, monkeypatch, tmp_path
):
    """A closed paused campaign cannot resume, so its resend follows release.

    An unknown receipt resent there is held at the current pause and goes only
    once the closed resolution releases receipts. A receipt that resolution
    already released carries no new hold, so resending it goes at once.
    """
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_delivery_resolution_postgresql import resolve, retry_admitted
    from .test_policy_postgresql import user
    from .test_taskrun_postgresql import act

    item = scheduled
    # Receipt resolution is keyless, so the resolver's key rings stay empty.
    harness = SimpleNamespace(
        campaign=item.campaign,
        service=item.arguments[1],
        rings=SimpleNamespace(general=None, public=None),
    )
    principal = user("admin@example.org")

    def unknown(message, task_id):
        """Send one attempt to an unknown provider outcome and drain its task."""
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=task_id))
            assert begin(message, execution) is not None
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
            )
        act(_status(TaskRun.objects.get(pk=task_id)), "permanent_failure")
        message.refresh_from_db()
        assert message.state == "delivery_unknown"

    def resend(message):
        """Authorize the resend and return the retry task's admission verdict."""
        assert retry_admitted(message) is True
        command = resolve(
            harness, principal, message, "resend", general=None, public=None
        )
        message.refresh_from_db()
        assert message.state == "pending"
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=command.retry_task_id))
            return command, execution, begin(message, execution)

    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(hours=1)
    ):
        receipt = submit_while_paused(item, settings).outbox
        unknown(receipt, receipt.task_id)
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Hold mail")
            commands.confirm(*item.arguments, token=token)
    with campaign_clock(
        item.campaign.active_configuration.starts_at + timedelta(days=9)
    ):
        # A held weekly report keeps the closed pause in place after receipts
        # are released, so the released-receipt case is observed while paused.
        status = queue_weekly(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        close_campaign(item.campaign, uuid4())
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        command, execution, attempt = resend(receipt)
        assert attempt is None and receipt.pause_hold_id is not None
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            _, token = commands.preview_resolution(
                *item.arguments,
                reason="Release receipts",
                decision="release",
                types=["receipt"],
            )
            assert commands.confirm(*item.arguments, token=token).control_id is None
        item.campaign.refresh_from_db()
        receipt.refresh_from_db()
        assert item.campaign.delivery_paused and receipt.pause_hold_id is None
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert begin(receipt, execution) is not None
            finish_submission(
                receipt.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
            )
        act(_status(TaskRun.objects.get(pk=command.retry_task_id)), "permanent_failure")
        receipt.refresh_from_db()
        _, _, attempt = resend(receipt)
        assert attempt is not None and receipt.pause_hold_id is None


@pytest.mark.parametrize("closed", [False, True])
def test_confirmed_unsent_receipt_clears_the_resume_guard(
    scheduled, settings, monkeypatch, tmp_path, closed
):
    """The real Admin resume refuses an unknown receipt until it is settled.

    Recording the provider's evidence that the receipt was not sent settles it
    without a resend, including on a campaign closed while paused.
    """
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_delivery_resolution_postgresql import (
        confirmed_unsent,
        resolve,
        unknown_inventory,
    )
    from .test_policy_postgresql import user
    from .test_taskrun_postgresql import act

    item = scheduled
    # Receipt resolution is keyless, so the resolver's key rings stay empty.
    harness = SimpleNamespace(
        campaign=item.campaign,
        service=item.arguments[1],
        rings=SimpleNamespace(general=None, public=None),
    )
    starts = item.campaign.active_configuration.starts_at
    with campaign_clock(starts + timedelta(hours=1)):
        receipt = submit_while_paused(item, settings).outbox
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=receipt.task_id))
            assert begin(receipt, execution) is not None
            finish_submission(
                receipt.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
            )
        act(_status(TaskRun.objects.get(pk=receipt.task_id)), "permanent_failure")
        receipt.refresh_from_db()
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Hold mail")
            commands.confirm(*item.arguments, token=token)
        assert unknown_inventory(item.campaign) == 1
        if not closed:
            accepted_sender_check(item, monkeypatch, tmp_path)
            with web_login(), pytest.raises(StaleRecordError):
                commands.preview_resume(*item.arguments, reason="Too early")
    clock = starts + timedelta(hours=2)
    if closed:
        close_campaign(item.campaign, uuid4())
        clock = item.campaign.active_configuration.ends_at + timedelta(hours=1)
    with campaign_clock(clock):
        command = resolve(
            harness,
            user("admin@example.org"),
            receipt,
            "confirm_unsent",
            general=None,
            public=None,
        )
        confirmed_unsent(receipt, command)
        assert unknown_inventory(item.campaign) == 0
        with web_login():
            if closed:
                # Closed campaigns cannot resume; the settled receipt leaves
                # nothing held or unknown, so the closed resolution clears it.
                _, token = commands.preview_resolution(
                    *item.arguments, reason="Settled", decision="clear", types=[]
                )
            else:
                _, token = commands.preview_resume(*item.arguments, reason="Settled")
            assert commands.confirm(*item.arguments, token=token).control_id
        item.campaign.refresh_from_db()
        assert not item.campaign.delivery_paused


@pytest.mark.parametrize("removed", [True, False])
@pytest.mark.parametrize("kind", ["daily_digest", "weekly_digest"])
def test_closed_skip_counts_only_a_removed_recipients_failed_report(
    scheduled, settings, kind, removed
):
    """A failed report to a removed Admin cannot block the closed skip.

    That report can never be retried, so the closed held-message resolution
    counts it as settled, exactly like a recipient_revoked cancellation, and
    cancelling the rest of the report records the occurrence's semantic skip.
    A failed report whose recipient is still an Administrator is not settled:
    the closed proof is false, no skip is recorded and the occurrence stays
    pending.
    """
    from django.db.models import Case, When

    from parishkit.stewardship.accounts.policy_models import PortalUser
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_daily_digest_dispatch_postgresql import allocated as allocate_daily
    from .test_daily_digest_dispatch_postgresql import revoke_admin
    from .test_delivery_resolution_postgresql import (
        confirmed_unsent,
        resolve,
        unknown_inventory,
    )
    from .test_taskrun_postgresql import act

    item = scheduled
    harness = SimpleNamespace(
        campaign=item.campaign,
        service=item.arguments[1],
        rings=SimpleNamespace(general=None, public=None),
    )
    starts = item.campaign.active_configuration.starts_at
    daily = kind == "daily_digest"
    with campaign_clock(
        starts + (timedelta(days=2, hours=12) if daily else timedelta(days=9))
    ):
        if daily:
            allocate_daily(harness)
            model = DailyDigestRecipient
            occurrence_path = "ready__snapshot__preparation__occurrence_id"
        else:
            submit_while_paused(item, settings, text="Unsent weekly request")
            status = queue_weekly(harness)
            with task_login(ServiceRole.WORKER, exact=True):
                execute_weekly(status)
            model = WeeklyDigestRecipient
            occurrence_path = "snapshot__preparation__occurrence_id"
        # Fail the copy of the Admin who is not driving the control session.
        admin = PortalUser.objects.get(pk=item.arguments[0].portal_session.principal_id)
        held, unsent = model.objects.select_related("outbox").order_by(
            Case(When(address=admin.email, then=0), default=1)
        )
        occurrence_id = model.objects.filter(pk=held.pk).values_list(
            occurrence_path, flat=True
        )[0]
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(unsent.outbox)
            assert begin(unsent.outbox, execution) is not None
            finish_submission(
                unsent.outbox_id,
                execution.claim,
                FamilyDeliveryResult(Status.UNKNOWN, 1),
            )
        act(_status(TaskRun.objects.get(pk=unsent.outbox.task_id)), "permanent_failure")
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Hold reports")
            commands.confirm(*item.arguments, token=token)
        if removed:
            revoke_admin(harness, unsent)
        message = OutboxMessage.objects.get(pk=unsent.outbox_id)
        command = resolve(
            harness, admin, message, "confirm_unsent", general=None, public=None
        )
        confirmed_unsent(message, command)
        assert unknown_inventory(item.campaign) == 0
        close_campaign(item.campaign, uuid4())
    with (
        campaign_clock(item.campaign.active_configuration.ends_at + timedelta(hours=1)),
        web_login(),
    ):
        _, token = commands.preview_resolution(
            *item.arguments,
            reason="Cancel the remaining report",
            decision="cancel",
            types=[kind],
        )
        commands.confirm(*item.arguments, token=token)
    assert OutboxMessage.objects.get(pk=held.outbox_id).state == "cancelled"

    def settled():
        """Read the closed digest proof for this occurrence."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_delivery_closed_digest_v1(%s)", [occurrence_id]
            )
            return cursor.fetchone()[0]

    assert settled() is removed
    assert (
        PostCloseMailResolution.objects.filter(occurrence_id=occurrence_id).exists()
        is removed
    )
    assert ScheduleOccurrence.objects.get(pk=occurrence_id).state == (
        "skipped" if removed else "pending"
    )
    if not removed:
        # The documented ordering: once the closed resolution has cancelled
        # the other copies, a later removal settles the proof but can no
        # longer record the skip, so the report stays pending.
        with campaign_clock(
            item.campaign.active_configuration.ends_at + timedelta(hours=2)
        ):
            revoke_admin(harness, unsent)
        assert settled() is True
        assert not PostCloseMailResolution.objects.filter(
            occurrence_id=occurrence_id
        ).exists()
        assert ScheduleOccurrence.objects.get(pk=occurrence_id).state == "pending"
