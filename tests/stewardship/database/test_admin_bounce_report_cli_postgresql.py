"""``delivery bounce-report``: the Mail message page's bounce report (#1211).

The command asks for the page's tick ("I saw a bounce message for this
address from this email") at its prompt, or takes ``--yes``, before any
transaction opens, warning as the page does when the address is the
Family's only one left. It then calls the page's own
``report_recipient_bounce`` in the page's command scope on the restricted
web login, so the definer and the refusal guard of #806 slice 4 decide as
for the page. Process admission is replaced by the test's own Django
assembly (the runner of test_admin_task_retry_cli_postgresql.py).
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

from ..policy_factory import address
from .automation_builders import paired
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_admin_delivery_cli_postgresql import private
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_bounce_report_postgresql import ADDRESS, NOTE, credentials, delivered
from .test_policy_postgresql import user
from .test_recipient_suppressions_postgresql import email

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_delivery_bounce_report"
DOMAIN = "recipient_bounce_reported"


def bounce(admin, secret, message_id, key, *, answer=b"", yes=False, **over):
    """``delivery bounce-report`` with the page's fields."""
    values = {"--address": ADDRESS, "--note": NOTE, "--request-key": str(key)} | over
    argv = ["delivery", "bounce-report", str(message_id)]
    for name, value in values.items():
        argv += [name, value]
    return admin(*argv, *(["--yes"] if yes else []), secret=secret, answer=answer)


@pytest.fixture
def scenario(response_service):
    """A delivered Production email to Family 1's only eligible address.

    The Administrator is the paired session's own (``paired`` signs one in).
    """
    harness = activate_response_service(response_service)
    return harness, delivered(harness)


def events(kind):
    """How many audit events of ``kind`` are recorded."""
    return AuditEvent.objects.filter(event_type=kind).count()


def test_bounce_report_asks_then_reports_once_per_key(
    auth_service,
    monkeypatch,
    scenario,
    google,
):
    """The page's report: acknowledged, keyed, audited once, never the note.

    No answer reports nothing; the prompt warns, without the address, that
    it is the Family's only one left, and does not for an address that is
    not. The typed ``yes`` sets the address aside
    (given in another case, as copied from a bounce message); the same key
    again returns it, the page's form with that key is the same report,
    another intent is ``invalid`` and another key for the same address is
    ``stale_version``. A read-only session is refused; the refusal reads say
    it is a reported bounce. The Family's credentials never change.
    """
    harness, status = scenario
    admin = runner(auth_service, monkeypatch)
    browser, secret, row = paired(admin.service)
    message = OutboxMessage.objects.get(pk=status.message_id)
    before = credentials(message.family_id)
    key = uuid4()
    # No answer, or another answer: exit 4, nothing reported or recorded.
    for answer in (b"", b"no\n"):
        code, document, err = bounce(admin, secret, message.pk, key, answer=answer)
        assert code == 4 and document["error"]["code"] == "confirmation_required"
        assert "I saw a bounce message" in err
        assert "only address left" in err
        assert ADDRESS not in err and NOTE not in err
        assert not RecipientRefusal.objects.exists()
        assert events(EVENT) == events(DOMAIN) == 0
    # An email to an address that is not the Family's current one: reporting
    # it leaves the current address, so no warning.
    old = delivered(harness, address="old@example.org")
    code, _, err = bounce(
        admin, secret, old.message_id, key, **{"--address": "old@example.org"}
    )
    assert code == 4 and "I saw a bounce message" in err
    assert "only address left" not in err
    # A read-only session is refused before anything changes.
    _, reader, _ = paired(admin.service, scope="read-only")
    code, denied, _ = bounce(admin, reader, message.pk, uuid4(), yes=True)
    assert code == 1 and denied["error"]["code"] == "denied", denied
    # An unknown email is not available; one not delivered, or an address
    # the email was not sent to, is stale (the page's 409).
    code, missing, _ = bounce(admin, secret, uuid4(), uuid4(), yes=True)
    assert code == 1 and missing["error"]["code"] == "not_available", missing
    pending = email(harness)
    code, stale, _ = bounce(admin, secret, pending.message_id, uuid4(), yes=True)
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    code, stale, _ = bounce(
        admin, secret, message.pk, uuid4(), yes=True, **{"--address": "x@example.org"}
    )
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert not RecipientRefusal.objects.exists() and events(EVENT) == 0
    # The typed acknowledgement reports; the address may be copied as the
    # bounce message spells it.
    code, document, _ = bounce(
        admin,
        secret,
        message.pk,
        key,
        answer=b"yes\n",
        **{"--address": f"  {ADDRESS.upper()} "},
    )
    assert code == 0, document
    result = document["result"]
    assert result["created"] is True and result["request_key"] == str(key)
    assert result["refusal"]["id"] == str(key)
    assert result["refusal"]["message_id"] == str(message.pk)
    assert result["refusal"]["kind"] == "reported_bounce"
    assert not private(document) and NOTE not in str(document)
    refusal = RecipientRefusal.objects.get(pk=key)
    assert (refusal.kind, refusal.address) == ("reported_bounce", ADDRESS)
    assert (refusal.evidence_note, refusal.actor_id) == (NOTE, row.principal_id)
    [event] = AuditEvent.objects.filter(event_type=EVENT)
    assert event.subject_id == row.pk
    assert not FamilyCampaign.objects.get(pk=message.family_id).email_deliverable
    assert credentials(message.family_id) == before

    def once():
        """One report, one command event, one domain event."""
        assert RecipientRefusal.objects.count() == 1
        assert events(EVENT) == events(DOMAIN) == 1

    once()
    code, again, _ = bounce(admin, secret, message.pk, key, yes=True)
    assert code == 0 and again["result"]["created"] is False, again
    assert again["result"]["refusal"] == result["refusal"]
    once()
    # The page's form with the same key, box ticked: the same report.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.post(
            f"/admin/mail/outgoing/{message.pk}/bounce-report/",
            {
                "command_id": str(key),
                "address": ADDRESS,
                "note": NOTE,
                "observed": "yes",
            },
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )
    assert response.status_code == 302
    once()
    # The key with another intent is invalid; another key for the address
    # now set aside is stale.
    code, bound, _ = bounce(
        admin, secret, message.pk, key, yes=True, **{"--note": "Another note"}
    )
    assert code == 1 and bound["error"]["code"] == "invalid", bound
    code, stale, _ = bounce(admin, secret, message.pk, uuid4(), yes=True)
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    once()
    # The refusal reads say it is a reported bounce, without its note.
    code, listed, _ = admin("delivery", "refusals", secret=secret)
    assert code == 0, listed
    assert listed["result"]["refusals"][0] == {
        "id": str(key),
        "kind": "reported_bounce",
        "created_at": listed["result"]["refusals"][0]["created_at"],
    }
    code, shown, _ = admin("delivery", "refusal-show", str(key), secret=secret)
    assert code == 0 and shown["result"]["kind"] == "reported_bounce", shown
    assert not private(listed) and not private(shown)
    assert NOTE not in str(shown)
    assert credentials(message.family_id) == before


def test_a_key_another_administrator_used_is_invalid(
    auth_service, monkeypatch, scenario, google
):
    """A key another Administrator reported with, as on the page, is bound
    to them: the same address and note from this session is ``invalid`` and
    records nothing more."""
    from parishkit.stewardship.jobs.delivery_admin import report_recipient_bounce

    harness, status = scenario
    admin = runner(auth_service, monkeypatch)
    _, secret, row = paired(admin.service)
    store = admin.service.store
    other = user("other@example.org")
    change(
        store,
        store.active(),
        row.principal_id,
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("other@example.org"),
            }
        ],
    )
    key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        report_recipient_bounce(
            store,
            other.pk,
            message_id=status.message_id,
            address=ADDRESS,
            command_id=key,
            note=NOTE,
            observed=True,
        )
    code, document, _ = bounce(admin, secret, status.message_id, key, yes=True)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert RecipientRefusal.objects.get().actor_id == other.pk
    assert events(EVENT) == 0 and events(DOMAIN) == 1
