"""``content preview`` and ``content confirm``: catalog entries and pure parts.

The Pages and emails email tables' Delete (#879), split into a preview and a
confirm, and the editors' plain-text preview. These pure tests cover the
catalog entries, the usage errors, the shared choice check
(``content_delete_views.chosen_ids``) and the shared plain-text conversion
(``content_views.plain_text``). The commands against a real database are in
database/test_admin_content_changes_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli, admin_content
from parishkit.stewardship.accounts.content_delete_views import chosen_ids
from parishkit.stewardship.accounts.content_views import plain_text
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
        assert entry["arguments"] == []
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(admin_content.ContentPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert {"--delete", "--html", "--expected-version"} <= set(options)
    assert not options["--expected-version"]["required"]
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_content_confirm"
    assert "admin_cmd_content_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(admin_content.ContentConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


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
    }
    # ``preview.token`` is the signed binding, not a secret, as the other
    # preview commands print it.
    nested = {"id", "slot", "subject", "html", "text", "removed"}
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
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """One mode at a time, the version exactly with a Delete, UUID ids and
    a required token."""
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
