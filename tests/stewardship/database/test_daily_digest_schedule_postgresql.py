"""Schedule edits reconcile every individually addressed daily digest message."""

from datetime import timedelta
from functools import partial
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.schedule_preview import work_summary
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.cleanup_catalog import iter_inventory
from parishkit.stewardship.campaigns.production_models import ProductionCleanupTarget
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleRecoveryReplacement,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import _status
from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_building import (
    admit_daily_facts,
    begin_daily_facts,
    load_daily_document,
    retain_daily_content,
)
from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
from parishkit.stewardship.reports.digest_fanout import fanout_daily
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestRecipient,
)
from parishkit.stewardship.reports.digest_planning import cover_dates, discover_dates
from parishkit.stewardship.reports.materialization import materialize_fact_set

from .campaign_builders import campaign_clock
from .campaign_builders import change as config_change
from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_daily_digest_fanout_postgresql import ready_report
from .test_daily_digest_planning_postgresql import INSTANT, allocate
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_outbox_postgresql import change, provider_evidence, submit
from .test_schedule_reconciliation_postgresql import replace_schedule

pytestmark = pytest.mark.django_db(transaction=True)


def messages(harness):
    """Use actual worker allocation; provider outcomes below remain synthetic."""
    claim, _ = ready_report(harness, additional_admins=("second@example.org",))
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        fanout_daily(claim)
    return ScheduleDefinition.objects.get(kind="daily_digest"), list(
        OutboxMessage.objects.order_by("pk")
    )


def replacement_report():
    """Run the real current-revision recovery owner after configuration edits."""
    claim = allocate()
    with task_login(ServiceRole.WORKER, exact=True):
        for _ in range(20):
            row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
            if row.phase == "facts":
                break
            with work_transaction():
                (discover_dates if row.phase == "dates" else cover_dates)(claim)
        else:
            pytest.fail("Replacement discovery did not finish its bounded pages.")
        with work_transaction():
            capture_daily_snapshot(claim)
            facts = begin_daily_facts(claim)
        if facts.state == "building":
            materialize_fact_set(
                facts.pk, claim, admit=partial(admit_daily_facts, claim)
            )
        with work_transaction():
            document = load_daily_document(claim, facts.pk)
        content = render_daily_digest(document, public_origin="https://parish.example")
        with work_transaction():
            ready = retain_daily_content(claim, document, content)
    return claim, ready


@pytest.mark.parametrize("remove", [False, True])
def test_revision_cancels_all_unsent_admin_messages(family_mail, remove):  # noqa: F811
    with campaign_clock(INSTANT):
        definition, rows = messages(family_mail)
        with task_login(ServiceRole.WEB, exact=True):
            summary = work_summary(family_mail.campaign.pk)[str(definition.pk)]
        assert summary["outboxes"] == 2 and summary["blocking"] == 0
        assert (
            replace_schedule(
                family_mail.service.store, definition, uuid4(), remove=remove
            ).state
            == "applied"
        )
        for row in rows:
            row.refresh_from_db()
            assert row.state == "cancelled"
            assert row.reason == ("schedule_removed" if remove else "schedule_replaced")
            assert TaskRun.objects.get(pk=row.task_id).state == "cancelled"


@pytest.mark.parametrize(
    "outcome", ["submitting", "delivery_unknown", "retry_idempotent"]
)
def test_one_uncertain_admin_message_blocks_schedule_change(family_mail, outcome):  # noqa: F811
    with campaign_clock(INSTANT):
        definition, rows = messages(family_mail)
        message = submit(_status(rows[0]))
        if outcome != "submitting":
            message = change(
                message,
                DeliveryAction.MARK_UNKNOWN
                if outcome == "delivery_unknown"
                else DeliveryAction.RETRY_IDEMPOTENT,
                **(
                    {}
                    if outcome == "delivery_unknown"
                    else {"retry_seconds": 30, "evidence": provider_evidence()}
                ),
            )
        store = family_mail.service.store
        before = store.active()
        assert (
            work_summary(family_mail.campaign.pk)[str(definition.pk)]["blocking"] == 1
        )
        with pytest.raises(CampaignAdmissionUnavailable):
            replace_schedule(store, definition, uuid4())
        assert store.active() == before
        rows[1].refresh_from_db()
        assert rows[1].state == "pending"


def test_partial_acceptance_survives_cancellation_of_other_admin(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        definition, rows = messages(family_mail)
        accepted = change(
            submit(_status(rows[0])),
            DeliveryAction.ACCEPT,
            evidence=provider_evidence(),
        )
        assert (
            replace_schedule(family_mail.service.store, definition, uuid4()).state
            == "applied"
        )
        rows[0].refresh_from_db()
        rows[1].refresh_from_db()
        assert rows[0].state == "delivered" and rows[0].version == accepted.version
        assert rows[1].state == "cancelled"


@pytest.mark.parametrize("new_day", [False, True])
def test_replacement_retains_all_dates_and_exact_per_admin_acceptance(
    family_mail,  # noqa: F811
    new_day,
):
    with campaign_clock(INSTANT):
        definition, rows = messages(family_mail)
        accepted_recipient = DailyDigestRecipient.objects.get(outbox=rows[0])
        old_dates = list(accepted_recipient.ready.snapshot.covered_dates)
        change(
            submit(_status(rows[0])),
            DeliveryAction.ACCEPT,
            evidence=provider_evidence(),
        )
        assert (
            replace_schedule(family_mail.service.store, definition, uuid4()).state
            == "applied"
        )
    with campaign_clock(INSTANT + timedelta(days=int(new_day))):
        claim, ready = replacement_report()
        assert set(old_dates) <= set(ready.snapshot.covered_dates)
        assert len(ready.snapshot.covered_dates) == len(old_dates) + int(new_day)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        retained = DailyDigestRecipient.objects.get(
            ready=ready, address=accepted_recipient.address
        )
        if new_day:
            # Partial historical coverage must not suppress a new reporting day.
            assert retained.outbox_id and retained.covered_messages == []
        else:
            assert retained.outbox_id is None
            assert retained.covered_messages == [str(rows[0].pk)]
        other = (
            DailyDigestRecipient.objects.filter(ready=ready)
            .exclude(pk=retained.pk)
            .get()
        )
        assert other.outbox_id and other.covered_messages == []
        row = ready.snapshot.preparation
        assert [
            d.isoformat() for d in covered_dates(row.occurrence_id, mode=row.mode)
        ] == ready.snapshot.covered_dates


def test_forged_prior_acceptance_is_denied_by_database(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        claim, ready = ready_report(family_mail)
        with (
            task_login(ServiceRole.WORKER, exact=True),
            work_transaction(),
            pytest.raises(DatabaseError, match="exact accepted coverage"),
            transaction.atomic(),
        ):
            DailyDigestRecipient.objects.create(
                ready=ready,
                address=ready.recipients[0],
                covered_messages=[str(uuid4())],
                actor_id=claim.worker_id,
                correlation_id=claim_event(claim),
            )
        assert not DailyDigestRecipient.objects.exists()


def test_repeated_replacements_keep_lineage_and_testing_cleanup_complete(family_mail):  # noqa: F811
    """No rewrite, duplicate acceptance or orphaned Testing edge after re-editing."""
    with campaign_clock(INSTANT):
        definition, original = messages(family_mail)
        recipient = DailyDigestRecipient.objects.get(outbox=original[0])
        original_dates = recipient.ready.snapshot.covered_dates
        change(
            submit(_status(original[0])),
            DeliveryAction.ACCEPT,
            evidence=provider_evidence(),
        )
        store = family_mail.service.store
        for time in ("10:00:00", "11:00:00"):
            assert (
                config_change(
                    store,
                    store.active(),
                    uuid4(),
                    [
                        {
                            "operation": "update",
                            "section": "schedules",
                            "id": str(definition.pk),
                            "values": {"time": time},
                        }
                    ],
                ).state
                == "applied"
            )
            claim, ready = replacement_report()
            assert ready.snapshot.covered_dates == original_dates
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                fanout_daily(claim)
            assert DailyDigestRecipient.objects.get(
                ready=ready, address=recipient.address
            ).covered_messages == [str(original[0].pk)]
        assert ScheduleRecoveryReplacement.objects.count() == 2
        edge = ScheduleRecoveryReplacement.objects.first()
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM stewardship_recovery_replacement WHERE id=%s", [edge.pk]
            )
        assert (
            replace_schedule(store, definition, uuid4(), remove=True).state == "applied"
        )
        with work_transaction(), connection.cursor() as cursor:
            targets = {
                (t.category.value, t.identifier)
                for t in iter_inventory(family_mail.campaign.pk)
            }
            cursor.execute(
                "SELECT category,target_id FROM stewardship_cleanup_inventory_v1(%s)",
                [family_mail.campaign.pk],
            )
            assert targets == set(cursor.fetchall())
        assert ("recovery_replacements", edge.pk) in targets
        status = running_request(family_mail)
        for _ in range(status.inventory_total * 4):
            if not ProductionCleanupTarget.objects.filter(
                request_id=status.request_id
            ).exists():
                break
            assert delete_batch(status, maximum=2).deleted_count <= 2
        assert not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists()
        assert not ScheduleRecoveryReplacement.objects.exists()
        assert not DailyDigestRecipient.objects.exists()
