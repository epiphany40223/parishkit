"""``pk-admin users email-leaders``: Email Ministry leaders on the command line (#1622).

The real command line on the restricted web login, over #1592's deployments
(``test_leader_emails_postgresql``): ``--preview`` prints the page's review
(each leader's Ministries and open counts, who is skipped and why, the
Testing notice) under the command's own Admin session, audited by count by
the review definer; ``--confirm`` records the page's request through the
same guard, once, with the command's own event; and every refusal the page
and the guard make (another role, a read-only session, paused mail, a
changed or expired review, another Administrator's review, more than 100
addresses) leaves nothing recorded. In Testing, the emails the command
queues go only to the Testing recipient.
"""

import io
import json
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.campaign_mail import recipient_digest
from parishkit.stewardship.accounts.leader_email_views import SALT
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.leader_email_models import (
    LeaderEmailRecipient,
    LeaderEmailRequest,
)

from .automation_builders import as_staff, paired
from .test_admin_status_cli_postgresql import admin, one  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_leader_emails_postgresql import (  # noqa: F401
    PAUSE,
    admin_id,
    begin,
    claim,
    consume,
    mail_ready,
    production,
    schedule,
    seed,
    testing,
    token_payload,
)

pytestmark = pytest.mark.django_db(transaction=True)

COMMAND = ("users", "email-leaders")
EVENT = "admin_cmd_users_email_leaders"


def events(event_type):
    """How many audit events of this type have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def preview(admin, secret, *choice):  # noqa: F811
    """``--preview`` with the given choice; returns (code, document)."""
    return one(admin, *COMMAND, "--preview", *choice, secret=secret)


def confirm(admin, secret, token):  # noqa: F811
    """``--confirm --token --yes``; returns (code, document)."""
    return one(admin, *COMMAND, "--confirm", "--token", token, "--yes", secret=secret)


def forged(**values):
    """A review signed as the page signs one, with these values changed."""
    runtime = SystemConfiguration.objects.get()
    intent = {
        "actor": str(admin_id()),
        "key": str(uuid4()),
        "configuration": str(runtime.active_configuration_id),
        "campaign": str(runtime.current_campaign_id),
        "mode": runtime.mode,
        "recipient": recipient_digest(runtime.testing_recipient),
        "addresses": ["ann@example.org"],
        "facts": "0" * 64,
    }
    return signing.dumps(intent | values, salt=SALT)


def test_the_command_reviews_and_confirms_in_testing(testing, admin):  # noqa: F811
    """The page's review and Apply; Testing mail reaches only its recipient."""
    store, _browser = testing
    _, secret, _ = paired(admin.service)
    runtime = SystemConfiguration.objects.get()
    assert runtime.mode == "testing" and runtime.testing_recipient
    reviewed = events("user_leader_emails_reviewed")
    code, document = preview(admin, secret, "--all")
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME[" ".join(COMMAND)].result_fields)
    assert result["mode"] == "testing"
    assert result["note"].startswith("Testing mode: every email goes to the Testing")
    assert [(item["email"], item["name"]) for item in result["emailed"]] == [
        ("ann@example.org", "Ann, Leader"),
        ("both@example.org", "Both, Leader"),
    ]
    for item in result["emailed"]:
        assert item["ministries"], item
        assert all(
            set(ministry) == {"duid", "name", "join", "leave"}
            for ministry in item["ministries"]
        )
    assert result["skipped"] == [] and result["remaining"] == 0
    assert result["preview"]["token"]
    # The definer audited the read by count; the Testing recipient's address
    # stays on the page, as for the other Testing commands.
    assert events("user_leader_emails_reviewed") == reviewed + 1
    event = AuditEvent.objects.filter(event_type="user_leader_emails_reviewed").latest(
        "created_at"
    )
    context = AuditContext.objects.get(event_id=event.pk).context
    assert context == {"outcome": "succeeded", "count": 2}
    assert runtime.testing_recipient not in json.dumps(document)
    # Nor in the printed token: it is signed, not encrypted, so it binds the
    # Testing recipient by digest only.
    assert runtime.testing_recipient not in token_payload(result["preview"]["token"])
    # The review alone records nothing.
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0

    token = result["preview"]["token"]
    code, document = confirm(admin, secret, token)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert result["created"] is True and result["recipient_count"] == 2
    request = LeaderEmailRequest.objects.get()
    assert str(request.pk) == result["request_id"]
    assert request.actor_id == admin_id() and request.mode == "testing"
    assert request.testing_recipient == runtime.testing_recipient
    assert sorted(request.addresses) == ["ann@example.org", "both@example.org"]
    assert result["mode"] is None and result["emailed"] is None
    assert events(EVENT) == 1
    requested = AuditEvent.objects.get(event_type="user_leader_emails_requested")
    assert AuditContext.objects.get(event_id=requested.pk).context == {
        "outcome": "succeeded",
        "count": 2,
    }
    # No audit row of either event names an address.
    for row in AuditContext.objects.filter(
        event__event_type__in=(EVENT, "user_leader_emails_requested")
    ):
        assert "@" not in json.dumps(row.context)
    # A repeated confirm returns the first request and records nothing more.
    code, document = confirm(admin, secret, token)
    assert code == 0 and document["result"]["created"] is False, document
    assert document["result"]["request_id"] == str(request.pk)
    assert LeaderEmailRequest.objects.count() == 1 and events(EVENT) == 1

    # The background engine prepares one email per leader, each routed to
    # the Testing recipient alone; the leaders' own addresses get nothing.
    (identifier,) = schedule()
    assert consume(identifier, store)
    rows = {
        row.address: row
        for row in LeaderEmailRecipient.objects.select_related("outbox__render")
    }
    assert set(rows) == {"ann@example.org", "both@example.org"}
    for address, row in rows.items():
        message = row.outbox
        assert message.routing == "testing_override" and message.mode == "testing"
        assert message.render.intended_recipients == [address]
        assert message.render.routed_recipients == [runtime.testing_recipient]
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message, store)
            mail, _, _ = begin(message, execution, store)
        assert mail.recipients == (runtime.testing_recipient,)


def test_the_command_skips_who_it_will_not_email(testing, admin):  # noqa: F811
    """Chosen addresses that are no Ministry leader user are skipped and named."""
    _, secret, _ = paired(admin.service)
    code, document = preview(
        admin,
        secret,
        "--email",
        " Ann@Example.org ",
        "--email",
        "admin@example.org",
        "--email",
        "stranger@example.org",
    )
    assert code == 0, document
    result = document["result"]
    assert [item["email"] for item in result["emailed"]] == ["ann@example.org"]
    assert result["skipped"] == [
        {"email": "admin@example.org", "reason": "not_leader"},
        {"email": "stranger@example.org", "reason": "not_leader"},
    ]
    # Nobody left to email: no token to confirm.
    code, document = preview(admin, secret, "--email", "stranger@example.org")
    assert code == 0 and document["result"]["preview"] == {"token": None}
    code, document = preview(admin, secret, "--email", "not an address")
    assert code == 1 and document["error"]["code"] == "invalid", document


@pytest.mark.parametrize(
    "argv",
    [
        ("--preview",),
        ("--preview", "--all", "--email", "ann@example.org"),
        ("--preview", "--all", "--token", "x"),
        ("--confirm",),
        ("--confirm", "--token", "x", "--all"),
        ("--confirm", "--token", "x", "--email", "ann@example.org"),
        # The review asks nothing; --yes answers Apply's prompt alone.
        ("--preview", "--all", "--yes"),
        # Standard input cannot carry both the token and the answer.
        ("--confirm", "--token", "-"),
    ],
)
def test_the_options_are_checked(testing, admin, argv):  # noqa: F811
    """Each mode takes only its own options (exit 2, ``usage``)."""
    _, secret, _ = paired(admin.service)
    code, document = one(admin, *COMMAND, *argv, secret=secret)
    assert code == 2 and document["error"]["code"] == "usage", document
    assert not LeaderEmailRequest.objects.exists()


def test_confirm_prompts_unless_yes(testing, admin):  # noqa: F811
    """``--confirm`` asks first, as send confirm does: no answer (a run
    without a terminal) is ``confirmation_required`` and records nothing; a
    typed ``yes`` applies. The prompt names counts and the mode, never an
    address."""
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, "--all")
    token = document["result"]["preview"]["token"]
    argv = (*COMMAND, "--confirm", "--token", token)
    code, document = one(admin, *argv, secret=secret)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0
    stderr = io.StringIO()
    code, documents = admin(*argv, secret=secret, answer=b"yes\n", stderr=stderr)
    assert code == 0 and documents[-1]["result"]["created"] is True, documents
    prompt = stderr.getvalue()
    assert "Email 2 Ministry leader users how to sign in." in prompt
    assert "Testing mode: every email goes to the Testing recipient" in prompt
    assert "Type yes to continue" in prompt and "@" not in prompt
    assert LeaderEmailRequest.objects.count() == 1 and events(EVENT) == 1


def test_only_a_full_scope_administrator_session_runs_it(
    admin,  # noqa: F811
    testing,  # noqa: F811
    monkeypatch,
):
    """A read-only session and a Staff caller are refused (``denied``)."""
    _, secret, _ = paired(admin.service, scope="read-only")
    code, document = preview(admin, secret, "--all")
    assert code == 1 and document["error"]["code"] == "denied", document
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, "--all")
    token = document["result"]["preview"]["token"]
    reviewed = events("user_leader_emails_reviewed")
    as_staff(monkeypatch)
    for code, document in (
        preview(admin, secret, "--all"),
        confirm(admin, secret, token),
    ):
        assert code == 1 and document["error"]["code"] == "denied", document
    assert events("user_leader_emails_reviewed") == reviewed
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0


def test_paused_mail_refuses_the_review_and_apply(production, admin):  # noqa: F811
    """While Production mail is paused nothing is reviewed or queued."""
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, "--all")
    assert code == 0 and document["result"]["mode"] == "production", document
    assert document["result"]["note"] is None
    token = document["result"]["preview"]["token"]
    seed(PAUSE)
    reviewed = events("user_leader_emails_reviewed")
    for code, document in (
        preview(admin, secret, "--all"),
        confirm(admin, secret, token),
    ):
        assert code == 1 and document["error"]["code"] == "not_available", document
    assert events("user_leader_emails_reviewed") == reviewed
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0


def test_a_changed_or_expired_review_is_stale(
    admin,  # noqa: F811
    testing,  # noqa: F811
    monkeypatch,
):
    """The guard refuses a review the deployment no longer matches."""
    from parishkit.stewardship.web import refusals

    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, "--all")
    token = document["result"]["preview"]["token"]
    with monkeypatch.context() as patch:
        patch.setattr(refusals, "PREVIEW_MAX_AGE", -1)
        code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # A Testing recipient changed since the review: Apply refuses it.
    seed(
        "UPDATE stewardship_system_configuration SET testing_recipient=%s",
        "other-recipient@example.org",
    )
    code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0


def test_forged_and_foreign_reviews_are_refused(testing, admin):  # noqa: F811
    """Another Administrator's, altered and over-limit reviews record nothing."""
    _, secret, _ = paired(admin.service)
    cases = (
        (forged(actor=str(uuid4())), "denied"),
        ("x" + forged(), "invalid"),
        # Past the guard's limit of 100 addresses: the guard refuses it.
        (
            forged(addresses=[f"leader-{n}@example.org" for n in range(101)]),
            "stale_version",
        ),
        # Facts other than those the guard works out: refused after it.
        (forged(), "stale_version"),
    )
    for token, error in cases:
        code, document = confirm(admin, secret, token)
        assert code == 1 and document["error"]["code"] == error, (error, document)
    assert not LeaderEmailRequest.objects.exists() and events(EVENT) == 0
