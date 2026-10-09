"""The delivery commands of ``pk-stewardship admin`` (ADM-11 PR 9b, #682).

Process admission is replaced by the test's own Django assembly (the runner
of test_admin_task_retry_cli_postgresql.py); everything after it is the
real command line on the restricted web login, reading real Outgoing mail
and refused addresses through the pages' own functions and resolving a real
uncertain delivery through the page's resolution form. A Family email's
retry re-prepares it with the Family keys the command loads only for that
(#682), exactly once per key. No document names a recipient: no address,
Family DUID or Family id, and no evidence note.
"""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .automation_builders import paired
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_test_postgresql import family_test  # noqa: F401
from .test_recipient_suppressions_postgresql import refused, remember

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_delivery_resolve"
NOTE = "private-evidence-marker from the provider console"


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Commands over the shared authentication service."""
    return runner(auth_service, monkeypatch)


def views():
    """How many delivery_viewed events have been recorded."""
    return AuditEvent.objects.filter(event_type="delivery_viewed").count()


def private(document):
    """Whether a document carries a recipient, a DUID or the evidence note."""
    text = json.dumps(document)
    return "@" in text or "family_duid" in text or "private-evidence" in text


def resolve(admin, secret, message, action, key, *, version=None):
    """``delivery resolve`` with the evidence note inline."""
    return admin(
        "delivery",
        "resolve",
        str(message.pk),
        "--action",
        action,
        "--expected-version",
        str(version or message.version),
        "--note",
        NOTE,
        "--request-key",
        str(key),
        secret=secret,
    )


def test_list_and_show_read_outgoing_mail_without_recipients(
    admin,
    family_mail,  # noqa: F811
    google,
):
    """The pages' rows and history, the page's view events, no recipient."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        _, secret, _ = paired(admin.service, scope="read-only")
        before = views()
        code, document, _ = admin(
            "delivery", "list", "--state", "delivery_unknown", secret=secret
        )
        assert code == 0, document
        [row] = document["result"]["deliveries"]
        assert row["id"] == str(message.pk) and row["version"] == message.version
        assert row["purpose"] == "initial" and row["state"] == "delivery_unknown"
        assert not private(document)
        # The page's search takes a DUID as input; it is never printed.
        code, found, _ = admin("delivery", "list", "--search", "1", secret=secret)
        assert code == 0 and [r["id"] for r in found["result"]["deliveries"]] == [
            str(message.pk)
        ]
        code, shown, _ = admin("delivery", "show", str(message.pk), secret=secret)
        assert code == 0, shown
        result = shown["result"]
        assert result["delivery"]["id"] == str(message.pk)
        assert result["task"]["state"] == "failed"
        # resend is offered by the page, not here (PR 9c adds it).
        assert result["actions"] == ["note", "accept", "confirm_unsent"]
        assert result["events"] and not private(shown)
        assert views() == before + 3
        code, missing, _ = admin("delivery", "show", str(uuid4()), secret=secret)
        assert code == 1 and missing["error"]["code"] == "not_available"
        code, bad, _ = admin("delivery", "list", "--search", "x", secret=secret)
        assert code == 1 and bad["error"]["code"] == "invalid"


def test_resolve_accepts_external_evidence_once_per_key(
    admin,
    family_mail,  # noqa: F811
    google,
):
    """The page's form: keyed, versioned, audited, shared with the page."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        browser, secret, row = paired(admin.service)
        key = uuid4()
        code, document, _ = resolve(admin, secret, message, "accept", key)
        assert code == 0, document
        result = document["result"]
        assert result["created"] is True and result["request_key"] == str(key)
        assert result["resolution"]["action"] == "accept"
        assert not private(document)
        receipt = DeliveryResolution.objects.get(pk=key)
        assert receipt.actor_id == row.principal_id and receipt.evidence_note == NOTE
        [event] = AuditEvent.objects.filter(event_type=EVENT)
        assert event.subject_id == row.pk and event.actor_id == row.principal_id
        # A repeat returns the receipt; the page's form with the key too.
        code, again, _ = resolve(admin, secret, message, "accept", key)
        assert code == 0 and again["result"]["created"] is False
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                f"/admin/mail/outgoing/{message.pk}/resolution/",
                {
                    "command_id": str(key),
                    "expected_version": str(message.version),
                    "action": "accept",
                    "note": NOTE,
                },
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 302
        assert DeliveryResolution.objects.count() == 1
        assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
        # The same key for another intent is invalid; an old version stale.
        code, bound, _ = resolve(admin, secret, message, "note", key)
        assert code == 1 and bound["error"]["code"] == "invalid"
        code, stale, _ = resolve(admin, secret, message, "note", uuid4(), version=1)
        assert code == 1 and stale["error"]["code"] == "stale_version"


def test_resolve_refusals_change_nothing(
    admin,
    family_mail,  # noqa: F811
    google,
):
    """resend, read-only scope and an ended session; no receipt."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        _, secret, row = paired(admin.service)
        # resend is not offered here (its acknowledgement waits for PR 9c).
        code, document, _ = resolve(admin, secret, message, "resend", uuid4())
        assert code == 2 and document["error"]["code"] == "usage"
        _, reader, _ = paired(admin.service, scope="read-only")
        code, document, _ = resolve(admin, reader, message, "note", uuid4())
        assert code == 1 and document["error"]["code"] == "denied"
        actor = current_principal(admin.service.store, row.principal_id)
        automation.revoke(row.pk, actor, reason="revoked_by_owner")
        code, document, _ = resolve(admin, secret, message, "note", uuid4())
        assert code == 5 and document["error"]["code"] == "session_ended"
        assert not DeliveryResolution.objects.exists()
        assert not AuditEvent.objects.filter(event_type=EVENT).exists()


def test_refusals_are_listed_and_shown_without_the_address(
    admin,
    response_service,
    google,
):
    """Refused addresses by id and time, and the source version to verify."""
    harness = activate_response_service(response_service)
    refusal = remember(refused(harness))
    _, secret, _ = paired(admin.service, scope="read-only")
    before = views()
    code, document, _ = admin("delivery", "refusals", "--duid", "1", secret=secret)
    assert code == 0, document
    assert document["result"]["refusals"] == [
        {"id": str(refusal.pk), "created_at": refusal.created_at.isoformat()}
    ]
    assert not private(document)
    code, shown, _ = admin("delivery", "refusal-show", str(refusal.pk), secret=secret)
    assert code == 0, shown
    current = SourceCurrent.objects.get()
    assert shown["result"]["source"] == {
        "snapshot_id": str(current.snapshot_id),
        "generation": current.generation,
    }
    assert shown["result"]["resolved"] is None and not private(shown)
    assert views() == before + 2
    code, missing, _ = admin("delivery", "refusal-show", str(uuid4()), secret=secret)
    assert code == 1 and missing["error"]["code"] == "not_available"


def credentials():
    """The Family's code and link rows a retry must never change (hard rule)."""
    from parishkit.stewardship.campaigns.credential_models import (
        FamilyAccessToken,
        FamilyCampaign,
    )

    return (
        list(FamilyCampaign.objects.values_list("pk", "code_ciphertext")),
        list(
            FamilyAccessToken.objects.order_by("pk").values_list(
                "pk", "generation_id", "destroyed_at"
            )
        ),
    )


@pytest.mark.parametrize(
    "action,status,production",
    [("retry_failed", "PERMANENT", True), ("retry_unsent", None, False)],
)
def test_family_retry_prepares_once_per_key_like_the_page(
    auth_service,
    monkeypatch,
    family_mail,  # noqa: F811
    google,
    action,
    status,
    production,
):
    """A Family email retry: the page's effects, once, credentials unchanged.

    The command loads the web's general and public token keyrings only for
    this; a repeat of the key returns the receipt without loading them or
    preparing again, the page's form with that key does too, and a second
    key on the same version is stale. One delivery, one retry task.
    """
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus

    loads = []

    def family_keys():
        """The harness's own rings, as load_keyrings returns them."""
        loads.append(True)
        return family_mail.rings.general, family_mail.rings.public

    admin = runner(auth_service, monkeypatch, family_keys)
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(
            family_mail, status and getattr(FamilyDeliveryStatus, status)
        )
        before = credentials()
        render_id, attempt = message.render_id, message.attempt
        browser, secret, row = paired(admin.service)
        code, shown, _ = admin("delivery", "show", str(message.pk), secret=secret)
        assert code == 0 and action in shown["result"]["actions"], shown
        key = uuid4()
        code, document, _ = resolve(admin, secret, message, action, key)
        assert code == 0, document
        result = document["result"]
        assert result["created"] is True and result["resolution"]["action"] == action
        assert result["resolution"]["retry_task_id"] and not private(document)
        assert len(loads) == 1
        retried = message.version
        message.refresh_from_db()
        assert message.state == "pending" and message.render_id != render_id
        assert message.sealed_substitutions and message.attempt == attempt
        task = TaskRun.objects.get(pk=result["resolution"]["retry_task_id"])
        assert task.parent_id == message.task_id and task.state == "queued"

        def unchanged():
            """One resolution, retry task, delivery and event; same credentials."""
            assert DeliveryResolution.objects.count() == 1
            assert TaskRun.objects.filter(root_id=message.task_id).count() == 2
            assert OutboxMessage.objects.count() == 1
            assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
            assert credentials() == before

        unchanged()
        # The double invoke: the same key returns the receipt, loads no key
        # and prepares nothing; the delivery keeps the first retry's render.
        code, again, _ = resolve(admin, secret, message, action, key, version=retried)
        assert code == 0 and again["result"]["created"] is False, again
        assert again["result"]["resolution"] == result["resolution"]
        assert len(loads) == 1
        pending = (message.version, message.render_id)
        message.refresh_from_db()
        assert (message.version, message.render_id) == pending
        unchanged()
        # The page's form with the same key is the same resolution.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                f"/admin/mail/outgoing/{message.pk}/resolution/",
                {
                    "command_id": str(key),
                    "expected_version": str(retried),
                    "action": action,
                    "note": NOTE,
                },
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 302
        message.refresh_from_db()
        assert (message.version, message.render_id) == pending
        unchanged()
        # Another key for the version already retried is stale: no second send.
        code, stale, _ = resolve(
            admin, secret, message, action, uuid4(), version=retried
        )
        assert code == 1 and stale["error"]["code"] == "stale_version", stale
        # A new key loads the keys; the version check then refuses before
        # any sealing.
        assert len(loads) == 2
        unchanged()


def test_a_page_retry_leaves_the_command_line_stale(
    auth_service,
    monkeypatch,
    family_mail,  # noqa: F811
    google,
):
    """The page retries first with its own key; the command's key is stale."""
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus

    loads = []

    def family_keys():
        """The harness's own rings, as load_keyrings returns them."""
        loads.append(True)
        return family_mail.rings.general, family_mail.rings.public

    admin = runner(auth_service, monkeypatch, family_keys)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, FamilyDeliveryStatus.PERMANENT)
        before = credentials()
        browser, secret, _ = paired(admin.service)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                f"/admin/mail/outgoing/{message.pk}/resolution/",
                {
                    "command_id": str(uuid4()),
                    "expected_version": str(message.version),
                    "action": "retry_failed",
                    "note": NOTE,
                },
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 302
        retried = (message.version, message.render_id)
        message.refresh_from_db()
        assert message.state == "pending" and message.render_id != retried[1]
        pending = (message.version, message.render_id)
        code, stale, _ = resolve(
            admin, secret, message, "retry_failed", uuid4(), version=retried[0]
        )
        assert code == 1 and stale["error"]["code"] == "stale_version", stale
        message.refresh_from_db()
        assert (message.version, message.render_id) == pending
        assert DeliveryResolution.objects.count() == 1
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 2
        assert not AuditEvent.objects.filter(event_type=EVENT).exists()
        assert credentials() == before
        # The key load happened (a Family retry), the sealing did not.
        assert len(loads) == 1


def test_a_family_test_retry_loads_no_key(
    family_test,  # noqa: F811
    auth_service,
    monkeypatch,
):
    """A chosen-Family test is never retried, and loads no Family key.

    ``family_test`` comes first: it shapes the configuration the shared
    authentication service is built from.
    """
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus
    from parishkit.stewardship.jobs.storage import _status

    from .test_family_mail_test_postgresql import (
        deliver,
        prepare_tests,
        request_tickets,
    )
    from .test_taskrun_postgresql import act

    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    (message,) = prepare_tests(harness)
    deliver(harness, message, FamilyDeliveryStatus.PERMANENT)
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    assert message.purpose == "family_test"
    # The command's stand-ins replace the deployment the page above needs.
    loads = []
    admin = runner(auth_service, monkeypatch, lambda: loads.append(True))
    _, secret, _ = paired(admin.service)
    code, document, _ = resolve(admin, secret, message, "retry_failed", uuid4())
    assert code == 1, document
    assert document["error"]["code"] == "denied", document
    assert loads == []
    assert not DeliveryResolution.objects.exists()
    assert TaskRun.objects.filter(root_id=message.task_id).count() == 1


def test_family_retry_refuses_keys_the_web_did_not_load(
    auth_service,
    monkeypatch,
    family_mail,  # noqa: F811
    google,
):
    """A keyring that differs from the web's is exit 2; nothing changes."""
    from parishkit.stewardship import admin_cli
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus

    def family_keys():
        """A rotation in progress: the mounted ring is not the web's."""
        raise admin_cli.CredentialMismatch("The web Family keyrings differ.")

    admin = runner(auth_service, monkeypatch, family_keys)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, FamilyDeliveryStatus.PERMANENT)
        _, secret, _ = paired(admin.service)
        code, document, _ = resolve(admin, secret, message, "retry_failed", uuid4())
        assert code == 2 and document["error"]["code"] == "credential_mismatch"
        # A process with no Family keys at all refuses the same way, as 1.
        bare = runner(auth_service, monkeypatch)
        code, document, _ = resolve(bare, secret, message, "retry_failed", uuid4())
        assert code == 1 and document["error"]["code"] == "not_available"
        # A key rotation holding the credential key lock: exit 3, retry.
        from parishkit.stewardship.accounts.cryptography import CryptographicError

        def busy(*rings, exclusive=False):
            """key_set_lock refusing as a rotation in progress does."""
            raise CryptographicError("Credential key rotation is busy; retry.")

        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_credentials.key_set_lock", busy
        )
        rotating = runner(
            auth_service,
            monkeypatch,
            lambda: (family_mail.rings.general, family_mail.rings.public),
        )
        code, document, _ = resolve(rotating, secret, message, "retry_failed", uuid4())
        assert code == 3 and document["error"]["code"] == "unavailable", document
        version = message.version
        message.refresh_from_db()
        assert message.version == version and message.state == "permanent_failure"
        assert not DeliveryResolution.objects.exists()
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 1
        assert not AuditEvent.objects.filter(event_type=EVENT).exists()
