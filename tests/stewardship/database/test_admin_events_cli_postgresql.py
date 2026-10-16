"""``pk-stewardship admin events list`` and ``events acknowledge`` (ADM-11 PR 11).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. Both
commands are Home's security event panel and critical-problem banner
through the pages' own reads and the views' ``acknowledge_now``, so the
acknowledgement rows and the pages' audit events are the pages'.
"""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import admin_editing, sessions
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.policy_models import (
    PolicySecurityAcknowledgement,
    PolicySecurityEvent,
)
from parishkit.stewardship.accounts.security_events import open_events
from parishkit.stewardship.audit.critical_events import SALT, sign
from parishkit.stewardship.audit.models import (
    AuditEvent,
    CriticalEventAcknowledgement,
    OperationalLog,
)
from parishkit.stewardship.observability import Event

from ..policy_factory import address
from .auth_builders import signed_in
from .automation_builders import approve_now, paired, start
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_critical_events_postgresql import critical
from .test_security_events_postgresql import signed_in_as
from .user_builders import add_rules, convert, user_change

pytestmark = pytest.mark.django_db(transaction=True)

ACKNOWLEDGE = "admin_cmd_events_acknowledge"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


def events(kind):
    """How many audit events of ``kind`` are recorded."""
    return AuditEvent.objects.filter(event_type=kind).count()


def as_staff(monkeypatch):
    """Give every admission the session's principal holding only Staff.

    Pairing needs an Administrator. The reads admit through
    ``sessions.authenticated_admin`` and the acknowledgements through the
    pages' ``admin_editing.principal``, which imported it, so both see Staff.
    """
    real = sessions.authenticated_admin

    def staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(sessions, "authenticated_admin", staff)
    monkeypatch.setattr(admin_editing, "authenticated_admin", staff)


def listed(admin, secret):
    """``events list``'s result, asserting success."""
    code, document, _ = admin("events", "list", secret=secret)
    assert code == 0, document
    return document["result"]


def acknowledge(admin, secret, *options, answer=b""):
    """``events acknowledge`` with ``options``; ``answer`` follows on stdin."""
    return admin("events", "acknowledge", *options, secret=secret, answer=answer)


def test_a_security_event_is_listed_and_acknowledged(admin):
    """The session Administrator's open events, as the page reads them; an
    acknowledgement records the page's event and the command's together,
    and a repeat records nothing."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    principal = SimpleNamespace(identity=row.principal_id)
    expected = open_events(principal)
    # The fixture's own rule installations recorded expansions.
    assert expected
    before = events("dashboard_viewed")
    result = listed(admin, reader)
    assert [entry["id"] for entry in result["security"]] == [
        str(entry["id"]) for entry in expected
    ]
    first = result["security"][0]
    assert first["name"] == expected[0]["label"]
    assert first["target"] == expected[0]["target"]
    assert first["actor_id"] == (
        str(expected[0]["actor_id"]) if expected[0]["actor_id"] else None
    )
    # A read records nothing, as Home's acknowledgement panels record none.
    assert events("dashboard_viewed") == before
    assert events(ACKNOWLEDGE) == 0

    code, document, _ = acknowledge(admin, secret, "--security", first["id"])
    assert code == 0, document
    assert document["result"] == {
        "kind": "security",
        "event_id": first["id"],
        "acknowledged": 1,
    }
    acknowledgement = PolicySecurityAcknowledgement.objects.get(event_id=first["id"])
    assert acknowledgement.actor_id == row.principal_id
    [page] = AuditEvent.objects.filter(event_type="security_event_acknowledged")
    assert str(page.subject_id) == first["id"]
    assert page.actor_id == row.principal_id
    [command] = AuditEvent.objects.filter(event_type=ACKNOWLEDGE)
    assert command.actor_id == row.principal_id and command.subject_id == row.pk
    assert command.auditcontext.context == {"outcome": "succeeded"}
    # Neither fresh-gated nor a notice of its own.
    assert events("automation_fresh_gate") == 0
    assert not AutomationNotice.objects.exclude(kind="approved").exists()
    # Gone from this Administrator's list.
    remaining = [entry["id"] for entry in listed(admin, reader)["security"]]
    assert first["id"] not in remaining

    # A repeat records nothing more.
    code, document, _ = acknowledge(admin, secret, "--security", first["id"])
    assert code == 0 and document["result"]["acknowledged"] == 0, document
    assert events(ACKNOWLEDGE) == 1
    assert events("security_event_acknowledged") == 1


def test_an_unknown_security_event_is_not_available(admin):
    """No such event: nothing recorded."""
    _, secret, _ = paired(admin.service)
    code, document, _ = acknowledge(admin, secret, "--security", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert not PolicySecurityAcknowledgement.objects.exists()
    assert events(ACKNOWLEDGE) == 0


def test_the_banner_is_listed_and_exactly_its_rows_acknowledged(admin):
    """The banner's groups and token; the token acknowledges the listed rows
    alone, from standard input too, and a later CRITICAL row stays."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    empty = listed(admin, reader)["critical"]
    assert empty == {
        "count": 0,
        "all_ended": False,
        "since": empty["since"],
        "events": [],
        "token": None,
        "acknowledgeable": 0,
    }
    critical(Event.SOURCE_INVALID)
    critical(Event.SOURCE_INVALID)
    critical(Event.MAIL_PROVIDER_FAILED)
    banner = listed(admin, reader)["critical"]
    assert banner["count"] == 3 and banner["acknowledgeable"] == 3
    assert not banner["all_ended"]
    assert [
        (group["event"], group["name"], group["count"]) for group in banner["events"]
    ] == [
        (Event.SOURCE_INVALID.value, "ParishSoft refresh failed", 2),
        (Event.MAIL_PROVIDER_FAILED.value, "Email sending failed", 1),
    ]
    # Recorded after the list: not in its token, so it stays.
    critical(Event.TASK_FAILED)

    code, document, _ = acknowledge(
        admin, secret, "--critical", "-", answer=banner["token"].encode()
    )
    assert code == 0, document
    assert document["result"] == {
        "kind": "critical",
        "event_id": None,
        "acknowledged": 3,
    }
    assert CriticalEventAcknowledgement.objects.count() == 3
    [page] = AuditEvent.objects.filter(event_type="critical_events_acknowledged")
    assert page.actor_id == row.principal_id
    assert page.auditcontext.context == {"outcome": "succeeded", "count": 3}
    [command] = AuditEvent.objects.filter(event_type=ACKNOWLEDGE)
    assert command.subject_id == row.pk
    later = listed(admin, reader)["critical"]
    assert later["count"] == 1
    assert [group["event"] for group in later["events"]] == [Event.TASK_FAILED.value]

    # The spent token records nothing more.
    code, document, _ = acknowledge(admin, secret, "--critical", banner["token"])
    assert code == 0 and document["result"]["acknowledged"] == 0, document
    assert events(ACKNOWLEDGE) == 1
    assert CriticalEventAcknowledgement.objects.count() == 3
    assert events("critical_events_acknowledged") == 1


@pytest.mark.parametrize("tamper", ["edited", "other_salt", "garbage"])
def test_an_altered_token_is_invalid(admin, tamper):
    """A token the banner did not sign acknowledges nothing, as on the page
    (test_critical_events_postgresql's tampered lists)."""
    _, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    critical(Event.SOURCE_INVALID)
    token = listed(admin, reader)["critical"]["token"]
    row = OperationalLog.objects.get(level="CRITICAL")
    forged = {
        # Swap one character of the signed payload, keeping its signature.
        "edited": ("A" if token[0] != "A" else "B") + token[1:],
        "other_salt": signing.dumps([row.pk.hex], salt=SALT + ".other"),
        "garbage": "not-a-token",
    }[tamper]
    code, document, _ = acknowledge(admin, secret, "--critical", forged)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert not CriticalEventAcknowledgement.objects.exists()
    assert events(ACKNOWLEDGE) == 0
    assert events("critical_events_acknowledged") == 0


def test_a_genuine_token_naming_no_critical_row_acknowledges_nothing(admin):
    """A properly signed list whose row is not CRITICAL is skipped, as the
    page's acknowledge rechecks each row: nothing recorded, the banner kept."""
    _, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    critical(Event.SOURCE_INVALID)
    quiet = OperationalLog.objects.create(
        level="ERROR", event=Event.TASK_FAILED.value, schema="exception", context={}
    )
    code, document, _ = acknowledge(admin, secret, "--critical", sign([quiet.pk]))
    assert code == 0, document
    assert document["result"]["acknowledged"] == 0
    assert not CriticalEventAcknowledgement.objects.exists()
    assert events(ACKNOWLEDGE) == 0
    assert events("critical_events_acknowledged") == 0
    assert listed(admin, reader)["critical"]["count"] == 1


def test_the_granting_administrators_own_acknowledgement_settles_nothing(
    auth_service, google, monkeypatch
):
    """With a second Administrator at activation, the granting actor's
    --security clears the event from the actor's own list only; the other
    recipient's clears it for both, as on the page."""
    admin = runner(auth_service, monkeypatch)
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    convert(store)
    browser, login = signed_in()
    assert login.status_code == 302
    user_change(
        store,
        browser,
        "/admin/users/new/",
        {"email": "new@example.org", "name": "New", "role": "administrator"},
    )
    event = PolicySecurityEvent.objects.get(target="new@example.org")
    assert event.recipients == ["admin@example.org", "second@example.org"]
    granter, code = start(auth_service)
    approve_now(auth_service, code)
    signed_in_as(google, "second@example.org", "second-subject")
    other, code = start(auth_service, email="second@example.org")
    approve_now(auth_service, code, email="second@example.org")

    def shows(secret):
        """Whether the event is on that session Administrator's list."""
        return str(event.pk) in [row["id"] for row in listed(admin, secret)["security"]]

    assert shows(granter) and shows(other)
    code, document, _ = acknowledge(admin, granter, "--security", str(event.pk))
    assert code == 0 and document["result"]["acknowledged"] == 1, document
    assert PolicySecurityAcknowledgement.objects.get(event=event).own
    assert not shows(granter) and shows(other)
    code, document, _ = acknowledge(admin, other, "--security", str(event.pk))
    assert code == 0 and document["result"]["acknowledged"] == 1, document
    assert not shows(granter) and not shows(other)
    assert events(ACKNOWLEDGE) == 2
    assert (
        AuditEvent.objects.filter(
            event_type="security_event_acknowledged", subject_id=event.pk
        ).count()
        == 2
    )


def test_refused_sessions_change_nothing(admin, monkeypatch):
    """A read-only session cannot acknowledge, an ended one is exit 5, and
    Staff is denied the list and both acknowledgements."""
    _, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    critical(Event.SOURCE_INVALID)
    result = listed(admin, reader)
    event_id = result["security"][0]["id"]
    token = result["critical"]["token"]
    for options in (("--security", event_id), ("--critical", token)):
        code, document, _ = acknowledge(admin, reader, *options)
        assert code == 1 and document["error"]["code"] == "denied", document
    _, ended, _ = paired(admin.service)
    revoke(admin.service, ended)
    for argv in (
        ("events", "list"),
        ("events", "acknowledge", "--security", event_id),
        ("events", "acknowledge", "--critical", token),
    ):
        code, document, _ = admin(*argv, secret=ended)
        assert code == 5 and document["error"]["code"] == "session_ended", document
    as_staff(monkeypatch)
    for argv in (
        ("events", "list"),
        ("events", "acknowledge", "--security", event_id),
        ("events", "acknowledge", "--critical", token),
    ):
        code, document, _ = admin(*argv, secret=secret)
        assert code == 1 and document["error"]["code"] == "denied", document
    assert not PolicySecurityAcknowledgement.objects.exists()
    assert not CriticalEventAcknowledgement.objects.exists()
    assert events(ACKNOWLEDGE) == 0
    assert OperationalLog.objects.filter(level="CRITICAL").count() == 1
    assert PolicySecurityEvent.objects.filter(pk=event_id).exists()
