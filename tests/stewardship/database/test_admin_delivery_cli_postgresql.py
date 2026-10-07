"""The delivery commands of ``pk-stewardship admin`` (ADM-11 PR 9b).

Process admission is replaced by the test's own Django assembly (the runner
of test_admin_task_retry_cli_postgresql.py); everything after it is the
real command line on the restricted web login, reading real Outgoing mail
and refused addresses through the pages' own functions and resolving a real
uncertain delivery through the page's resolution form. No document names a
recipient: no address, Family DUID or Family id, and no evidence note.
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
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .automation_builders import paired
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
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
                f"/admin/deliveries/{message.pk}/resolve",
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
    """Family retries, read-only scope and an ended session; no receipt."""
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, FamilyDeliveryStatus.PERMANENT)
        _, secret, row = paired(admin.service)
        # Retrying a Family email needs the web's Family keys.
        code, document, _ = resolve(admin, secret, message, "retry_failed", uuid4())
        assert code == 1 and document["error"]["code"] == "not_available", document
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
