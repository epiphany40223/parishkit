"""``test sample-preview`` and ``test sample`` of ``pk-stewardship admin`` (PR 6b).

Process admission is replaced by the test's own Django assembly, as in
test_admin_task_retry_cli_postgresql.py; everything after it is the real
command line on the restricted web login, previewing and sending real
sample test emails through the Preview and test email page's own functions
(the ``campaign_test`` fixture of test_campaign_mail_postgresql.py). The
preview token crosses between the page and the command line both ways.
"""

import contextlib
import io
import json
from uuid import uuid4

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .automation_builders import paired
from .test_admin_status_cli_postgresql import without_web_runtimes
from .test_background_grants_postgresql import task_login
from .test_campaign_mail_postgresql import campaign_test, deliver  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_test_sample"


def runner(service, monkeypatch):
    """Run admin commands in this process over ``service``; ``run`` answers prompts.

    ``run(*argv, secret, answer=b"")`` returns ``(exit code, document,
    standard error)``; ``answer`` follows the preamble on standard input.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )

    @contextlib.contextmanager
    def admitted(configuration):
        """The test's Django and Valkey stand in for the web's own assembly."""
        yield admin_cli.AdminRuntime(
            store=service.store,
            pairing=automation.PairingStore(
                service.limiter.client, service.limiter.namespace
            ),
            public_origin="https://campaign.example.org",
            setup_complete=service.setup_complete,
        )

    monkeypatch.setattr(admin_cli, "ADMISSION", admitted)

    def run(*argv, secret, answer=b""):
        """One command as the wrapper runs it, on the restricted web login."""
        out, err = io.StringIO(), io.StringIO()
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            without_web_runtimes(),
        ):
            code = admin_cli.main(
                [*argv, "--config", "web.yaml", "--session-stdin"],
                stdin=io.BytesIO(
                    f"pk-admin-session/1 {secret} {'a' * 64}\n".encode() + answer
                ),
                stdout=out,
                stderr=err,
            )
        [document] = [json.loads(line) for line in out.getvalue().splitlines()]
        return code, document, err.getvalue()

    run.service = service
    return run


@pytest.fixture
def admin(campaign_test, monkeypatch):  # noqa: F811
    """Commands over the campaign test's service."""
    return runner(campaign_test[0], monkeypatch)


def revision(campaign_test):  # noqa: F811
    """The fixture's email revision, from its page path."""
    return campaign_test[2].rstrip("/").rsplit("/", 1)[-1]


def decoded(token):
    """A token's signed payload, read as anyone holding the token can."""
    from django.core import signing

    from parishkit.stewardship.accounts.campaign_mail import SALT

    return signing.loads(token, salt=SALT)


def events():
    """The command events recorded so far."""
    return list(AuditEvent.objects.filter(event_type=EVENT))


def test_a_sample_is_previewed_then_sent_once(admin, campaign_test):  # noqa: F811
    """The page's review and send: keyed by the token, audited once."""
    _, secret, row = paired(admin.service)
    key = uuid4()
    code, document, _ = admin(
        "test",
        "sample-preview",
        revision(campaign_test),
        "--request-key",
        str(key),
        secret=secret,
    )
    assert code == 0, document
    preview = document["result"]
    assert preview["request_key"] == str(key)
    assert preview["subject"].startswith("[TEST] ")
    assert preview["testing_recipient_set"] is True
    assert (preview["pending"], preview["unknown"], preview["tests"]) == (
        False,
        False,
        [],
    )
    # No address, no message body, and no event for a preview. The token
    # is signed, not encrypted, so it is decoded and checked too.
    assert "@" not in json.dumps(document) and not events()
    assert "test@example.org" not in json.dumps(decoded(preview["preview"]["token"]))
    assert not CampaignMailTest.objects.exists()
    token = preview["preview"]["token"]
    # No unknown outcome: no prompt, even with nothing typed.
    code, sent, errors = admin("test", "sample", "--token", token, secret=secret)
    assert code == 0, sent
    assert "Type yes" not in errors
    test = CampaignMailTest.objects.get()
    assert sent["result"] == {
        "created": True,
        "request_key": str(key),
        "test": {
            "id": str(test.pk),
            "state": test.state,
            "task_id": str(test.task_id),
            "created_at": test.created_at.isoformat(),
        },
    }
    assert test.requested_by_id == row.principal_id
    [event] = events()
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    # The same token again is the same test, from here and from the page.
    code, again, _ = admin("test", "sample", "--token", token, secret=secret)
    assert code == 0 and again["result"]["created"] is False
    assert again["result"]["test"]["id"] == str(test.pk)
    _, browser, path, _ = campaign_test
    with web_login():
        assert post(browser, path, {"preview_token": token}).status_code == 302
    assert CampaignMailTest.objects.count() == 1 and len(events()) == 1
    # A new preview while that test is pending cannot be sent.
    code, fresh, _ = admin(
        "test", "sample-preview", revision(campaign_test), secret=secret
    )
    assert fresh["result"]["pending"] is True
    assert fresh["result"]["tests"][0]["id"] == str(test.pk)
    code, stale, _ = admin(
        "test", "sample", "--token", fresh["result"]["preview"]["token"], secret=secret
    )
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert CampaignMailTest.objects.count() == 1


def test_a_page_preview_is_sent_from_the_command_line(admin, campaign_test):  # noqa: F811
    """The page's signed token is the command line's: one test, no prompt."""
    _, browser, path, _ = campaign_test
    _, secret, _ = paired(admin.service)
    with web_login():
        page = browser.get(path)
        assert page.status_code == 200
        token = page.context["form"]["preview_token"].value()
    code, document, _ = admin("test", "sample", "--token", token, secret=secret)
    assert code == 0 and document["result"]["created"] is True, document
    assert CampaignMailTest.objects.count() == 1 and len(events()) == 1


def test_an_unknown_outcome_asks_for_the_pages_acknowledgement(
    admin,
    campaign_test,  # noqa: F811
    monkeypatch,
):
    """Refused: nothing queued. ``yes``: a second test, as the ticked box."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    _, secret, _ = paired(admin.service)
    first = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    token = first[1]["result"]["preview"]["token"]
    assert admin("test", "sample", "--token", token, secret=secret)[0] == 0
    monkeypatch.setattr(
        tasks, "submit_sample", lambda *args, **kwargs: DeliveryOutcome.UNKNOWN
    )
    assert deliver(campaign_test).state == "delivery_unknown"
    code, preview, _ = admin(
        "test", "sample-preview", revision(campaign_test), secret=secret
    )
    assert code == 0 and preview["result"]["unknown"] is True
    token = preview["result"]["preview"]["token"]
    for answer in (b"", b"no\n"):
        code, refused, errors = admin(
            "test", "sample", "--token", token, secret=secret, answer=answer
        )
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "A previous test may have arrived" in errors
        assert CampaignMailTest.objects.count() == 1 and len(events()) == 1
    code, sent, _ = admin(
        "test", "sample", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0 and sent["result"]["created"] is True, sent
    assert CampaignMailTest.objects.count() == 2 and len(events()) == 2
    # --yes answers the acknowledgement as a typed yes does.
    assert deliver(campaign_test).state == "delivery_unknown"
    preview = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    assert preview[1]["result"]["unknown"] is True
    code, sent, errors = admin(
        "test",
        "sample",
        "--token",
        preview[1]["result"]["preview"]["token"],
        "--yes",
        secret=secret,
    )
    assert code == 0 and sent["result"]["created"] is True, sent
    assert "Type yes" not in errors
    assert CampaignMailTest.objects.count() == 3 and len(events()) == 3
    # --yes answers it too; a token from standard input then needs --yes.
    code, usage, _ = admin(
        "test", "sample", "--token", "-", secret=secret, answer=token.encode()
    )
    assert code == 2 and usage["error"]["code"] == "usage"


def test_one_send_records_the_sessions_activity_once(
    admin,
    campaign_test,  # noqa: F811
    monkeypatch,
):
    """Only the page's own admission writes; the command's checks do not."""
    from parishkit.stewardship.accounts import admin_editing, sessions

    _, secret, _ = paired(admin.service)
    preview = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    token = preview[1]["result"]["preview"]["token"]
    real = sessions.authenticated_admin
    writes = []

    def counted(caller, **options):
        """Count the session reads that record activity."""
        if options.get("activity") and not options.get("read_only"):
            writes.append(True)
        return real(caller, **options)

    # The page's ``principal`` holds its own reference to the function.
    monkeypatch.setattr(sessions, "authenticated_admin", counted)
    monkeypatch.setattr(admin_editing, "authenticated_admin", counted)
    assert admin("test", "sample", "--token", token, secret=secret)[0] == 0
    assert writes == [True]


def test_refusals_change_nothing(admin, campaign_test):  # noqa: F811
    """Read-only scope, an altered token and an ended session."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = admin(
        "test", "sample-preview", revision(campaign_test), secret=reader
    )
    assert code == 1 and document["error"]["code"] == "denied"
    preview = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    token = preview[1]["result"]["preview"]["token"]
    code, document, _ = admin("test", "sample", "--token", token, secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"
    code, document, _ = admin(
        "test", "sample", "--token", token[:-2] + "xx", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document, _ = admin("test", "sample-preview", str(uuid4()), secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document, _ = admin("test", "sample", "--token", token, secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert not CampaignMailTest.objects.exists() and not events()


def test_an_expired_preview_is_stale(admin, campaign_test, monkeypatch):  # noqa: F811
    """Past the page's preview lifetime: exit 1, nothing queued; preview again."""
    from django.core import signing

    _, secret, _ = paired(admin.service)
    preview = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    token = preview[1]["result"]["preview"]["token"]
    now = signing.time.time()
    monkeypatch.setattr(signing.time, "time", lambda: now + 901)
    code, stale, _ = admin("test", "sample", "--token", token, secret=secret)
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert not CampaignMailTest.objects.exists() and not events()


def test_a_revision_gone_since_the_preview_is_stale(
    admin,
    campaign_test,  # noqa: F811
    monkeypatch,
):
    """Its revision left the active configuration: exit 1, not outcome unknown.

    The send's transaction rolled back, so nothing was sent; the operator
    reviews a fresh preview rather than checking for a test that never left.
    """
    from parishkit.stewardship.accounts import campaign_mail
    from parishkit.stewardship.accounts.content_models import ContentVersion

    _, secret, _ = paired(admin.service)
    preview = admin("test", "sample-preview", revision(campaign_test), secret=secret)
    token = preview[1]["result"]["preview"]["token"]

    def gone(*args, **kwargs):
        """The page's lookup, after an activation dropped the revision."""
        raise ContentVersion.DoesNotExist

    monkeypatch.setattr(campaign_mail, "prepare", gone)
    code, stale, _ = admin("test", "sample", "--token", token, secret=secret)
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert not CampaignMailTest.objects.exists() and not events()
