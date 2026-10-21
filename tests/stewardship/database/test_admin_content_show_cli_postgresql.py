"""``content show``: the content editor's read (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The command
reads the editor through the page's own lookup (``editor_content``), so it
opens the same text the page does.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.content_forms import default_values, page_slots
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import CampaignWorkGate
from parishkit.stewardship.jobs.receipt_content import ReceiptTemplate

from ..content_factory import content
from .auth_builders import unguarded
from .automation_builders import as_staff, paired
from .test_admin_content_cli_postgresql import draft
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_content_resave_lists_postgresql import stale_rows
from .test_content_views_postgresql import added, closing_note, plant, plant_stale

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


def show(admin, secret, *argv):
    """``content show ARGV...``: its exit code and document."""
    code, document, _ = admin("content", "show", *argv, secret=secret)
    return code, document


def test_a_saved_page_and_email_open_as_on_the_editor(admin):
    """A read-only session reads a custom page and one saved invitation with
    the editor's fields; nothing is recorded."""
    store = admin.service.store
    campaign, _ = draft(store)
    owner = str(campaign.pk)
    welcome = content(owner, html="<p>Hello {{ parish_name }}</p>")
    invite = content(owner, kind="email", slot="initial", subject="Come and see")
    added(store, campaign, welcome, invite)
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()

    code, document = show(admin, reader, "page", "welcome")
    assert code == 0, document
    assert document["result"] == {
        "campaign_id": owner,
        "version": store.active().digest,
        "kind": "page",
        "slot": "welcome",
        "name": "Family welcome",
        "revision_id": welcome["id"],
        "state": "edited",
        "resave": None,
        "from_default": False,
        # A page has no subject, plain text or banner choice in the editor.
        "content": {
            "subject": None,
            "html": "<p>Hello {{ parish_name }}</p>",
            "text": None,
            "generate_text": None,
            "show_banner": None,
        },
    }

    code, document = show(admin, reader, "email", "initial", "--revision", invite["id"])
    assert code == 0, document
    result = document["result"]
    assert result["name"] == "Initial invitation"
    assert result["revision_id"] == invite["id"]
    assert result["content"] == {
        "subject": "Come and see",
        "html": invite["values"]["html"],
        "text": invite["values"]["text"],
        "generate_text": True,
        # Family emails show the campaign banner unless it is hidden.
        "show_banner": True,
    }
    # The editor's GET records no event, and neither does the command.
    assert AuditEvent.objects.count() == events


def test_a_new_email_is_empty_and_the_default_fills_it(admin):
    """Without ``--revision`` an invitation is the editor's new email;
    ``--default`` prints the default as an editor save stores it."""
    store = admin.service.store
    campaign, _ = draft(store)
    _, secret, _ = paired(admin.service)

    code, document = show(admin, secret, "email", "initial")
    assert code == 0, document
    result = document["result"]
    assert result["revision_id"] is None and result["content"] is None
    assert result["state"] == "empty" and not result["from_default"]

    code, document = show(admin, secret, "email", "initial", "--default")
    assert code == 0, document
    result = document["result"]
    expected = default_values("email", "initial", campaign_id=campaign.pk)
    assert result["from_default"] and result["state"] == "empty"
    assert result["content"]["subject"] == expected["subject"]
    assert result["content"]["html"] == expected["html"]
    assert result["content"]["text"] == expected["text"]
    assert result["content"]["generate_text"] is True


def test_the_confirmation_email_opens_its_single_text(admin):
    """The confirmation email needs no ``--revision``: the editor opens its
    one saved text."""
    store = admin.service.store
    campaign, _ = draft(store)
    receipt = content(
        str(campaign.pk), kind="email", slot="confirmation", subject="Thank you"
    )
    added(store, campaign, receipt)
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "email", "confirmation")
    assert code == 0, document
    result = document["result"]
    assert result["revision_id"] == receipt["id"]
    assert result["content"]["subject"] == "Thank you"


def test_the_confirmation_email_opens_with_a_retired_closing_note(admin, monkeypatch):
    """A retired closing note (#260) opens folded into the saved email, as
    the editor shows it."""
    store = admin.service.store
    campaign, _ = draft(store)
    email = content(str(campaign.pk), kind="email", slot="confirmation")
    note = closing_note(campaign)
    plant(store, monkeypatch, email, note)
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "email", "confirmation")
    assert code == 0, document
    result = document["result"]
    assert result["revision_id"] == email["id"]
    assert result["content"]["subject"] == email["values"]["subject"]
    assert result["content"]["html"] == (
        email["values"]["html"] + note["values"]["html"]
    )
    assert result["content"]["text"].endswith("Call the office.")


def test_a_closing_note_alone_opens_as_the_built_in_email(admin, monkeypatch):
    """With no saved confirmation email, the note opens folded into the
    built-in email: no saved record, but text to show, as on the page."""
    store = admin.service.store
    campaign, _ = draft(store)
    plant(store, monkeypatch, closing_note(campaign))
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "email", "confirmation")
    assert code == 0, document
    result = document["result"]
    assert result["revision_id"] is None
    fallback = ReceiptTemplate()
    assert result["content"]["subject"] == fallback.subject
    assert result["content"]["html"] == fallback.html + "<p>Call the office.</p>"


def test_content_show_names_a_resave_notice(admin, monkeypatch):
    """Saved markup today's cleaning changes carries the editor's notice,
    with the text still as it was saved."""
    store = admin.service.store
    campaign, _ = draft(store)
    page, email = stale_rows(str(campaign.pk))
    plant_stale(store, monkeypatch, page, email)
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "email", "initial", "--revision", email["id"])
    assert code == 0, document
    result = document["result"]
    assert result["resave"]["lost"] == ["{{ family_code }}"]
    assert result["resave"]["blocked"]
    assert result["content"]["html"] == email["values"]["html"]


@pytest.mark.parametrize(
    "argv",
    [
        ("page", "nowhere"),
        ("email", "nowhere"),
        ("email", "initial", "--revision", "random"),
        ("page", "welcome", "--revision", "random"),
    ],
)
def test_an_unknown_slot_or_revision_is_not_available(admin, argv):
    """As the editor answers "not found"."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    argv = [str(uuid4()) if word == "random" else word for word in argv]
    code, document = show(admin, secret, *argv)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


def test_a_page_the_campaign_does_not_show_is_not_available(admin):
    """A page slot for a module the campaign lacks is hidden from the
    editor, so it is not available, as on the page."""
    campaign, _ = draft(admin.service.store)
    assert "financial" not in page_slots(campaign.active_configuration.values)
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "page", "financial")
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


def test_no_current_campaign_is_not_available(admin):
    """Without a campaign there is no editor to open."""
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "page", "welcome")
    assert code == 1 and document["error"]["code"] == "not_available", document


def test_content_show_while_work_holds_the_campaign_is_stale(admin):
    """As the editor refuses while background work holds the campaign."""
    campaign, _ = draft(admin.service.store)
    with unguarded():
        CampaignWorkGate.objects.create(
            campaign=campaign, request_id=uuid4(), initiated_by_id=uuid4()
        )
    _, secret, _ = paired(admin.service)
    code, document = show(admin, secret, "page", "welcome")
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert "result" not in document


def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch):
    """Staff cannot read content, as on the editor: denied, nothing shown."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document = show(admin, secret, "page", "welcome")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("argv", [("page", "welcome"), ("page", "nowhere")])
def test_an_ended_session_reads_nothing(admin, argv):
    """A revoked session is exit 5, even where the read would be refused:
    the session is rechecked before the refusal is reported."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document = show(admin, secret, *argv)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert "result" not in document
