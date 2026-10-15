"""``parish show``, ``ministries list``, ``branding show``, ``campaign shares``.

Pure tests: the golden documents, built through the projections the
commands use from stand-ins for the pages' own reads; the catalog entries;
the ledger; and usage errors. The commands against a real database are in
database/test_admin_settings_cli_postgresql.py.
"""

import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_settings
from parishkit.stewardship.admin_parity import LEDGER

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000081")
SNAPSHOT = UUID("00000000-0000-4000-8000-000000000082")
OPTION = "00000000-0000-4000-8000-000000000083"
VERSION = "b" * 64
# Each command's member names at any depth: exactly what it may print.
# ``phone`` is the parish's public telephone and ``duid`` a Ministry's, never
# a Family's, so each is allowed past the personal-data pattern for its
# command alone.
ALLOWED = {
    "parish show": {
        "version",
        "parish",
        "name",
        "website",
        "timezone",
        "phone",
        "online_giving_url",
        "date_format",
    },
    "ministries list": {
        "version",
        "campaign_id",
        "snapshot_id",
        "ministries",
        "duid",
        "name",
        "included",
    },
    "branding show": {
        "version",
        "logos",
        "slot",
        "id",
        "width",
        "height",
        "size",
        "sha256",
    },
    "campaign shares": {
        "version",
        "campaign_id",
        "editable",
        "financial",
        "options",
        "id",
        "name",
        "free_text",
    },
}
PUBLIC = {"parish show": {"phone"}, "ministries list": {"duid"}}


def check_members(command, document):
    """The document's members are exactly the allowlist, none personal."""
    assert set(members(document)) == ALLOWED[command]
    for name in ALLOWED[command] - PUBLIC.get(command, set()):
        assert FORBIDDEN.search(name) is None, (command, name)


def configuration(document=None, current=None):
    """A stand-in applied configuration at ``VERSION``."""
    return SimpleNamespace(
        current_campaign_id=current,
        active_configuration=SimpleNamespace(
            digest=VERSION, canonical_document=document
        ),
    )


def test_parish_show_is_its_golden_projection():
    """The editor's fields, with the date format's default and no giving URL."""
    document = {
        "sections": {
            "parish": [
                {
                    "id": "parish",
                    "values": {
                        "name": "St. Example",
                        "website": "https://example.org/",
                        "timezone": "America/New_York",
                        "phone": "+12125551234",
                        # Kept for branding, never printed.
                        "large_logo_id": "x",
                    },
                }
            ]
        }
    }
    result = admin_settings.parish_model(configuration(document)).to_document()
    assert result == {
        "version": VERSION,
        "parish": {
            "name": "St. Example",
            "website": "https://example.org/",
            "timezone": "America/New_York",
            "phone": "+12125551234",
            "online_giving_url": None,
            "date_format": "us_long",
        },
    }
    check_members("parish show", result)


def test_ministries_list_is_its_golden_projection():
    """Every catalog row in the page's order; no ``active`` (#1267)."""
    catalog = [
        {"duid": 7, "name": "Choir", "included": True},
        {"duid": 3, "name": "Ushers", "included": False},
    ]
    state = (configuration(current=CAMPAIGN), SimpleNamespace(snapshot_id=SNAPSHOT))
    result = admin_settings.ministry_model((*state, catalog)).to_document()
    assert result == {
        "version": VERSION,
        "campaign_id": str(CAMPAIGN),
        "snapshot_id": str(SNAPSHOT),
        "ministries": catalog,
    }
    check_members("ministries list", result)


def test_campaign_shares_is_its_golden_projection():
    """The options in order, the label as ``name``; editable needs financial."""
    options = [{"id": OPTION, "label": "Prayer", "free_text": False}]

    def campaign(modules):
        """A stand-in campaign with these modules and options."""
        return SimpleNamespace(
            pk=CAMPAIGN,
            active_configuration=SimpleNamespace(
                values={"modules": modules, "share_options": options}
            ),
        )

    result = admin_settings.shares_model(
        configuration(), campaign(["financial"]), True
    ).to_document()
    assert result == {
        "version": VERSION,
        "campaign_id": str(CAMPAIGN),
        "editable": True,
        "financial": True,
        "options": [{"id": OPTION, "name": "Prayer", "free_text": False}],
    }
    check_members("campaign shares", result)
    # The page edits Share options only with the financial module.
    other = admin_settings.shares_model(configuration(), campaign([]), True)
    assert not other.editable and not other.financial


def test_branding_show_is_its_golden_projection():
    """The parish record's logos in slot order; a missing asset is left out,
    as the page draws only the logos whose rows exist."""
    ids = {slot: UUID(int=index + 1) for index, slot in enumerate(("l", "m", "i", "f"))}
    parish = SimpleNamespace(
        large_logo_id=ids["l"],
        menu_logo_id=ids["m"],
        icon_logo_id=ids["i"],
        favicon_id=ids["f"],
    )

    def asset(key, size):
        """A stand-in ``BrandingAsset`` row."""
        return SimpleNamespace(
            pk=ids[key], width=size, height=size, size=size * 10, sha256="c" * 64
        )

    # Deliberately out of slot order, and without the menu logo.
    assets = {row.pk: row for row in (asset("f", 32), asset("l", 512), asset("i", 64))}
    result = admin_settings.branding_model(VERSION, parish, assets).to_document()
    assert result == {
        "version": VERSION,
        "logos": [
            {
                "slot": slot,
                "id": str(ids[key]),
                "width": size,
                "height": size,
                "size": size * 10,
                "sha256": "c" * 64,
            }
            for slot, key, size in (
                ("large", "l", 512),
                ("icon", "i", 64),
                ("favicon", "f", 32),
            )
        ],
    }
    check_members("branding show", result)


@pytest.mark.parametrize(
    "name,model,options",
    [
        ("parish show", admin_settings.ParishShow, set()),
        ("ministries list", admin_settings.MinistryList, set()),
        ("branding show", admin_settings.BrandingShow, set()),
        ("campaign shares", admin_settings.CampaignShares, {"--campaign"}),
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
    assert entry["arguments"] == []
    assert entry["result_fields"] == list(model.field_names())


def test_the_ledger_covers_the_pages():
    """Each page is covered by its read; its changes are still owed.

    Parish settings' changes are covered too (``test_admin_parish``), and
    Ministries changes nothing since #1267, so nothing is owed for it.
    """
    for route, name, owed in (
        ("ministries", "ministries list", None),
        ("branding_settings", "branding show", "PR 10"),
        ("share_settings", "campaign shares", "PR 10"),
    ):
        assert LEDGER[route].kind == "command"
        assert LEDGER[route].detail == (name,)
        assert LEDGER[route].owed == owed


@pytest.mark.parametrize(
    "argv",
    [
        ["parish", "show", "extra"],
        ["ministries", "list", "--campaign", OPTION],
        ["branding", "show", "extra"],
        ["campaign", "shares", "--campaign", "not-a-uuid"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The reads take nothing but ``campaign shares``'s ``--campaign`` UUID."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
