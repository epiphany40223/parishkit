"""Real original-owner Slack intent, isolated submission and uncertain recovery."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts import setup_notifications as notifications
from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_notification_models import SetupSlackDelivery
from parishkit.stewardship.accounts.setup_preview import PREVIEW_SALT, prepare_preview
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.provider_checks import ProviderCheckOwnershipLost
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import publish
from .test_setup_exchange_postgresql import target_login
from .test_setup_preview_postgresql import complete_draft
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def ready(service, monkeypatch, tmp_path, *, enqueue=True):
    """Complete the actual wizard and stage only a synthetic, isolated Slack token."""
    request, status, _ = complete_draft(service, monkeypatch, tmp_path)
    publish("slack", material=b"n" * 32)
    with web_login():
        status = save_section(
            request,
            service,
            status.attempt_id,
            step="slack",
            values={"enabled": True, "channel_id": "CFIXTURE"},
            expected_version=status.version,
        )
        status, _ = stage_credential(
            request,
            service,
            status.attempt_id,
            target="slack",
            candidate=b"synthetic-private-slack",
            expected_version=status.version,
        )
        token = signing.dumps(
            prepare_preview(request, service).binding(), salt=PREVIEW_SALT
        )
        request_key = uuid4()
        identifier = (
            notifications.request_notification(
                request, service, preview_token=token, request_key=request_key
            )
            if enqueue
            else None
        )
    return request, status, token, request_key, identifier


def test_original_request_replay_is_inert_and_other_pending_intents_are_rejected(
    setup_service, monkeypatch, tmp_path
):
    """The web request only journals intent; it cannot submit or claim acceptance."""
    request, _, token, request_key, identifier = ready(
        setup_service, monkeypatch, tmp_path
    )
    with web_login():
        assert (
            notifications.request_notification(
                request, setup_service, preview_token=token, request_key=request_key
            )
            == identifier
        )
        with pytest.raises(StaleRecordError):
            notifications.request_notification(
                request, setup_service, preview_token=token, request_key=uuid4()
            )
        with pytest.raises(DatabaseError), work_transaction():
            SetupSlackDelivery.objects.update(
                state="accepted", version=F("version") + 1
            )
    assert SetupSlackDelivery.objects.count() == 1
    assert not setup_service.configured()


def test_collecting_slack_keeps_original_login_lifetime_after_loading(
    setup_service, monkeypatch, tmp_path
):
    """A completed source load's age does not prematurely expire Slack testing."""
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    from .test_setup_progress_postgresql import aged_original_load

    request, _, token, key, _ = ready(
        setup_service, monkeypatch, tmp_path, enqueue=False
    )
    aged_original_load(SetupAttempt.objects.get().source_task_id, minutes=121)
    with web_login():
        identifier = notifications.request_notification(
            request, setup_service, preview_token=token, request_key=key
        )
    assert SetupSlackDelivery.objects.get(pk=identifier).state == "queued"


@pytest.mark.parametrize("outcome", [*DeliveryOutcome, RuntimeError("private")])
def test_isolated_target_commits_before_one_fake_submission(
    setup_service, monkeypatch, tmp_path, outcome
):
    """Real private decryption and SQL grants end at a fake external provider call."""
    ready(setup_service, monkeypatch, tmp_path)
    calls = []

    def provider(value, notification, *, seconds, check):
        """The helper boundary sees committed intent, exact channel and no open TX."""
        assert not connection.in_atomic_block
        assert SetupSlackDelivery.objects.get().state == "submitting"
        assert (
            value == b"synthetic-private-slack"
            and notification.channel_id == "CFIXTURE"
        )
        assert 0 < seconds <= 30
        check()
        calls.append(notification.delivery_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(notifications, "submit_notification", provider)
    with target_login("slack", reconnect=True):
        assert notifications.run_pending(
            key("slack", material=b"n" * 32), check=lambda: None
        )
        assert not notifications.run_pending(
            key("slack", material=b"n" * 32), check=lambda: None
        )
    assert len(calls) == 1
    assert SetupSlackDelivery.objects.get().state == (
        outcome.value if isinstance(outcome, DeliveryOutcome) else "delivery_unknown"
    )


def test_unknown_requires_explicit_new_acknowledged_request(
    setup_service, monkeypatch, tmp_path
):
    """Only another deliberate command may follow a permanently uncertain result."""
    request, _, token, _, _ = ready(setup_service, monkeypatch, tmp_path)
    monkeypatch.setattr(
        notifications, "submit_notification", Mock(return_value=DeliveryOutcome.UNKNOWN)
    )
    with target_login("slack", reconnect=True):
        notifications.run_pending(key("slack", material=b"n" * 32), check=lambda: None)
    with web_login():
        with pytest.raises(ValueError, match="acknowledge"):
            notifications.request_notification(
                request, setup_service, preview_token=token, request_key=uuid4()
            )
        notifications.request_notification(
            request,
            setup_service,
            preview_token=token,
            request_key=uuid4(),
            acknowledge_unknown=True,
        )
    assert SetupSlackDelivery.objects.count() == 2


def test_cancelled_unsent_intent_never_reaches_provider(
    setup_service, monkeypatch, tmp_path
):
    """Expired setup invalidates queued notification scope before decryption."""
    request, attempt, _, _, _ = ready(setup_service, monkeypatch, tmp_path)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    provider = Mock()
    monkeypatch.setattr(notifications, "submit_notification", provider)
    with target_login("slack", reconnect=True):
        assert not notifications.run_pending(
            key("slack", material=b"n" * 32), check=lambda: None
        )
    assert SetupSlackDelivery.objects.get().state == "cancelled"
    provider.assert_not_called()


def test_lost_owner_retains_submission_until_timed_metadata_recovery(
    setup_service, monkeypatch, tmp_path
):
    """No automatic replay follows a lost helper, even when the setup is still live."""
    ready(setup_service, monkeypatch, tmp_path)
    provider = Mock(side_effect=ProviderCheckOwnershipLost("Ownership lost."))
    monkeypatch.setattr(notifications, "submit_notification", provider)
    with (
        target_login("slack", reconnect=True),
        pytest.raises(ProviderCheckOwnershipLost),
    ):
        notifications.run_pending(key("slack", material=b"n" * 32), check=lambda: None)
    assert SetupSlackDelivery.objects.get().state == "submitting"
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert notifications.recover_pending() == 0
    age_notification()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert notifications.recover_pending() == 1
        assert notifications.recover_pending() == 0
    assert SetupSlackDelivery.objects.get().state == "delivery_unknown"
    provider.assert_called_once()


def age_notification():
    """Install elapsed fixture instants with all guards restored before settlement."""
    # Install historical fixture instants, then restore all production guards.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_setup_slack_delivery DISABLE TRIGGER USER"
        )
        try:
            cursor.execute(
                "UPDATE stewardship_setup_slack_delivery "
                "SET deadline_at=deadline_at-%s",
                [timedelta(minutes=1)],
            )
        finally:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE stewardship_setup_slack_delivery ENABLE TRIGGER USER"
            )


def test_slack_deadline_crossing_settles_unknown_in_same_owned_call(
    setup_service, monkeypatch, tmp_path
):
    """A Python pre-deadline read cannot defeat the SQL result-time boundary."""
    from parishkit.stewardship.accounts import delivery_results

    ready(setup_service, monkeypatch, tmp_path)

    def accepted(*args, **kwargs):
        """Model elapsed provider time only as the disposable schema owner."""
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            age_notification()
            deadline = SetupSlackDelivery.objects.get().deadline_at
            ticks = iter(
                [deadline - timedelta(seconds=1), notifications.database_now()]
            )
            monkeypatch.setattr(delivery_results, "database_now", lambda: next(ticks))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SET SESSION AUTHORIZATION "pk_stewardship_credential_slack"'
                )
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(notifications, "submit_notification", accepted)
    with target_login("slack", reconnect=True):
        assert notifications.run_pending(
            key("slack", material=b"n" * 32), check=lambda: None
        )
    assert SetupSlackDelivery.objects.get().state == "delivery_unknown"


def test_changed_channel_cancels_old_intent_instead_of_retargeting_it(
    setup_service, monkeypatch, tmp_path
):
    """A new public channel is never silently substituted into an earlier request."""
    request, attempt, _, _, _ = ready(setup_service, monkeypatch, tmp_path)
    with web_login():
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="slack",
            values={"enabled": True, "channel_id": "COTHER"},
            expected_version=attempt.version,
        )
    send = Mock()
    monkeypatch.setattr(notifications, "submit_notification", send)
    with target_login("slack", reconnect=True):
        assert not notifications.run_pending(
            key("slack", material=b"n" * 32), check=lambda: None
        )
    assert SetupSlackDelivery.objects.get().state == "cancelled"
    send.assert_not_called()


def test_unrelated_service_cannot_submit_notification(
    setup_service, monkeypatch, tmp_path
):
    """The Slack journal adds no private authority to Workspace or general workers."""
    ready(setup_service, monkeypatch, tmp_path)
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as denied,
        transaction.atomic(),
    ):
        SetupSlackDelivery.objects.update(state="submitting", version=F("version") + 1)
    assert denied.value.__cause__.sqlstate == "42501"
    with (
        target_login("google_workspace"),
        pytest.raises(DatabaseError) as denied,
        transaction.atomic(),
    ):
        SetupSlackDelivery.objects.update(state="submitting", version=F("version") + 1)
    assert denied.value.__cause__.sqlstate == "42501"


def test_slack_target_has_equality_evidence_not_unrelated_draft_values(
    setup_service, monkeypatch, tmp_path
):
    """Target grants admit its live notification without exposing public drafts."""
    ready(setup_service, monkeypatch, tmp_path)
    with target_login("slack"):
        with (
            pytest.raises(DatabaseError) as denied,
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT values FROM stewardship_setup_draft_section")
        assert denied.value.__cause__.sqlstate == "42501"
        with connection.cursor() as cursor:
            cursor.execute("SELECT step FROM stewardship_setup_draft_section")
            assert cursor.fetchall() == [("slack",)]
        with work_transaction():
            assert notifications.live(SetupSlackDelivery.objects.get())


def test_wrong_private_key_records_unsent_without_a_provider_call(
    setup_service, monkeypatch, tmp_path
):
    """A failed local unwrap is known to precede any submission to Slack."""
    ready(setup_service, monkeypatch, tmp_path)
    send = Mock()
    monkeypatch.setattr(notifications, "submit_notification", send)
    with target_login("slack", reconnect=True):
        assert notifications.run_pending(
            key("slack", material=b"z" * 32), check=lambda: None
        )
        with pytest.raises(DatabaseError), work_transaction():
            SetupSlackDelivery.objects.update(state="queued", version=F("version") + 1)
    assert SetupSlackDelivery.objects.get().state == "not_sent"
    send.assert_not_called()
