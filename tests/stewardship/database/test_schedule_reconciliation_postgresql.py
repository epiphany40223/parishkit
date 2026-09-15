"""Schedule selection owns safe cancellation without rewriting delivery history."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.schedule_preview import fingerprint, work_summary
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.storage import change_run, retry_failed
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import advance, campaign_clock, claimed_task, occurrence
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_outbox_boundaries_postgresql import sealed_inputs  # noqa: F401
from .test_outbox_postgresql import change, claim, permit, provider_evidence, submit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scheduled_delivery(sealed_inputs, auth_service):  # noqa: F811
    """Bind a synthetic sealed message to real occurrence and installer records."""
    options, _ = sealed_inputs
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(
            definition,
            options["actor_id"],
            target=f"family:{options['identity'].family_id}",
        )
        message = create_message(**options)
        type(row).objects.filter(pk=row.pk).update(
            outbox_id=message.message_id, version=F("version") + 1
        )
        row.refresh_from_db()
        yield auth_service.store, definition, row, message, options["actor_id"]


def replace_schedule(store, definition, actor, *, remove=False):
    """Apply a real configuration intent; never mutate the revision directly."""
    operation = dict(
        operation="remove" if remove else "update",
        section="schedules",
        id=str(definition.pk),
    )
    if not remove:
        operation["values"] = {"time": "10:00:00"}
    receipt = record_request(
        base_digest=store.active().digest,
        patch=[operation],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    return install_request(store, request_id=receipt.request_id, correlation_id=uuid4())


@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("waiting", [False, True])
def test_selection_cancels_proven_unsent_delivery(scheduled_delivery, remove, waiting):
    """Pending/backoff mail is scrubbed atomically; prior renders/events survive."""
    store, definition, row, message, actor = scheduled_delivery
    if waiting:
        message = change(
            submit(message),
            Action.RETRY_UNACCEPTED,
            retry_seconds=30,
            evidence=provider_evidence(),
        )
    before = work_summary(definition.campaign_id)[str(definition.pk)]
    assert before["blocking"] == 0 and before["cancellable"] == 1
    assert replace_schedule(store, definition, actor, remove=remove).state == "applied"
    retained = OutboxMessage.objects.get(pk=message.message_id)
    row.refresh_from_db()
    assert retained.state == "cancelled" and row.state == "skipped"
    assert (
        retained.reason
        == row.reason
        == ("schedule_removed" if remove else "schedule_replaced")
    )
    assert retained.sealed_substitutions is None and retained.sealed_key_id is None
    assert retained.renders.count() == 1
    assert retained.events.order_by("version").last().action == "cancel_unsent"
    assert TaskRun.objects.get(pk=message.task_id).state == (
        "running" if waiting else "cancelled"
    )
    evidence = AuditContext.objects.get(schema="schedule").context
    assert evidence["cancelled_messages"] == evidence["skipped_occurrences"] == 1
    assert evidence["previous_revision_id"] == str(row.revision_id)
    assert (
        "selected_revision_id" in evidence
        if not remove
        else "selected_revision_id" not in evidence
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM stewardship_schedule_effect")
        assert cursor.fetchone() == (0,)


@pytest.mark.parametrize(
    "state", ["submitting", "delivery_unknown", "retry_idempotent"]
)
def test_uncertain_acceptance_blocks_before_yaml_selection(scheduled_delivery, state):
    """Backoff is not proof of nonacceptance for an idempotent uncertain attempt."""
    store, definition, row, message, actor = scheduled_delivery
    message = submit(message)
    if state == "delivery_unknown":
        message = change(message, Action.MARK_UNKNOWN)
    elif state == "retry_idempotent":
        message = change(
            message,
            Action.RETRY_IDEMPOTENT,
            retry_seconds=30,
            evidence=provider_evidence(),
        )
    before = store.active()
    assert work_summary(definition.campaign_id)[str(definition.pk)]["blocking"] == 1
    with pytest.raises(CampaignAdmissionUnavailable):
        replace_schedule(store, definition, actor)
    assert store.active() == before
    retained = OutboxMessage.objects.get(pk=message.message_id)
    assert retained.state == message.state and retained.sealed_substitutions is not None
    row.refresh_from_db()
    assert row.state == "pending"


def test_running_local_owner_is_not_impersonated(scheduled_delivery):
    """An exact transaction proof closes local work but keeps worker lease history."""
    store, definition, row, message, actor = scheduled_delivery
    run = claimed_task("schedule_occurrence", row.pk, actor)
    row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    delivery_run = claim(message)
    assert replace_schedule(store, definition, uuid4()).state == "applied"
    row.refresh_from_db()
    assert row.state == "skipped" and row.worker_id == actor and row.fence == run.fence
    assert TaskRun.objects.get(pk=run.run_id).state == "running"
    assert TaskRun.objects.get(pk=delivery_run.run_id).state == "running"
    with pytest.raises(StaleRecordError, match="version changed"):
        submit(message, task=delivery_run)


def test_outbox_only_changes_invalidate_preview(scheduled_delivery):
    """Message versions are part of confirmation even without occurrence writes."""
    _, definition, row, message, _ = scheduled_delivery
    original = work_summary(definition.campaign_id)
    change(
        submit(message),
        Action.RETRY_UNACCEPTED,
        retry_seconds=30,
        evidence=provider_evidence(),
    )
    updated = work_summary(definition.campaign_id)
    key = str(definition.pk)
    assert original[key]["versions"] == updated[key]["versions"] == row.version
    assert original[key]["outbox_versions"] < updated[key]["outbox_versions"]
    assert fingerprint("domain", original) != fingerprint("domain", updated)


def test_failed_occurrence_stays_failed_and_loses_retry(scheduled_delivery):
    """Replacement supersedes identity; it never turns failure into a skipped result."""
    store, definition, row, _, actor = scheduled_delivery
    run = claimed_task("schedule_occurrence", row.pk, actor)
    row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    row = advance(row, actor, "failed", fence=run.fence, reason="definitive_failure")
    assert replace_schedule(store, definition, actor).state == "applied"
    row.refresh_from_db()
    assert row.state == "failed" and row.reason == "definitive_failure"
    with pytest.raises(IntegrityError, match="explicit current identity"):
        advance(row, actor, "pending", reason="admin_retry", retry_command_id=uuid4())
    assert (
        AuditContext.objects.get(schema="schedule").context["failed_occurrences"] == 1
    )


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.CONFIG_INSTALLER])
def test_counts_only_boundary_has_no_private_read_or_effect_authority(
    scheduled_delivery, role
):
    """Actual restricted sessions get counts, never delivery payloads or proofs."""
    _, definition, row, _, actor = scheduled_delivery
    expected = work_summary(definition.campaign_id)
    with task_login(role):
        assert work_summary(definition.campaign_id) == expected
        for relation in (
            "stewardship_schedule_work_row",
            "stewardship_schedule_effect",
            "stewardship_outbox_message",
            "stewardship_outbox_render",
        ):
            with (
                pytest.raises(ProgrammingError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(f"SELECT * FROM {relation}")
        with connection.cursor() as cursor:
            for function in (
                "stewardship_schedule_effect_v1(uuid,bigint,uuid,uuid,text)",
                "stewardship_schedule_reconcile_v1(uuid,uuid,uuid,uuid)",
                "stewardship_schedule_selection_effect_v1()",
                "stewardship_occurrence_guard_v1()",
                "stewardship_schedule_definition_v1()",
            ):
                cursor.execute(
                    "SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                    [function],
                )
                assert cursor.fetchone() == (False,)
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO stewardship_schedule_effect VALUES "
                "(pg_current_xact_id(),pg_backend_pid(),%s,%s,%s,%s,'schedule_removed')",
                [row.pk, row.version, actor, uuid4()],
            )


def test_restricted_installer_owns_exact_cancellation_effect(scheduled_delivery):
    """Narrow trigger ownership works without granting generic outbox mutation."""
    store, definition, row, message, actor = scheduled_delivery
    # Request insertion is the web's authority; the installer only consumes it.
    receipt = record_request(
        base_digest=store.active().digest,
        patch=[dict(operation="remove", section="schedules", id=str(definition.pk))],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    with task_login(ServiceRole.CONFIG_INSTALLER):
        assert (
            install_request(
                store, request_id=receipt.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
    row.refresh_from_db()
    assert row.state == "skipped"
    assert OutboxMessage.objects.get(pk=message.message_id).state == "cancelled"


@pytest.mark.parametrize(
    "malformed", ["missing_message", "other_family", "shared_message", "wrong_task"]
)
def test_malformed_link_cannot_cancel_unrelated_work(scheduled_delivery, malformed):
    """An opaque pointer alone cannot prove ownership of a cancellation target."""
    store, definition, row, message, actor = scheduled_delivery
    if malformed == "missing_message":
        type(row).objects.filter(pk=row.pk).update(
            outbox_id=uuid4(), version=F("version") + 1
        )
    elif malformed == "wrong_task":
        type(row).objects.filter(pk=row.pk).update(
            task_id=message.task_id, version=F("version") + 1
        )
    else:
        other = occurrence(definition, actor, target="family:another")
        if malformed == "other_family":
            type(row).objects.filter(pk=row.pk).update(
                outbox_id=None, version=F("version") + 1
            )
        type(other).objects.filter(pk=other.pk).update(
            outbox_id=message.message_id, version=F("version") + 1
        )
    before = store.active()
    assert work_summary(definition.campaign_id)[str(definition.pk)]["blocking"] > 0
    with pytest.raises(CampaignAdmissionUnavailable):
        replace_schedule(store, definition, actor)
    assert store.active() == before
    assert OutboxMessage.objects.get(pk=message.message_id).state == "pending"
    assert not AuditContext.objects.filter(schema="schedule").exists()


def test_forged_effect_reason_does_not_impersonate_a_live_worker(scheduled_delivery):
    """The narrow proof is transaction-owned, not a magic reason or actor field."""
    _, _, row, _, actor = scheduled_delivery
    run = claimed_task("schedule_occurrence", row.pk, actor)
    row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    with (
        pytest.raises(IntegrityError, match="live worker fencing"),
        transaction.atomic(),
    ):
        type(row).objects.filter(pk=row.pk).update(
            state="skipped",
            reason="schedule_removed",
            version=F("version") + 1,
            lease_expires_at=None,
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
    row.refresh_from_db()
    assert row.state == "running"


def test_old_failed_delivery_cannot_be_rearmed_after_replacement(
    scheduled_delivery,
    sealed_inputs,  # noqa: F811
):
    """Even a fresh retry-root command cannot bypass superseded schedule identity."""
    store, definition, row, message, actor = scheduled_delivery
    failed = change(
        submit(message), Action.FAIL_UNACCEPTED, evidence=provider_evidence()
    )
    task = TaskRun.objects.get(pk=failed.task_id)
    change_run(
        run_id=task.pk,
        action="permanent_failure",
        expected_version=task.version,
        actor_id=task.worker_id,
        fence=task.fence,
        correlation_id=uuid4(),
        admit=permit,
    )
    run = claimed_task("schedule_occurrence", row.pk, actor)
    row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    advance(row, actor, "failed", fence=run.fence, reason="definitive_failure")
    assert replace_schedule(store, definition, actor).state == "applied"
    options, reseal = sealed_inputs
    with (
        pytest.raises(IntegrityError, match="schedule is no longer current"),
        work_transaction(),
    ):
        retry_failed(
            run_id=task.pk,
            command_id=uuid4(),
            actor_id=actor,
            correlation_id=uuid4(),
            admit=permit,
        )
        change(failed, Action.RETRY_FAILED, render=options["render"], sealed=reseal())
    retained = OutboxMessage.objects.get(pk=message.message_id)
    assert retained.state == "permanent_failure" and retained.renders.count() == 1
    assert TaskRun.objects.filter(root_id=task.pk).count() == 1


def test_late_effect_failure_rolls_back_revision_and_cancellation(scheduled_delivery):
    """A failure after delivery updates cannot leave half of selection committed."""
    store, definition, row, message, actor = scheduled_delivery
    previous = definition.current_revision_id
    # This constraint exists only in the disposable test database. It injects a
    # failure at the final audit write, after message/occurrence/task effects.
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_audit_context "
            "ADD CONSTRAINT test_reject_schedule_audit CHECK (schema<>'schedule')"
        )
    try:
        with pytest.raises(IntegrityError, match="test_reject_schedule_audit"):
            replace_schedule(store, definition, actor)
        definition.refresh_from_db()
        row.refresh_from_db()
        retained = OutboxMessage.objects.get(pk=message.message_id)
        assert definition.current_revision_id == previous and row.state == "pending"
        assert retained.state == "pending" and retained.sealed_substitutions is not None
        assert retained.events.count() == 1
        assert TaskRun.objects.get(pk=message.task_id).state == "queued"
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM stewardship_schedule_effect")
            assert cursor.fetchone() == (0,)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_audit_context "
                "DROP CONSTRAINT test_reject_schedule_audit"
            )
