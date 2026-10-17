"""``content list``, ``content show`` and ``content history``: documents and
catalog entries.

Pure tests: the golden documents, built through the projections the
commands use from stand-ins for the pages' own reads (``catalog_entries``,
``editor_content`` and ``retained_content``), which carry the browser-only
links and element IDs the commands leave out; and the catalog entries. The
commands against a real database are in
database/test_admin_content_cli_postgresql.py and
database/test_admin_content_show_cli_postgresql.py.
"""

import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_content
from parishkit.stewardship.accounts.content_forms import StaleMarkup
from parishkit.stewardship.admin_parity import LEDGER

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000071")
CONFIGURATION = UUID("00000000-0000-4000-8000-000000000072")
REVISION = "00000000-0000-4000-8000-000000000073"
VERSION = "a" * 64
# Each command's member names at any depth: exactly what it may print.
# ``emails`` names the campaign's email templates, never an address, so it
# alone is allowed past the personal-data pattern; ``name`` and ``subject``
# are the pages' and templates' own, never a Family's.
EMAIL_TEMPLATES = {"emails"}
ALLOWED = {
    "content list": {
        "campaign_id",
        "version",
        "pages",
        "emails",
        "slot",
        "name",
        "state",
        "resave",
        "removed",
        "lost",
        "blocked",
        "scheduled",
        "singleton",
        "revisions",
        "id",
        "subject",
        "excerpt",
        "used_by",
        "deletable",
    },
    "content show": {
        "campaign_id",
        "version",
        "kind",
        "slot",
        "name",
        "revision_id",
        "state",
        "resave",
        "removed",
        "lost",
        "blocked",
        "from_default",
        "content",
        "subject",
        "html",
        "text",
        "generate_text",
        "show_banner",
    },
    "content history": {
        "campaign_id",
        "state",
        "configuration_id",
        "entries",
        "revision",
        "id",
        "kind",
        "slot",
        "name",
        "subject",
        "sample",
        "html",
        "text",
    },
}


def check_members(command, document):
    """The document's members are exactly the allowlist, none personal."""
    assert set(members(document)) == ALLOWED[command]
    for name in ALLOWED[command] - EMAIL_TEMPLATES:
        assert FORBIDDEN.search(name) is None, (command, name)


def catalog():
    """``catalog_entries``'s rows: one page, one mail type with two emails."""
    pages = [
        {
            "slot": "welcome",
            "label": "Family welcome",
            "url": "/admin/campaign/content/page/welcome/",
            "state": "custom",
            "resave": StaleMarkup(
                removed=("an onclick attribute",), lost=(), blocked=False
            ),
        }
    ]
    emails = [
        {
            "slot": "reminder",
            "label": "Reminder",
            "form_id": "email-reminder-actions",
            "dialog_id": "email-reminder-delete",
            "heading_id": "email-reminder-title",
            "deletable": True,
            "singleton": False,
            "scheduled": True,
            "url": "/admin/campaign/content/email/reminder/",
            "revisions": [
                {
                    "id": REVISION,
                    "subject": "Please reply",
                    "short_id": None,
                    "name": "Please reply",
                    "excerpt": "Dear {{ family_name }}",
                    "used_by": ["Reminder 1"],
                    "deletable": False,
                    "state": "default",
                    "resave": None,
                    "test_url": "/admin/campaign/content/test/x/",
                    "url": "/admin/campaign/content/email/reminder/x/",
                }
            ],
        },
        {
            "slot": "confirmation",
            "label": "Confirmation email",
            "form_id": "email-confirmation-actions",
            "dialog_id": "email-confirmation-delete",
            "heading_id": "email-confirmation-title",
            "deletable": False,
            "singleton": True,
            "scheduled": False,
            "url": "/admin/campaign/content/email/confirmation/",
            # The built-in email with a retired closing note folded in (#260).
            "revisions": [
                {
                    "subject": "Thank you",
                    "name": "Thank you",
                    "deletable": False,
                    "state": "custom",
                    "resave": None,
                    "test_url": None,
                    "url": "/admin/campaign/content/email/confirmation/",
                }
            ],
        },
    ]
    return pages, emails


def test_content_list_is_its_golden_projection():
    """The page's rows without its links and element IDs; ``label`` is
    ``name``."""
    document = admin_content.catalog_model(CAMPAIGN, VERSION, *catalog()).to_document()
    assert document == {
        "campaign_id": str(CAMPAIGN),
        "version": VERSION,
        "pages": [
            {
                "slot": "welcome",
                "name": "Family welcome",
                "state": "custom",
                "resave": {
                    "removed": ["an onclick attribute"],
                    "lost": [],
                    "blocked": False,
                },
            }
        ],
        "emails": [
            {
                "slot": "reminder",
                "name": "Reminder",
                "scheduled": True,
                "singleton": False,
                "revisions": [
                    {
                        "id": REVISION,
                        "subject": "Please reply",
                        "excerpt": "Dear {{ family_name }}",
                        "used_by": ["Reminder 1"],
                        "deletable": False,
                        "state": "default",
                        "resave": None,
                    }
                ],
            },
            {
                "slot": "confirmation",
                "name": "Confirmation email",
                "scheduled": False,
                "singleton": True,
                "revisions": [
                    {
                        "id": None,
                        "subject": "Thank you",
                        "excerpt": "",
                        "used_by": [],
                        "deletable": False,
                        "state": "custom",
                        "resave": None,
                    }
                ],
            },
        ],
    }
    check_members("content list", document)
    assert "/admin/" not in json.dumps(document)


def editor(values, kind="email", slot="reminder"):
    """``editor_content``'s read of one saved record holding ``values``."""
    record = None if values is None else {"id": REVISION, "values": values}
    return ("Reminder" if kind == "email" else "Family welcome", record, None, values)


def saved(kind="email", slot="reminder", html="<p>Please reply.</p>", text=None):
    """Saved canonical values of one slot; the plain text generated by
    default."""
    return {
        "campaign_id": str(CAMPAIGN),
        "kind": kind,
        "slot": slot,
        "subject": "Please reply" if kind == "email" else None,
        "html": html,
        "text": "Please reply." if text is None else text,
    }


def show(read, kind="email", slot="reminder", **options):
    """``content show``'s document from an editor read."""
    campaign = SimpleNamespace(pk=CAMPAIGN)
    return admin_content.editor_model(
        campaign, VERSION, kind, slot, read, **options
    ).to_document()


def test_content_show_is_its_golden_projection():
    """A saved email: its record, text, the editor's checkboxes; ``label``
    is ``name``."""
    document = show(editor(saved()), banner=True)
    assert document == {
        "campaign_id": str(CAMPAIGN),
        "version": VERSION,
        "kind": "email",
        "slot": "reminder",
        "name": "Reminder",
        "revision_id": REVISION,
        "state": "custom",
        "resave": None,
        "from_default": False,
        "content": {
            "subject": "Please reply",
            "html": "<p>Please reply.</p>",
            "text": "Please reply.",
            "generate_text": True,
            "show_banner": True,
        },
    }


def test_content_show_of_hand_written_text_opens_unchecked():
    """Plain text that is not what the HTML generates keeps the editor's
    "Generate plain text from HTML" unchecked, as on the page."""
    document = show(editor(saved(text="Reply, please.")))
    assert document["content"]["generate_text"] is False
    assert document["content"]["show_banner"] is None


def test_content_show_of_a_page_has_no_email_fields():
    """A page has no subject, plain text or banner choice in the editor."""
    values = saved(kind="page", slot="welcome")
    document = show(editor(values, "page", "welcome"), "page", "welcome")
    assert document["name"] == "Family welcome"
    assert document["content"] == {
        "subject": None,
        "html": "<p>Please reply.</p>",
        "text": None,
        "generate_text": None,
        "show_banner": None,
    }


def test_content_show_of_an_empty_slot_and_its_default():
    """An empty slot (a new email) prints no content; ``--default`` prints
    the default instead, while ``state`` stays the saved text's."""
    document = show(editor(None))
    assert document["revision_id"] is None and document["content"] is None
    assert document["state"] == "empty" and document["resave"] is None
    assert document["from_default"] is False
    document = show(editor(None), default=saved())
    assert document["from_default"] is True
    assert document["state"] == "empty"
    assert document["content"]["html"] == "<p>Please reply.</p>"


def test_content_show_names_a_resave_notice():
    """Saved markup today's cleaning changes carries ``content list``'s
    notice; the document's members are exactly the allowlist."""
    values = saved(html='<p onclick="x()">Please reply.</p>')
    document = show(editor(values))
    resave = document["resave"]
    assert set(resave) == {"removed", "lost", "blocked"} and resave["removed"]
    # With every member present: exactly the allowlist, none personal.
    check_members("content show", document)


def retained(selected=True):
    """``retained_content``'s read: two entries, the page selected or none."""
    entries = [
        {
            "id": REVISION,
            "kind": "email",
            "slot": "initial",
            "label": "Initial invitation",
            "subject": "Come and see",
        },
        {
            "id": "00000000-0000-4000-8000-000000000074",
            "kind": "page",
            "slot": "welcome",
            "label": "Family welcome",
            "subject": None,
        },
    ]
    return {
        "campaign": SimpleNamespace(pk=CAMPAIGN, state="active"),
        "version": SimpleNamespace(pk=CONFIGURATION),
        "entries": entries,
        "selected": {"id": REVISION, "values": {}} if selected else None,
        "sample": {
            "subject": "Come and see",
            "html": "<p>Sample</p>",
            "text": "Sample",
            "extra": "not printed",
        }
        if selected
        else None,
    }


def test_content_history_is_its_golden_projection():
    """Entries with ``name`` for the page's label, and the chosen sample."""
    document = admin_content.history_model(retained()).to_document()
    assert document == {
        "campaign_id": str(CAMPAIGN),
        "state": "active",
        "configuration_id": str(CONFIGURATION),
        "entries": [
            {
                "id": REVISION,
                "kind": "email",
                "slot": "initial",
                "name": "Initial invitation",
                "subject": "Come and see",
            },
            {
                "id": "00000000-0000-4000-8000-000000000074",
                "kind": "page",
                "slot": "welcome",
                "name": "Family welcome",
                "subject": None,
            },
        ],
        "revision": {
            "id": REVISION,
            "kind": "email",
            "slot": "initial",
            "name": "Initial invitation",
            "sample": {
                "subject": "Come and see",
                "html": "<p>Sample</p>",
                "text": "Sample",
            },
        },
    }
    check_members("content history", document)
    assert admin_content.history_model(retained(False)).revision is None


@pytest.mark.parametrize(
    "name,model,options",
    [
        ("content list", admin_content.ContentList, set()),
        (
            "content show",
            admin_content.ContentShow,
            {"--revision", "--default"},
        ),
        ("content history", admin_content.ContentHistory, {"--revision"}),
    ],
)
def test_the_catalog_entries(name, model, options):
    """Any session, no state change, no event, prompt or recent sign-in."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    entry = entries[name]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 10
    assert not entry["prompts"] and not entry["fresh_gated"]
    names = {option["name"] for option in entry["options"]}
    assert names == {"--config", "--session-stdin"} | options
    assert entry["arguments"] == (["KIND", "SLOT"] if name == "content show" else [])
    assert entry["result_fields"] == list(model.field_names())


def test_the_ledger_covers_the_pages():
    """The catalog and both history routes are covered."""
    assert LEDGER["content_catalog"].detail == ("content list",)
    # The editor as it opens; its Review and Apply are still owed.
    for route in ("content_edit", "content_revision"):
        assert LEDGER[route].kind == "command"
        assert LEDGER[route].detail == ("content show",)
        assert LEDGER[route].owed == "PR 10"
    # The email tables' Delete and the editors' plain-text preview (#463).
    assert LEDGER["content_delete"].kind == "command"
    assert LEDGER["content_delete"].detail == ("content preview", "content confirm")
    assert LEDGER["content_plain_text"].kind == "command"
    assert LEDGER["content_plain_text"].detail == ("content preview",)
    for route in ("content_history", "content_history_revision"):
        assert LEDGER[route].kind == "command"
        assert LEDGER[route].detail == ("content history",)


@pytest.mark.parametrize(
    "argv",
    [
        ["content", "list", "extra"],
        ["content", "list", "--revision", REVISION],
        ["content", "history", "--revision", "not-a-uuid"],
        ["content", "history", "extra"],
        ["content", "show"],
        ["content", "show", "page"],
        ["content", "show", "letter", "welcome"],
        ["content", "show", "email", "reminder", "extra"],
        ["content", "show", "email", "reminder", "--revision", "not-a-uuid"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """``content list`` takes nothing; ``content show`` a kind and a slot;
    ``--revision`` is a UUID."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
