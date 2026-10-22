"""``content preview`` and ``content confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
build the email tables' Delete through the page's own removal
(``content_delete_views.review_deletion``) and confirm it through the
page's own salt and scope, and preview typed HTML through the editors' own
plain-text conversion, so the page and the command line answer alike.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import parish_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.content_delete_views import delete_salt
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import CampaignWorkGate

from ..content_factory import content
from .auth_builders import signed_in, unguarded
from .automation_builders import as_staff, paired
from .test_admin_content_cli_postgresql import draft
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_artwork_views_postgresql import change_parish
from .test_campaign_views_postgresql import post
from .test_content_views_postgresql import added, saved_emails

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_content_confirm"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def emails(admin):
    """A current draft with a sent email and two unused ones.

    Returns ``(campaign, sent, spare, reminder)``, the emails as content rows.
    """
    store = admin.service.store
    campaign, schedule = draft(store)
    owner = str(campaign.pk)
    sent = content(owner, kind="email", slot="initial", subject="Sent")
    spare = content(owner, kind="email", slot="initial", subject="Spare")
    reminder = content(owner, kind="email", slot="reminder", subject="Spare too")
    added(store, campaign, sent, spare, reminder, schedule=schedule, sent=sent)
    return campaign, sent, spare, reminder


def preview(admin, secret, *identifiers, version=None):
    """Run ``content preview --delete`` for ``identifiers``."""
    version = version or admin.service.store.active().digest
    chosen = [word for value in identifiers for word in ("--delete", value)]
    return admin(
        "content",
        "preview",
        *chosen,
        "--expected-version",
        version,
        secret=secret,
    )


def confirm(admin, secret, token):
    """Run ``content confirm`` with ``token``."""
    return admin("content", "confirm", "--token", token, secret=secret)


def token(admin, secret, *identifiers):
    """A successful preview's token."""
    code, document, _ = preview(admin, secret, *identifiers)
    assert code == 0, document
    return document["result"]["preview"]["token"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events():
    """How many ``content confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``content confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def test_preview_and_confirm_delete_the_chosen_emails(admin, emails):
    """The preview names the chosen emails and changes nothing; the confirm
    records one request and the command's event; once installed both emails
    are gone and the sent one stays; a repeat returns the same request."""
    campaign, sent, spare, reminder = emails
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, spare["id"], reminder["id"])
    assert code == 0, document
    result = document["result"]
    signed = result.pop("preview")["token"]
    assert result == {
        "action": "delete",
        "version": version,
        "campaign_id": str(campaign.pk),
        "deleting": [
            {"id": spare["id"], "slot": "initial", "subject": "Spare"},
            {"id": reminder["id"], "slot": "reminder", "subject": "Spare too"},
        ],
        "plain_text": None,
        "edit": None,
    }
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0

    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install(admin, confirmed)
    left = saved_emails()
    assert sent["id"] in left
    assert spare["id"] not in left and reminder["id"] not in left
    code, again, _ = confirm(admin, secret, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_the_preview_refuses_what_the_page_refuses(admin, emails):
    """A sent email, the confirmation email, an unknown id and a repeat are
    invalid at ``delete`` with the page's words; another version is stale;
    nothing is requested."""
    campaign, sent, spare, _ = emails
    store = admin.service.store
    confirmation = content(str(campaign.pk), kind="email", slot="confirmation")
    added(store, campaign, confirmation)
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    for chosen, words in (
        ((spare["id"], sent["id"]), "used by a mail schedule"),
        ((confirmation["id"],), "no longer one that can be deleted"),
        ((str(uuid4()),), "no longer one that can be deleted"),
        ((spare["id"], spare["id"]), "more than once"),
    ):
        code, document, _ = preview(admin, secret, *chosen)
        assert code == 1, document
        refused(document, "invalid")
        (problem,) = document["error"]["fields"]
        assert problem["field"] == "delete" and words in problem["message"]
    code, document, _ = preview(admin, secret, spare["id"], version="0" * 64)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0
    assert {sent["id"], spare["id"]} <= saved_emails()


def test_a_held_campaign_is_stale(admin, emails):
    """Background work holding the campaign is the page's "can't be edited
    right now"."""
    campaign, _, spare, _ = emails
    with unguarded():
        CampaignWorkGate.objects.create(
            campaign=campaign, request_id=uuid4(), initiated_by_id=uuid4()
        )
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, spare["id"])
    assert code == 1
    refused(document, "stale_version")


def test_an_archived_campaign_is_not_available(admin, emails):
    """An archived current campaign is closed history: the menu's reason,
    permanent, so ``not_available`` rather than "try again later"."""
    from django.db import connection

    _, _, spare, _ = emails
    _, secret, _ = paired(admin.service)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute("UPDATE public.stewardship_campaign SET state='archived'")
    code, document, _ = preview(admin, secret, spare["id"])
    assert code == 1
    refused(document, "not_available")
    assert events() == 0 and spare["id"] in saved_emails()


def test_confirm_refuses_foreign_and_stale_tokens(admin, emails):
    """An altered token or another page's is invalid; another
    Administrator's is denied; one built before the settings changed is
    stale; nothing is requested by the command."""
    campaign, _, spare, _ = emails
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = token(admin, secret, spare["id"])
    code, document, _ = confirm(admin, secret, signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    # Another page's token (the same payload signed for Parish settings).
    payload = signing.loads(signed, salt=delete_salt(campaign.pk))
    code, document, _ = confirm(
        admin, secret, signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    # Another Administrator's build of the same removal.
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=delete_salt(campaign.pk),
        snapshot=payload["snapshot"],
    )
    code, document, _ = confirm(admin, secret, other)
    assert code == 1
    refused(document, "denied")
    change_parish(admin.service.store)
    requests += 1
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
    assert spare["id"] in saved_emails()


def test_without_a_campaign_nothing_is_available(admin):
    """No current campaign: there is nothing to delete or confirm."""
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, str(uuid4()))
    assert code == 1
    refused(document, "not_available")
    code, document, _ = confirm(admin, secret, "x")
    assert code == 1
    refused(document, "not_available")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_the_plain_text_preview_answers_as_the_page(admin):
    """The command's conversion is the editors' live preview's, from an
    option or standard input; it reads no campaign, records nothing and
    renews no session's idle time."""
    browser, _ = signed_in()
    source = (
        '<script>x()</script><p onclick="y()">Hi '
        '<a href="https://example.org/">there</a></p>'
    )
    page = post(browser, "/admin/content/plain-text", {"html": source})
    assert page.status_code == 200, page.content
    _, secret, _ = paired(admin.service)
    before = AuditEvent.objects.count()
    seen = dict(PortalSession.objects.values_list("pk", "last_activity_at"))
    code, document, _ = admin("content", "preview", "--html", source, secret=secret)
    assert code == 0, document
    assert document["result"] == {
        "action": "plain_text",
        "version": None,
        "campaign_id": None,
        "deleting": None,
        "plain_text": page.json(),
        "preview": None,
        "edit": None,
    }
    code, piped, _ = admin(
        "content", "preview", "--html", "-", secret=secret, answer=source.encode()
    )
    assert code == 0 and piped["result"] == document["result"], piped
    # Standard input is converted exactly as typed, as the page converts
    # its post: surrounding whitespace is not trimmed first.
    spaced = "\n  <pre>  x  </pre>  \n"
    page = post(browser, "/admin/content/plain-text", {"html": spaced})
    code, piped, _ = admin(
        "content", "preview", "--html", "-", secret=secret, answer=spaced.encode()
    )
    assert code == 0 and piped["result"]["plain_text"] == page.json(), piped
    assert AuditEvent.objects.count() == before
    # Each command opens its own command session; the ones already open
    # (the browser's and the paired session's) were not renewed.
    rows = PortalSession.objects.filter(pk__in=seen)
    assert dict(rows.values_list("pk", "last_activity_at")) == seen
    code, document, _ = admin(
        "content", "preview", "--html", "x" * (128 * 1024 + 1), secret=secret
    )
    assert code == 1
    refused(document, "invalid")


def test_refused_sessions(admin, emails, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5."""
    _, _, spare, _ = emails
    requests = ConfigurationChangeRequest.objects.count()
    _, reader, _ = paired(admin.service, scope="read-only")
    for code, document, _ in (
        preview(admin, reader, spare["id"]),
        admin("content", "preview", "--html", "<p>x</p>", secret=reader),
        confirm(admin, reader, "x"),
    ):
        assert code == 1
        refused(document, "denied")
    _, secret, _ = paired(admin.service)
    signed = token(admin, secret, spare["id"])
    revoke(admin.service, secret)
    for code, document, _ in (
        preview(admin, secret, spare["id"]),
        confirm(admin, secret, signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(admin, staff, spare["id"]),
        admin("content", "preview", "--html", "<p>x</p>", secret=staff),
        confirm(admin, staff, signed),
        # The role first (#1341): standard input that is not UTF-8, or
        # longer than any input may be, is never read, so it is denied,
        # not invalid.
        admin("content", "confirm", "--token", "-", secret=staff, answer=b"\xff"),
        admin("content", "preview", "--html", "-", secret=staff, answer=b"\xff"),
        admin(
            "content",
            "preview",
            "--html",
            "-",
            secret=staff,
            answer=b"x" * (128 * 1024 + 1),
        ),
    ):
        assert code == 1
        refused(document, "denied")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
