"""Campaign images commands: ``artwork show`` and the removal commands.

Pure tests: the golden document, built through the projection the command
uses from stand-ins for the page's own reads; the catalog entries; the
ledger; and usage errors. The commands against a real database are in
database/test_admin_artwork_cli_postgresql.py.
"""

import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_artwork, admin_cli
from parishkit.stewardship.accounts.artwork_views import SLOT_LABELS, UNAVAILABLE
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000091")
BANNER = UUID("00000000-0000-4000-8000-000000000092")
CLOSING = UUID("00000000-0000-4000-8000-000000000093")
VERSION = "b" * 64
# The document's member names at any depth: exactly what it may print.
ALLOWED = {
    "version",
    "campaign_id",
    "images",
    "slot",
    "name",
    "unavailable",
    "image",
    "id",
    "width",
    "height",
    "size",
    "sha256",
}


def stand_in(modules):
    """A stand-in current campaign with ``modules`` applied."""
    return SimpleNamespace(
        pk=CAMPAIGN,
        active_configuration=SimpleNamespace(values={"modules": modules}),
    )


def asset(pk, width, height):
    """A stand-in ``BrandingAsset`` row."""
    return SimpleNamespace(
        pk=pk, width=width, height=height, size=width * 10, sha256="c" * 64
    )


def test_artwork_show_is_its_golden_projection():
    """Every slot in the page's order with its label; the current images;
    and, for a census-only campaign, the Financial icon's unavailability."""
    images = {"closing": asset(CLOSING, 64, 64), "banner": asset(BANNER, 600, 200)}
    result = admin_artwork.artwork_model(
        VERSION, stand_in(["census"]), images
    ).to_document()

    def image(item):
        """The printed facts of one stand-in asset."""
        return {
            "id": str(item.pk),
            "width": item.width,
            "height": item.height,
            "size": item.size,
            "sha256": item.sha256,
        }

    assert result == {
        "version": VERSION,
        "campaign_id": str(CAMPAIGN),
        "images": [
            {
                "slot": slot,
                "name": str(SLOT_LABELS[slot]),
                "unavailable": str(UNAVAILABLE["financial"])
                if slot == "financial"
                else None,
                "image": image(images[slot]) if slot in images else None,
            }
            for slot in ("banner", "welcome", "member", "financial", "closing")
        ],
    }
    assert set(members(result)) == ALLOWED
    for name in ALLOWED:
        assert FORBIDDEN.search(name) is None, name


def test_a_campaign_without_member_pages_cannot_take_their_icon():
    """With neither Census nor Ministries, the Member pages' icon is
    unavailable too; the banner and the other icons stay available."""
    result = admin_artwork.artwork_model(VERSION, stand_in([]), {}).to_document()
    reasons = {row["slot"]: row["unavailable"] for row in result["images"]}
    assert reasons == {
        "banner": None,
        "welcome": None,
        "member": str(UNAVAILABLE["member"]),
        "financial": str(UNAVAILABLE["financial"]),
        "closing": None,
    }
    assert all(row["image"] is None for row in result["images"])


def test_the_catalog_entry():
    """Any session, no state change, no event, prompt or recent sign-in."""
    entry = {item["name"]: item for item in admin_cli.catalog()}["artwork show"]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 10
    assert not entry["prompts"] and not entry["fresh_gated"]
    assert {option["name"] for option in entry["options"]} == {
        "--config",
        "--session-stdin",
    }
    assert entry["arguments"] == []
    assert entry["result_fields"] == list(admin_artwork.ArtworkShow.field_names())


def test_the_removal_catalog_entries():
    """Full scope, as the page's requests; no prompt and no recent sign-in.

    The preview records nothing, as the page's review; the removal records
    its own described event. Both name the slot.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, remove = entries["artwork remove-preview"], entries["artwork remove"]
    for entry in (preview, remove):
        assert entry["scope"] == "full" and entry["pr"] == 10
        assert not entry["prompts"] and not entry["fresh_gated"]
        assert entry["arguments"] == ["SLOT"]
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(
        admin_artwork.ArtworkRemovePreview.field_names()
    )
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert remove["changes_state"] and not remove["expected_version"]
    assert remove["audit_event"] == "admin_cmd_artwork_remove"
    assert "admin_cmd_artwork_remove" in DESCRIPTIONS
    assert remove["result_fields"] == list(admin_artwork.ArtworkRemove.field_names())
    options = {option["name"]: option for option in remove["options"]}
    assert options["--token"]["required"]


def test_the_removal_documents_print_only_allowed_names():
    """The preview's members are the read's image facts and the token's."""
    names = set(admin_artwork.ArtworkRemovePreview.field_names()) | {"token"}
    assert names == {
        "version",
        "campaign_id",
        "slot",
        "name",
        "image",
        "preview",
        "token",
    }
    for name in names - {"token"} | set(admin_artwork.ArtworkRemove.field_names()):
        assert FORBIDDEN.search(name) is None, name


def test_the_parser_slots_are_the_pages():
    """The parser's spelled-out slots stay equal to the configuration's."""
    assert admin_cli.ARTWORK_SLOTS == ARTWORK_SLOTS


def test_the_ledger_covers_the_page():
    """Campaign images is covered by its read and its removal; uploads stay
    a permanent exemption."""
    parity = LEDGER["artwork_settings"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == (
        "artwork show",
        "artwork remove-preview",
        "artwork remove",
    )
    assert LEDGER["artwork_upload"].kind == "permanent"


@pytest.mark.parametrize(
    "argv",
    [
        ["artwork", "show", "extra"],
        ["artwork", "show", "--campaign", str(CAMPAIGN)],
        ["artwork", "remove-preview", "banner"],
        ["artwork", "remove-preview", "logo", "--expected-version", VERSION],
        ["artwork", "remove-preview", "--expected-version", VERSION],
        ["artwork", "remove-preview", "banner", "--expected-version", "nope"],
        ["artwork", "remove", "banner"],
        ["artwork", "remove", "--token", "-"],
        ["artwork", "remove", "icon", "--token", "-"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The read takes no argument and no option of its own; the removal
    commands need a known slot and their own required option."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
