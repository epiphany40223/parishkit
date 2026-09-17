"""Real web/mail/scheduler identities for explicit campaign test-mail outcomes."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.campaign_mail_delivery import recover_pending
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.campaign_mail_tasks import campaign_mail_handler
from parishkit.stewardship.accounts.key_files import file_fingerprint, write_private
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.campaigns.models import Campaign, ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from ..content_factory import content
from . import campaign_builders
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)
KEY = b"synthetic-installed-workspace-credential"


@pytest.fixture
def campaign_test(request, monkeypatch, tmp_path, google):
    """A completed foundation fixture has installed synthetic public mail identity.

    Initial installation has its separate end-to-end suite. This fixture starts
    with root configuration, then uses real guarded draft/content activation;
    no test changes SQL guards or fabricates provider acceptance receipts.
    """
    original = campaign_builders.configuration_document

    def document():
        """Publish only non-secret mail settings before building initial authority."""
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
    service = request.getfixturevalue("auth_service")
    result, _, _ = campaign_builders.add_draft(
        service.store, service.store.active(), uuid4()
    )
    assert result.state == "applied"
    campaign = Campaign.objects.get()
    template = content(
        str(campaign.pk),
        kind="email",
        slot="initial",
        html="<p>Hello {{ family_name }} {{ family_code }} {{ family_url }}</p>",
        text="Hello {{ family_name }} {{ family_code }} {{ family_url }}",
    )
    assert (
        campaign_builders.change(
            service.store,
            service.store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **template}],
        ).state
        == "applied"
    )
    browser, response = signed_in()
    assert response.status_code == 302
    path = f"/admin/campaign/{campaign.pk}/content/test/{template['id']}"
    credential = tmp_path / "installed-workspace"
    write_private(credential, KEY)
    return service, browser, path, credential


def queue(campaign_test):
    """Real GET signs a fictional exact preview; explicit POST commits its task."""
    service, browser, path, _ = campaign_test
    with web_login():
        before = PortalSession.objects.get().last_activity_at
        page = browser.get(path)
        assert page.status_code == 200, page.content
        assert b"TEST" in page.content and b"Sample" in page.content
        assert b"synthetic-installed" not in page.content
        assert PortalSession.objects.get().last_activity_at == before
        token = page.context["form"]["preview_token"].value()
        assert browser.post(path, {"preview_token": token}).status_code == 403
        response = post(browser, path, {"preview_token": token})
        assert response.status_code == 302, response.content
        assert post(browser, path, {"preview_token": token}).status_code == 302
        assert CampaignMailTest.objects.count() == 1
        assert browser.get(path).context["pending"]
    return CampaignMailTest.objects.get(), token


def deliver(campaign_test):
    """Run the compiled handler with real claim and maintenance, never direct writes."""
    service, _, _, path = campaign_test
    row = CampaignMailTest.objects.latest("created_at")
    handler = campaign_mail_handler(service.store, credential_path=path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            row.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={"campaign_mail_test": handler},
        )
        assert execution is not None
        with maintain_execution(execution):
            handler.execute(execution)
    row.refresh_from_db()
    return row


def test_confirmation_sample_includes_fixed_facts_and_selected_optional_block(
    campaign_test,
):
    """The restricted Admin test-mail path previews the actual receipt composition."""
    service, browser, _, credential = campaign_test
    campaign = Campaign.objects.get()
    template = content(str(campaign.pk), kind="email", slot="confirmation")
    block = content(
        str(campaign.pk),
        slot="submission_confirmation",
        html="<p>Optional follow-up.</p>",
        text="Optional follow-up.",
    )
    assert (
        campaign_builders.change(
            service.store,
            service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **template},
                {"operation": "add", "section": "content", **block},
            ],
        ).state
        == "applied"
    )
    path = f"/admin/campaign/{campaign.pk}/content/test/{template['id']}"
    row, _ = queue((service, browser, path, credential))
    for body in (row.mail["html"], row.mail["text"]):
        assert "Submitted:" in body and "Questions:" in body
        assert "Optional follow-up." in body and "Sample Family" in body
        assert "sample-family" not in body


def test_cancellation_winning_submission_recheck_settles_immediately(
    campaign_test, monkeypatch
):
    """A real scheduler cancellation between effect and submission needs no expiry."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    row, _ = queue(campaign_test)
    original = tasks.begin_submission

    def cancel_before_begin(identifier, claim):
        """Model a separate revocation/recovery owner without changing SQL guards."""
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            PortalUser.objects.update(disabled=True, version=F("version") + 1)
            with task_login(ServiceRole.SCHEDULER, exact=True):
                assert recover_pending() == 1
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SET SESSION AUTHORIZATION "pk_stewardship_mail_dispatch"'
                )
        return original(identifier, claim)

    monkeypatch.setattr(tasks, "begin_submission", cancel_before_begin)
    assert deliver(campaign_test).state == "cancelled"
    task = TaskRun.objects.get(pk=row.task_id)
    assert task.state == "cancelled" and task.action == "safe_cancel"
    assert task.lease_expires_at is None


def test_campaign_deadline_crossing_settles_unknown_without_lease_expiry(
    campaign_test, monkeypatch
):
    """Late acceptance is uncertain immediately, not an abandoned running task."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks
    from parishkit.stewardship.accounts import delivery_results
    from parishkit.stewardship.accounts.sessions import database_now

    row, _ = queue(campaign_test)

    def accepted(*args, **kwargs):
        """Only this disposable owner ages a no-network journal fixture."""
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE stewardship_campaign_mail_test DISABLE TRIGGER USER"
                )
                cursor.execute(
                    "UPDATE stewardship_campaign_mail_test SET "
                    "submitted_at=submitted_at-%s,deadline_at=deadline_at-%s "
                    "WHERE id=%s",
                    [timedelta(minutes=3), timedelta(minutes=3), row.pk],
                )
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute(
                    "ALTER TABLE stewardship_campaign_mail_test ENABLE TRIGGER USER"
                )
            deadline = CampaignMailTest.objects.get(pk=row.pk).deadline_at
            ticks = iter([deadline - timedelta(seconds=1), database_now()])
            monkeypatch.setattr(delivery_results, "database_now", lambda: next(ticks))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SET SESSION AUTHORIZATION "pk_stewardship_mail_dispatch"'
                )
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(tasks, "submit_sample", accepted)
    assert deliver(campaign_test).state == "delivery_unknown"
    task = TaskRun.objects.get(pk=row.task_id)
    assert task.state == "failed" and task.lease_expires_at is None


@pytest.mark.parametrize("outcome", list(DeliveryOutcome))
def test_explicit_sample_submits_once_and_never_fulfills_schedule(
    campaign_test, monkeypatch, outcome
):
    """Provider results are terminal; a duplicate request never repeats submission."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    row, token = queue(campaign_test)
    seen = []

    def submit(value, settings, mail, *, seconds, check):
        """Replace provider I/O, retaining committed intent and helper admission."""
        check()
        actual = CampaignMailTest.objects.get(pk=row.pk)
        assert actual.state == "submitting" and actual.submitted_at is not None
        assert value == KEY and settings["recipient"] == "test@example.org"
        assert mail.recipient == "test@example.org"
        assert mail.message()["Subject"].startswith("[TEST]")
        seen.append(mail.delivery_id)
        return outcome

    monkeypatch.setattr(tasks, "submit_sample", submit)
    row = deliver(campaign_test)
    assert row.state == outcome.value and row.mail == {}
    assert seen == [row.pk]
    assert not ScheduleOccurrence.objects.exists()
    assert TaskRun.objects.get(pk=row.task_id).state == (
        "succeeded" if outcome is DeliveryOutcome.ACCEPTED else "failed"
    )
    _, browser, path, _ = campaign_test
    with web_login():
        assert post(browser, path, {"preview_token": token}).status_code == 302
        page = browser.get(path)
        assert page.status_code == 200 and not page.context["pending"]
    assert CampaignMailTest.objects.count() == 1


def test_wrong_installed_credential_is_unsent_then_scheduler_scrubs(
    campaign_test, monkeypatch
):
    """Local identity failure occurs before submitting and cannot be called accepted."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    queue(campaign_test)
    write_private(campaign_test[3], b"different-synthetic-value")

    def forbidden(*args, **kwargs):
        """No provider invocation is permitted after mounted identity differs."""
        pytest.fail("Unexpected provider call")

    monkeypatch.setattr(tasks, "submit_sample", forbidden)
    row = deliver(campaign_test)
    assert row.state == "queued" and row.submitted_at is None
    assert TaskRun.objects.get(pk=row.task_id).state == "failed"
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    row.refresh_from_db()
    assert row.state == "cancelled" and row.mail == {}


@pytest.mark.parametrize("removed", [False, True])
def test_installed_credential_change_during_helper_is_uncertain(
    campaign_test, monkeypatch, removed
):
    """An admitted helper must stop on its next pulse, never accept or retry."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    queue(campaign_test)
    pulses = []

    def submit(value, settings, mail, *, seconds, check):
        """Replace only the external helper; real journal and pulse checks remain."""
        check()
        pulses.append("admitted")
        if removed:
            campaign_test[3].unlink()
        else:
            write_private(campaign_test[3], b"rotated-synthetic-workspace")
        check()
        pytest.fail("Credential change failed to stop the helper pulse")

    monkeypatch.setattr(tasks, "submit_sample", submit)
    row = deliver(campaign_test)
    assert pulses == ["admitted"]
    assert row.state == "delivery_unknown" and row.mail == {}
    assert TaskRun.objects.get(pk=row.task_id).state == "failed"
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 0


def test_web_cannot_forge_provider_result_or_retarget_queued_mail(campaign_test):
    """SQL column and transition owners defend against direct ORM bypasses."""
    row, _ = queue(campaign_test)
    with web_login():
        for mutation in (
            {"state": "accepted"},
            {"mail": {"recipient": "other@example.org"}},
        ):
            from django.db import DatabaseError

            with pytest.raises((DatabaseError, IntegrityError)), work_transaction():
                CampaignMailTest.objects.filter(pk=row.pk).update(
                    **mutation, version=F("version") + 1
                )
    row.refresh_from_db()
    assert row.state == "queued" and row.mail["recipient"] == "test@example.org"


@pytest.mark.parametrize("mutation", ["signature", "recipient", "duplicate", "expired"])
def test_closed_send_rejects_unreviewed_or_expired_commands(
    campaign_test, monkeypatch, mutation
):
    """Changed/expired signatures and extra client fields never create work."""
    _, browser, path, _ = campaign_test
    with web_login():
        assert Client().get(path).status_code == 403
        page = browser.get(path)
        values = {"preview_token": page.context["form"]["preview_token"].value()}
        if mutation == "signature":
            values["preview_token"] += "changed"
        elif mutation == "recipient":
            values["recipient"] = "unreviewed@example.org"
        elif mutation == "duplicate":
            values["preview_token"] = [values["preview_token"], values["preview_token"]]
        else:
            from django.core import signing

            now = signing.time.time()
            monkeypatch.setattr(signing.time, "time", lambda: now + 901)
        assert post(browser, path, values).status_code == 400
    assert not CampaignMailTest.objects.exists()


def test_campaign_mail_history_prevents_delete(campaign_test):
    """A retained test cannot lose its no-replay owner through ordinary deletion."""
    row, _ = queue(campaign_test)
    with pytest.raises(IntegrityError, match="retained"), work_transaction():
        CampaignMailTest.objects.filter(pk=row.pk).delete()


def test_expired_retry_preserves_journal_without_resending(campaign_test, monkeypatch):
    """Short-lived consent expires independently of the retained passive receipt."""
    from django.core import signing

    row, token = queue(campaign_test)
    _, browser, path, _ = campaign_test
    now = signing.time.time()
    with monkeypatch.context() as patch:
        patch.setattr(signing.time, "time", lambda: now + 901)
        with web_login():
            assert post(browser, path, {"preview_token": token}).status_code == 400
    assert CampaignMailTest.objects.count() == 1
    row.refresh_from_db()
    assert row.state == "queued"
    with web_login():
        assert browser.get(path).context["pending"]


def test_campaign_send_drains_after_graceful_stop(campaign_test, monkeypatch):
    """The ordinary campaign sample shares setup's in-flight ownership observer."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    row, _ = queue(campaign_test)
    service, _, _, credential = campaign_test
    handler = campaign_mail_handler(service.store, credential_path=credential)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            row.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={"campaign_mail_test": handler},
        )
        assert execution is not None

        def provider(value, settings, mail, *, seconds, check):
            """Shutdown begins only after the durable submission checkpoint."""
            assert CampaignMailTest.objects.get(pk=row.pk).state == "submitting"
            execution.control.stop.set()
            check()
            return DeliveryOutcome.ACCEPTED

        monkeypatch.setattr(tasks, "submit_sample", provider)
        with maintain_execution(execution):
            handler.execute(execution)
    row.refresh_from_db()
    assert row.state == "accepted"
    assert TaskRun.objects.get(pk=row.task_id).state == "succeeded"


def test_campaign_stop_before_submission_never_starts_provider(
    campaign_test, monkeypatch
):
    """Drain permission for an existing helper cannot admit a new provider effect."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    row, _ = queue(campaign_test)
    service, _, _, credential = campaign_test
    handler = campaign_mail_handler(service.store, credential_path=credential)
    original = tasks.read_private
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            row.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={"campaign_mail_test": handler},
        )
        assert execution is not None

        def stop_after_read(*args, **kwargs):
            """Shutdown wins after initial admission but before the durable marker."""
            value = original(*args, **kwargs)
            execution.control.stop.set()
            return value

        def forbidden(*args, **kwargs):
            """No provider unit may begin after stop-sensitive admission fails."""
            pytest.fail("Unexpected provider call during shutdown")

        monkeypatch.setattr(tasks, "read_private", stop_after_read)
        monkeypatch.setattr(tasks, "submit_sample", forbidden)
        with maintain_execution(execution):
            handler.execute(execution)
    row.refresh_from_db()
    assert row.state == "queued" and row.submitted_at is None
    assert TaskRun.objects.get(pk=row.task_id).state == "failed"
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    row.refresh_from_db()
    assert row.state == "cancelled" and row.mail == {}


def test_sql_rejects_recipient_substitution_and_rolls_back_task(
    campaign_test, monkeypatch
):
    """An ORM-bypassed recipient cannot leave either a delivery or an orphan Task."""
    from parishkit.stewardship.readiness_mail import ReadinessMail

    original = ReadinessMail.payload
    monkeypatch.setattr(
        ReadinessMail,
        "payload",
        lambda self: (
            original(self) | {"recipient": "not-the-test-recipient@example.org"}
        ),
    )
    _, browser, path, _ = campaign_test
    with web_login():
        page = browser.get(path)
        assert page.status_code == 200
        values = {"preview_token": page.context["form"]["preview_token"].value()}
        assert post(browser, path, values).status_code == 503
    assert not CampaignMailTest.objects.exists()
    assert not TaskRun.objects.filter(task_type="campaign_mail_test").exists()


def test_changed_configuration_invalidates_unsigned_effect_and_cancels_pending(
    campaign_test,
):
    """A later sender/parish/config revision cannot inherit an earlier send intent."""
    service, browser, path, _ = campaign_test
    row, token = queue(campaign_test)
    parish_id = service.store.active().document()["sections"]["parish"][0]["id"]
    assert (
        campaign_builders.change(
            service.store,
            service.store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": parish_id,
                    "values": {"name": "Changed Parish"},
                }
            ],
        ).state
        == "applied"
    )
    with web_login():
        # Idempotent replay acknowledges the original queued command; it does
        # not adopt the new configuration or create another provider effect.
        assert post(browser, path, {"preview_token": token}).status_code == 302
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    row.refresh_from_db()
    assert row.state == "cancelled" and row.mail == {}


def test_unknown_outcome_needs_explicit_new_send_acknowledgement(
    campaign_test, monkeypatch
):
    """A new signed key alone does not consent to a possibly duplicate delivery."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    queue(campaign_test)
    monkeypatch.setattr(
        tasks, "submit_sample", lambda *args, **kwargs: DeliveryOutcome.UNKNOWN
    )
    deliver(campaign_test)
    _, browser, path, _ = campaign_test
    with web_login():
        page = browser.get(path)
        assert page.context["unknown"]
        values = {"preview_token": page.context["form"]["preview_token"].value()}
        assert post(browser, path, values).status_code == 400
        assert CampaignMailTest.objects.count() == 1
        assert (
            post(browser, path, values | {"acknowledge_unknown": "on"}).status_code
            == 302
        )
    assert CampaignMailTest.objects.count() == 2


def test_revoked_requesting_admin_prevents_new_effects(campaign_test):
    """A queued test cannot outlive removal of its originating Admin authority."""
    row, _ = queue(campaign_test)
    PortalUser.objects.filter(pk=row.requested_by_id).update(
        disabled=True, version=F("version") + 1
    )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    row.refresh_from_db()
    assert row.state == "cancelled" and row.submitted_at is None


def test_failed_helper_drain_keeps_checkpoint_until_real_lease_expiry(
    campaign_test, monkeypatch
):
    """A fatal private helper never makes uncertain work safe to retry or complete."""
    from threading import Event

    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks
    from parishkit.stewardship.accounts.sessions import database_now
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure

    row, _ = queue(campaign_test)
    calls = []

    def failed(*args, **kwargs):
        """No real child exists; exercise the production fatal-drain exception path."""
        calls.append(True)
        raise ProviderCheckDrainFailure("synthetic failed helper drainage")

    monkeypatch.setattr(tasks, "submit_sample", failed)
    with pytest.raises(ProviderCheckDrainFailure):
        deliver(campaign_test)
    row.refresh_from_db()
    assert row.state == "submitting" and row.finished_at is None
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 0
    task = TaskRun.objects.get(pk=row.task_id)
    with work_transaction():
        remaining = (
            max(task.lease_expires_at, row.deadline_at) - database_now()
        ).total_seconds()
    assert 0 < remaining <= 60
    Event().wait(remaining + 0.05)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    service, _, _, path = campaign_test
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert recover_hint(
            row.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={
                "campaign_mail_test": campaign_mail_handler(
                    service.store, credential_path=path
                )
            },
        )
    row.refresh_from_db()
    assert row.state == "delivery_unknown" and row.mail == {}
    assert TaskRun.objects.get(pk=row.task_id).state == "failed"
    assert calls == [True]


def test_go_live_cleanup_gate_blocks_test_preview(campaign_test):
    """Credential cleanup cannot race a newly queued readiness test."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    from .credential_builders import keys, populate

    _, browser, path, _ = campaign_test
    campaign = Campaign.objects.get()
    populate(campaign, keys(), [])
    invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda _: True)
    assert CampaignCredentialState.objects.get().go_live_gate
    with web_login():
        assert browser.get(path).status_code == 409
    assert not CampaignMailTest.objects.exists()
