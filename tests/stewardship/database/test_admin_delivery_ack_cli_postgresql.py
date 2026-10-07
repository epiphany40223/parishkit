"""``delivery resend`` and ``delivery refusal-clear`` (ADM-11 PR 9c).

The two Outgoing mail actions whose pages ask for a ticked acknowledgement:
the command line asks for it at its prompt (or takes ``--yes``) before any
transaction opens, then runs the page's own function in the page's command
scope. Process admission is replaced by the test's own Django assembly (the
runner of test_admin_task_retry_cli_postgresql.py); the rest is the real
command line on the restricted web login. A resend prepares the email once
per request key, never twice, and never changes a Family's code or link.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.recipient_models import RecipientRefusalResolution
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .automation_builders import paired
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_admin_delivery_cli_postgresql import NOTE, credentials, private, resolve
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_recipient_suppressions_postgresql import refused, remember

pytestmark = pytest.mark.django_db(transaction=True)

RESEND = "admin_cmd_delivery_resend"
CLEAR = "admin_cmd_delivery_refusal_clear"


def resend(admin, secret, message, key, *, version, answer=b"", yes=False):
    """``delivery resend``, answering the prompt with ``answer``."""
    argv = [
        "delivery",
        "resend",
        str(message.pk),
        "--expected-version",
        str(version),
        "--note",
        NOTE,
        "--request-key",
        str(key),
    ]
    return admin(*argv, *(["--yes"] if yes else []), secret=secret, answer=answer)


def test_resend_asks_then_sends_once_per_key(
    auth_service,
    monkeypatch,
    family_mail,  # noqa: F811
    google,
):
    """The page's Resend: acknowledged, keyed, prepared once, never twice.

    No answer changes nothing and loads no key. The typed ``yes`` resends:
    one key load, a fresh render, one retry task. The same key again (with
    ``--yes``) returns the receipt without loading keys or preparing; the
    page's form with that key is the same resolution; another key for the
    version already resent is stale. Credentials never change.
    """
    loads = []

    def family_keys():
        """The harness's own rings, as load_family_keys returns them."""
        loads.append(True)
        return family_mail.rings.general, family_mail.rings.public

    admin = runner(auth_service, monkeypatch, family_keys)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        assert message.state == "delivery_unknown"
        before = credentials()
        browser, secret, row = paired(admin.service)
        code, shown, _ = admin("delivery", "show", str(message.pk), secret=secret)
        assert code == 0 and "resend" in shown["result"]["actions"], shown
        version, render_id = message.version, message.render_id
        runs = TaskRun.objects.filter(root_id=message.task_id).count()
        key = uuid4()

        def untouched():
            """Nothing resent: the delivery, its runs and the events as before."""
            message.refresh_from_db()
            assert (message.version, message.render_id) == (version, render_id)
            assert TaskRun.objects.filter(root_id=message.task_id).count() == runs
            assert not DeliveryResolution.objects.exists()
            assert not AuditEvent.objects.filter(
                event_type__startswith="admin_cmd_delivery_"
            ).exists()
            assert loads == [] and credentials() == before

        # No answer, or another answer: exit 4, nothing changed.
        for answer in (b"", b"no\n", b"YES\n"):
            code, refused_doc, err = resend(
                admin, secret, message, key, version=version, answer=answer
            )
            assert code == 4, refused_doc
            assert refused_doc["error"]["code"] == "confirmation_required"
            assert "duplicate risk" in err
            untouched()
        # A read-only session is refused before anything changes.
        _, reader, _ = paired(admin.service, scope="read-only")
        code, denied, _ = resend(admin, reader, message, key, version=version, yes=True)
        assert code == 1 and denied["error"]["code"] == "denied", denied
        untouched()
        # resend is not a delivery resolve action.
        code, usage, _ = resolve(admin, secret, message, "resend", uuid4())
        assert code == 2 and usage["error"]["code"] == "usage"
        # The typed acknowledgement resends.
        code, document, _ = resend(
            admin, secret, message, key, version=version, answer=b"yes\n"
        )
        assert code == 0, document
        result = document["result"]
        assert result["created"] is True and result["request_key"] == str(key)
        assert result["resolution"]["action"] == "resend"
        assert result["resolution"]["retry_task_id"] and not private(document)
        assert loads == [True]
        message.refresh_from_db()
        assert message.state == "pending" and message.render_id != render_id
        pending = (message.version, message.render_id)
        receipt = DeliveryResolution.objects.get(pk=key)
        assert receipt.duplicate_acknowledged and receipt.actor_id == row.principal_id
        [event] = AuditEvent.objects.filter(event_type=RESEND)
        assert event.subject_id == row.pk and event.actor_id == row.principal_id

        def unchanged():
            """One resolution, retry task, delivery and event; same credentials."""
            message.refresh_from_db()
            assert (message.version, message.render_id) == pending
            assert DeliveryResolution.objects.count() == 1
            assert TaskRun.objects.filter(root_id=message.task_id).count() == 2
            assert OutboxMessage.objects.count() == 1
            assert AuditEvent.objects.filter(event_type=RESEND).count() == 1
            assert not AuditEvent.objects.filter(
                event_type="admin_cmd_delivery_resolve"
            ).exists()
            assert credentials() == before

        unchanged()
        # The double invoke: the receipt again, no key load, no preparation.
        code, again, _ = resend(admin, secret, message, key, version=version, yes=True)
        assert code == 0 and again["result"]["created"] is False, again
        assert again["result"]["resolution"] == result["resolution"]
        assert loads == [True]
        unchanged()
        # The page's form with the same key, box ticked: the same resolution.
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                f"/admin/mail/outgoing/{message.pk}/resolution/",
                {
                    "command_id": str(key),
                    "expected_version": str(version),
                    "action": "resend",
                    "note": NOTE,
                    "duplicate_acknowledged": "yes",
                },
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 302
        unchanged()
        # Another key for the version already resent: stale, no second send.
        code, stale, _ = resend(
            admin, secret, message, uuid4(), version=version, yes=True
        )
        assert code == 1 and stale["error"]["code"] == "stale_version", stale
        unchanged()


def test_resend_note_from_standard_input_needs_yes(
    auth_service,
    monkeypatch,
    family_mail,  # noqa: F811
    google,
):
    """Standard input cannot carry both the note and the answer.

    Without ``--yes`` it is a usage error; with it, the note is read from
    standard input and the resend goes ahead.
    """
    admin = runner(
        auth_service,
        monkeypatch,
        lambda: (family_mail.rings.general, family_mail.rings.public),
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        _, secret, _ = paired(admin.service)
        code, document, _ = admin(
            "delivery",
            "resend",
            str(message.pk),
            "--expected-version",
            str(message.version),
            "--note",
            "-",
            secret=secret,
            answer=NOTE.encode(),
        )
        assert code == 2 and document["error"]["code"] == "usage"
        assert not DeliveryResolution.objects.exists()
        key = uuid4()
        code, document, _ = admin(
            "delivery",
            "resend",
            str(message.pk),
            "--expected-version",
            str(message.version),
            "--note",
            "-",
            "--request-key",
            str(key),
            "--yes",
            secret=secret,
            answer=NOTE.encode(),
        )
        assert code == 0 and document["result"]["created"] is True, document
        receipt = DeliveryResolution.objects.get(pk=key)
        assert receipt.evidence_note == NOTE and receipt.duplicate_acknowledged
        assert AuditEvent.objects.filter(event_type=RESEND).count() == 1


def test_a_page_resend_key_repeated_from_the_command_line(
    auth_service,
    monkeypatch,
    settings,
    family_mail,  # noqa: F811
    google,
):
    """A resend the page made is returned, not repeated, for the page's key."""
    from parishkit.stewardship.accounts import family_authentication

    # The page prepares with the web's own Family keys, as in
    # test_delivery_views_postgresql.py.
    settings.STEWARDSHIP_PUBLIC_ORIGIN = "http://localhost:8000"
    monkeypatch.setattr(family_authentication, "runtime", lambda: family_mail.rings)
    loads = []
    admin = runner(auth_service, monkeypatch, lambda: loads.append(True))
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        before = credentials()
        browser, secret, _ = paired(admin.service)
        version, key = message.version, uuid4()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response = browser.post(
                f"/admin/mail/outgoing/{message.pk}/resolution/",
                {
                    "command_id": str(key),
                    "expected_version": str(version),
                    "action": "resend",
                    "note": NOTE,
                    "duplicate_acknowledged": "yes",
                },
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
        assert response.status_code == 302
        message.refresh_from_db()
        resent = (message.version, message.render_id)
        runs = TaskRun.objects.filter(root_id=message.task_id).count()
        code, document, _ = resend(
            admin, secret, message, key, version=version, yes=True
        )
        assert code == 0 and document["result"]["created"] is False, document
        assert document["result"]["resolution"]["id"] == str(key)
        # No key load, no preparation, no command event: the page's receipt.
        message.refresh_from_db()
        assert (message.version, message.render_id) == resent
        assert TaskRun.objects.filter(root_id=message.task_id).count() == runs
        assert DeliveryResolution.objects.count() == 1
        assert not AuditEvent.objects.filter(event_type=RESEND).exists()
        assert loads == [] and credentials() == before


@pytest.fixture
def refusal(response_service):
    """One genuine refusal, and the current source to verify against."""
    harness = activate_response_service(response_service)
    return remember(refused(harness)), SourceCurrent.objects.get()


def clear(admin, secret, refusal_id, key, current, *, answer=b"", yes=False, **over):
    """``delivery refusal-clear`` with the page's source version."""
    values = {
        "--source-snapshot-id": str(current.snapshot_id),
        "--source-generation": str(current.generation),
        "--note": NOTE,
        "--request-key": str(key),
    } | over
    argv = ["delivery", "refusal-clear", str(refusal_id)]
    for name, value in values.items():
        argv += [name, value]
    return admin(*argv, *(["--yes"] if yes else []), secret=secret, answer=answer)


def test_refusal_clear_asks_then_clears_once_per_key(
    auth_service, monkeypatch, refusal, google
):
    """The page's verified clearance: acknowledged, keyed, audited once."""
    admin = runner(auth_service, monkeypatch)
    record, current = refusal
    browser, secret, row = paired(admin.service)
    key = uuid4()
    # No answer, or another answer: exit 4, nothing cleared or recorded.
    for answer in (b"", b"no\n"):
        code, document, err = clear(
            admin, secret, record.pk, key, current, answer=answer
        )
        assert code == 4 and document["error"]["code"] == "confirmation_required"
        assert "I verified this address" in err
        assert not RecipientRefusalResolution.objects.exists()
        assert not AuditEvent.objects.filter(
            event_type__in=(CLEAR, "recipient_refusal_cleared")
        ).exists()
    # A source version that is not current is stale.
    code, stale, _ = clear(
        admin,
        secret,
        record.pk,
        uuid4(),
        current,
        yes=True,
        **{"--source-generation": str(current.generation + 1)},
    )
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    code, missing, _ = clear(admin, secret, uuid4(), uuid4(), current, yes=True)
    assert code == 1 and missing["error"]["code"] == "not_available", missing
    assert not RecipientRefusalResolution.objects.exists()
    assert not AuditEvent.objects.filter(event_type=CLEAR).exists()
    code, document, _ = clear(admin, secret, record.pk, key, current, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["created"] is True and result["request_key"] == str(key)
    assert result["resolution"]["refusal_id"] == str(record.pk)
    assert result["resolution"]["source_generation"] == current.generation
    assert not private(document)
    receipt = RecipientRefusalResolution.objects.get(pk=key)
    assert receipt.reason == "verified_admin" and receipt.evidence_note == NOTE
    assert receipt.actor_id == row.principal_id
    [event] = AuditEvent.objects.filter(event_type=CLEAR)
    assert event.subject_id == row.pk

    def once():
        """One clearance, one command event, one domain event."""
        assert RecipientRefusalResolution.objects.count() == 1
        assert AuditEvent.objects.filter(event_type=CLEAR).count() == 1
        assert (
            AuditEvent.objects.filter(event_type="recipient_refusal_cleared").count()
            == 1
        )

    once()
    code, again, _ = clear(admin, secret, record.pk, key, current, yes=True)
    assert code == 0 and again["result"]["created"] is False, again
    once()
    # The page's form with the same key, box ticked.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.post(
            f"/admin/mail/refusals/{record.pk}/clearance/",
            {
                "command_id": str(key),
                "source_snapshot_id": str(current.snapshot_id),
                "source_generation": str(current.generation),
                "note": NOTE,
                "verified": "yes",
            },
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )
    assert response.status_code == 302
    once()
    # Another key for the cleared refusal is stale; the key for another
    # intent is invalid.
    code, stale, _ = clear(admin, secret, record.pk, uuid4(), current, yes=True)
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    code, bound, _ = clear(
        admin, secret, record.pk, key, current, yes=True, **{"--note": "other"}
    )
    assert code == 1 and bound["error"]["code"] == "invalid", bound
    once()
    # A read-only session is refused before anything changes.
    _, reader, _ = paired(admin.service, scope="read-only")
    code, denied, _ = clear(admin, reader, record.pk, uuid4(), current, yes=True)
    assert code == 1 and denied["error"]["code"] == "denied", denied
    once()
