"""``content preview`` and ``content confirm``: catalog entries and pure parts.

The content editor's Preview changes and its review's Apply (#1873) and the
Pages and emails email tables' Delete (#879), each split into a preview and
a confirm, and the editors' plain-text preview. These pure tests cover the
catalog entries, the usage errors, the editor's fields on standard input
(``admin_content.editor_data``), the shared salt
(``content_views.preview_salt``), the shared choice check
(``content_delete_views.chosen_ids``) and the shared plain-text conversion
(``content_views.plain_text``). The commands against a real database are in
database/test_admin_content_changes_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli, admin_content
from parishkit.stewardship.accounts.content_delete_views import chosen_ids
from parishkit.stewardship.accounts.content_views import plain_text, preview_salt
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.web.refusals import UserFacingError

from .test_admin_reads import FORBIDDEN

REVISION = "00000000-0000-4000-8000-000000000073"
OTHER = "00000000-0000-4000-8000-000000000074"
VERSION = "a" * 64


def test_the_catalog_entries():
    """Full scope, as the page's Delete post; no prompt and no recent sign-in.

    The preview records nothing; the confirm records its own described event.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, confirm = entries["content preview"], entries["content confirm"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full" and entry["pr"] == 10
        assert not entry["prompts"] and not entry["fresh_gated"]
    # The editor's fields come only from standard input (``-``).
    assert preview["arguments"] == ["-"]
    assert confirm["arguments"] == []
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(admin_content.ContentPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert {"--edit", "--revision", "--delete", "--html", "--expected-version"} <= set(
        options
    )
    assert not options["--expected-version"]["required"]
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_content_confirm"
    assert "admin_cmd_content_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(admin_content.ContentConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]
    assert not options["--edit"]["required"] and not options["--revision"]["required"]


def test_the_documents_print_only_allowed_names():
    """The preview's members: the chosen emails, the conversion and the token."""
    names = set(admin_content.ContentPreview.field_names())
    assert names == {
        "action",
        "version",
        "campaign_id",
        "deleting",
        "plain_text",
        "preview",
        "edit",
    }
    # ``preview.token`` is the signed binding, not a secret, as the other
    # preview commands print it.
    nested = {"id", "slot", "subject", "html", "text", "removed"} | {
        "kind",
        "revision_id",
        "name",
        "before",
        "after",
        "schedules",
        "due_at",
        "sent",
    }
    for name in names | nested | set(admin_content.ContentConfirm.field_names()):
        assert FORBIDDEN.search(name) is None, name


@pytest.mark.parametrize(
    "argv",
    [
        # Neither mode, or both.
        ["content", "preview"],
        ["content", "preview", "--expected-version", VERSION],
        [
            "content",
            "preview",
            "--delete",
            REVISION,
            "--html",
            "<p>x</p>",
            "--expected-version",
            VERSION,
        ],
        # A Delete needs the version; the plain-text preview takes none.
        ["content", "preview", "--delete", REVISION],
        ["content", "preview", "--html", "<p>x</p>", "--expected-version", VERSION],
        ["content", "preview", "--delete", "nope", "--expected-version", VERSION],
        ["content", "preview", "--delete", REVISION, "--expected-version", "nope"],
        ["content", "preview", "page", "welcome", "--html", "x"],
        ["content", "confirm"],
        ["content", "confirm", "extra", "--token", "-"],
        # An editor change needs the version and its fields on standard
        # input, its kind is page or email, and only it takes --revision.
        ["content", "preview", "--edit", "page", "welcome", "-"],
        [
            "content",
            "preview",
            "--edit",
            "page",
            "welcome",
            "--expected-version",
            VERSION,
        ],
        [
            "content",
            "preview",
            "--edit",
            "letter",
            "initial",
            "--expected-version",
            VERSION,
            "-",
        ],
        [
            "content",
            "preview",
            "--edit",
            "page",
            "welcome",
            "--delete",
            REVISION,
            "--expected-version",
            VERSION,
            "-",
        ],
        [
            "content",
            "preview",
            "--delete",
            REVISION,
            "--expected-version",
            VERSION,
            "-",
        ],
        ["content", "preview", "--html", "-", "-"],
        ["content", "preview", "--html", "x", "--revision", REVISION],
        ["content", "preview", "--edit", "page", "--expected-version", VERSION, "-"],
        ["content", "confirm", "--token", "-", "--revision", REVISION],
        ["content", "confirm", "--edit", "mail", "initial", "--token", "-"],
        ["content", "confirm", "--edit", "email", "initial", "--token", "-", "-"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """One mode at a time, the version exactly with an editor change or a
    Delete, the fields exactly with an editor change, UUID ids and a
    required token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_the_choice_needs_one_email_each_once():
    """The page's own words for no choice and a repeat; ids come back as
    canonical strings."""
    assert chosen_ids([REVISION.upper(), OTHER]) == [REVISION, OTHER]
    with pytest.raises(UserFacingError, match="at least one"):
        chosen_ids([])
    with pytest.raises(UserFacingError, match="more than once"):
        chosen_ids([REVISION, REVISION])


def test_the_plain_text_preview_is_the_pages_conversion():
    """The sanitized HTML, its generated plain text and the removed markup,
    as the editors' live preview answers."""
    answer = plain_text('<p onclick="x()">Hello <b>there</b></p><script>1</script>')
    assert set(answer) == {"html", "text", "removed"}
    assert "onclick" not in answer["html"] and "<script" not in answer["html"]
    assert "Hello" in answer["text"] and "there" in answer["text"]
    assert "onclick attribute" in answer["removed"]
    model = admin_content.plain_text_preview("<p>Hi</p>")
    assert model.action == "plain_text" and model.plain_text == plain_text("<p>Hi</p>")
    assert model.deleting is None and model.preview is None and model.version is None


def fields(source, kind="email", shown=None, banner=None):
    """``editor_data`` for ``source`` (a document, dumped as JSON)."""
    return admin_content.editor_data(json.dumps(source), kind, shown, banner, VERSION)


def problems(source, **kwargs):
    """The ``content.<name>`` fields ``editor_data`` refuses ``source`` at."""
    with pytest.raises(InvalidChange) as refused:
        fields(source, **kwargs)
    return [problem["field"] for problem in refused.value.fields]


SAVED = {"subject": "Hi", "html": "<p>Hi</p>", "text": "Hi"}


def test_absent_fields_keep_the_editors_values():
    """An absent field is the editor's own value as it opens; while the
    text is generated an absent ``text`` is posted empty, so the HTML
    makes it, as the editor fills it."""
    assert fields({}, shown=SAVED) == {
        "base_digest": VERSION,
        "subject": "Hi",
        "html": "<p>Hi</p>",
        "generate_text": True,
        "text": "",
    }
    typed = SAVED | {"text": "Typed by hand"}
    assert fields({"html": "<p>New</p>"}, shown=typed) == {
        "base_digest": VERSION,
        "subject": "Hi",
        "html": "<p>New</p>",
        "generate_text": False,
        "text": "Typed by hand",
    }
    assert fields({"generate_text": False, "text": "T"}, shown=None) == {
        "base_digest": VERSION,
        "subject": "",
        "html": "",
        "generate_text": False,
        "text": "T",
    }
    # A page has neither subject nor plain text; the banner choice is sent
    # wherever it applies, Remove or not.
    assert fields({"html": "<p>P</p>"}, kind="page", shown=SAVED) == {
        "base_digest": VERSION,
        "html": "<p>P</p>",
    }
    assert fields({"clear": True}, shown=SAVED, banner=False) == {
        "base_digest": VERSION,
        "show_banner": False,
        "clear": True,
    }
    assert (
        fields({"show_banner": False}, shown=SAVED, banner=True)["show_banner"] is False
    )


def test_fields_the_editor_does_not_take_are_invalid():
    """Unknown names, fields this editor does not show, wrong types and
    content sent with Remove are each ``invalid`` at its own name."""
    assert problems({"nope": 1}) == ["content.nope"]
    assert problems(
        {"subject": "x", "text": "y", "generate_text": True}, kind="page"
    ) == [
        "content.subject",
        "content.text",
        "content.generate_text",
    ]
    assert problems({"show_banner": True}) == ["content.show_banner"]
    assert problems({"html": 1, "clear": "yes", "generate_text": 0}) == [
        "content.html",
        "content.clear",
        "content.generate_text",
    ]
    assert problems({"clear": True, "html": "<p>x</p>", "subject": "s"}) == [
        "content.html",
        "content.subject",
    ]
    # Nesting too deep for the parser is refused like any other non-object.
    for source in ("[]", "not json", '"text"', "[" * 100_000):
        with pytest.raises(InvalidChange) as refused:
            admin_content.editor_data(source, "email", None, None, VERSION)
        assert [problem["field"] for problem in refused.value.fields] == ["content"]


def test_the_salt_names_one_page_or_email():
    """The editor's and the command's salt: campaign, kind, slot, revision."""
    assert preview_salt("c", "email", "initial", None) == (
        "stewardship-content-preview-v1:c:email:initial:None"
    )
    assert preview_salt("c", "email", "initial", REVISION) != preview_salt(
        "c", "email", "initial", None
    )
