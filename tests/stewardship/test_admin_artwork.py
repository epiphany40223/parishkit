"""``artwork show``: the Campaign images page's read.

Pure tests: the golden document, built through the projection the command
uses from stand-ins for the page's own reads; the catalog entry; the
ledger; and usage errors. The command against a real database is in
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


def test_the_ledger_covers_the_page():
    """Campaign images is covered by its read; ``artwork remove`` is owed."""
    parity = LEDGER["artwork_settings"]
    assert parity.kind == "command" and parity.detail == ("artwork show",)
    assert parity.owed == "PR 10"


@pytest.mark.parametrize(
    "argv",
    [
        ["artwork", "show", "extra"],
        ["artwork", "show", "--campaign", str(CAMPAIGN)],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The read takes no argument and no option of its own."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
