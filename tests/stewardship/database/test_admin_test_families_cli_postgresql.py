"""Chosen-Family tests of ``pk-stewardship admin`` (ADM-11 PR 6c).

``test families-preview``, ``test families`` and ``test status`` through
the Send to chosen Families page's own functions, on the restricted web
login, over the ``family_test`` fixture of test_family_mail_test_postgresql.py
(source-backed Families and an invitation used by a schedule). The real
message goes only to the Testing recipient, rendered later by the worker;
these commands only write tickets. A live full-scope session stands in for
the page's fresh sign-in (PR 5a) and leaves the fresh-gate trail (PR 5b).
"""

import json
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.campaign_family_test import SALT
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.family_mail_models import FamilyMailTest
from parishkit.stewardship.jobs.models import TaskRun

from .automation_builders import paired
from .test_admin_test_sample_cli_postgresql import runner
from .test_family_mail_test_postgresql import family_test, review  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_test_families"


@pytest.fixture
def admin(family_test, auth_service, monkeypatch):  # noqa: F811
    """Commands over the authentication service the Family test runs on."""
    return runner(auth_service, monkeypatch)


def revision(family_test):  # noqa: F811
    """The fixture's email revision, from its page path."""
    return family_test[3].rstrip("/").rsplit("/", 1)[-1]


def preview(admin, secret, family_test, *duids, key=None):  # noqa: F811
    """``test families-preview`` for ``duids``."""
    argv = ["test", "families-preview", revision(family_test)]
    for duid in duids:
        argv += ["--family", str(duid)]
    if key is not None:
        argv += ["--request-key", str(key)]
    return admin(*argv, secret=secret)


def send(admin, secret, token, answer=b""):
    """``test families`` with ``answer`` typed at the prompt."""
    return admin("test", "families", "--token", token, secret=secret, answer=answer)


def trail():
    """The command events, fresh-gate events and fresh-gated notices so far."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
    )


def test_chosen_families_are_previewed_then_sent_once(admin, family_test):  # noqa: F811
    """No names; the prompt; one ticket per Family; one trail; no resend."""
    _, secret, row = paired(admin.service)
    key = uuid4()
    code, document, _ = preview(admin, secret, family_test, 1, key=key)
    assert code == 0, document
    result = document["result"]
    assert result["families"] == [
        {"duid": 1, "eligible": True, "eligibility": "eligible"}
    ]
    assert result["credentials_ready"] is True and result["held"] is False
    assert result["request_key"] == str(key)
    # No Family name, no address; a preview records nothing.
    assert "name" not in json.dumps(result["families"])
    # The token is signed, not encrypted: its payload holds no address.
    payload = signing.loads(result["preview"]["token"], salt=SALT)
    assert "@" not in json.dumps(payload) and "@" not in json.dumps(result)
    assert trail() == (0, 0, 0) and not FamilyMailTest.objects.exists()
    token = result["preview"]["token"]
    tasks = TaskRun.objects.count()
    # Refused prompts change nothing: no ticket, task, event or notice.
    for answer in (b"", b"no\n"):
        code, refused, errors = send(admin, secret, token, answer)
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "names and codes will be sent to the Testing recipient" in errors
        assert "1 Families" in errors
        assert not FamilyMailTest.objects.exists() and TaskRun.objects.count() == tasks
        assert trail() == (0, 0, 0)
    code, sent, _ = send(admin, secret, token, b"yes\n")
    assert code == 0, sent
    [ticket] = FamilyMailTest.objects.all()
    assert sent["result"] == {
        "created": True,
        "request_key": str(key),
        "tickets": [
            {
                "id": str(ticket.pk),
                "sequence": 1,
                "state": ticket.state,
                "task_id": str(ticket.task_id),
            }
        ],
    }
    assert ticket.requested_by_id == row.principal_id
    # The session's sign-in stood in for the page's fresh sign-in.
    assert ticket.reauthenticated_at == row.authenticated_at
    assert trail() == (1, 1, 1)
    [event] = AuditEvent.objects.filter(event_type=EVENT)
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    # The same token again, from here or from the page, sends nothing more.
    code, again, _ = admin("test", "families", "--token", token, "--yes", secret=secret)
    assert code == 0 and again["result"]["created"] is False, again
    assert again["result"]["tickets"] == sent["result"]["tickets"]
    harness, browser, path, _ = family_test
    with web_login():
        response = post(
            browser, path, {"action": "confirm", "preview": token, "acknowledge": "on"}
        )
    assert response.status_code == 302, response.content
    assert FamilyMailTest.objects.count() == 1
    assert AuditEvent.objects.filter(event_type=EVENT).count() == 1
    # The status read lists the ticket without its DUID.
    _, reader, _ = paired(admin.service, scope="read-only")
    code, status, _ = admin("test", "status", secret=reader)
    assert code == 0, status
    [item] = status["result"]["tickets"]
    assert item["id"] == str(ticket.pk) and "duid" not in item


def test_a_page_review_is_sent_from_the_command_line(admin, family_test):  # noqa: F811
    """The page's signed review is the command line's token."""
    _, browser, path, _ = family_test
    _, secret, _ = paired(admin.service)
    token = review(browser, path, [1]).context["confirm"]["preview"].value()
    code, document, _ = send(admin, secret, token, b"yes\n")
    assert code == 0 and document["result"]["created"] is True, document
    assert FamilyMailTest.objects.count() == 1


def test_an_ineligible_family_is_refused(admin, family_test):  # noqa: F811
    """An unknown DUID is shown as such and cannot be sent."""
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, family_test, 1, 999999)
    assert code == 0, document
    assert document["result"]["families"][1] == {
        "duid": 999999,
        "eligible": False,
        "eligibility": "unknown",
    }
    token = document["result"]["preview"]["token"]
    code, stale, _ = send(admin, secret, token, b"yes\n")
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert not FamilyMailTest.objects.exists() and trail() == (0, 0, 0)


def test_refusals_change_nothing(admin, family_test):  # noqa: F811
    """Read-only scope, bad input, an altered token and an ended session."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = preview(admin, reader, family_test, 1)
    assert code == 1 and document["error"]["code"] == "denied"
    code, document, _ = preview(admin, secret, family_test, *range(1, 12))
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document, _ = admin(
        "test", "families-preview", str(uuid4()), "--family", "1", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    token = preview(admin, secret, family_test, 1)[1]["result"]["preview"]["token"]
    code, document, _ = send(admin, reader, token, b"yes\n")
    assert code == 1 and document["error"]["code"] == "denied"
    code, document, _ = send(admin, secret, token[:-2] + "xx", b"yes\n")
    assert code == 1 and document["error"]["code"] == "invalid", document
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document, _ = send(admin, secret, token, b"yes\n")
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert not FamilyMailTest.objects.exists() and trail() == (0, 0, 0)


@pytest.mark.parametrize(
    ("change", "expected"),
    [("unscheduled", "denied"), ("gone", "stale_version")],
)
def test_an_email_changed_since_the_preview_is_refused(
    admin,
    family_test,  # noqa: F811
    monkeypatch,
    change,
    expected,
):
    """No schedule uses the email, or its revision left the configuration.

    The send's transaction rolled back and no ticket exists, so the command
    refuses (exit 1) rather than reporting an unknown outcome (exit 6).
    """
    from parishkit.stewardship.accounts import campaign_family_test
    from parishkit.stewardship.accounts.content_models import ContentVersion

    _, secret, _ = paired(admin.service)
    token = preview(admin, secret, family_test, 1)[1]["result"]["preview"]["token"]

    def changed(*args, **kwargs):
        """The page's checks, after the schedule or configuration changed."""
        if change == "unscheduled":
            raise LookupError("This email is not used by a current schedule.")
        raise ContentVersion.DoesNotExist

    monkeypatch.setattr(campaign_family_test, "prepare", changed)
    code, document, _ = send(admin, secret, token, b"yes\n")
    assert code == 1 and document["error"]["code"] == expected, document
    assert not FamilyMailTest.objects.exists() and trail() == (0, 0, 0)
