"""The Family-level export commands' input rules (ADM-11 PR 8e).

Pure tests: the ``--filter`` grammar (no search, each filter once), the
options the parser refuses before anything runs, and the catalog entries.
The commands against a real database are in
database/test_admin_family_export_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli, admin_family_exports

FAMILY_EXPORTS = (
    "export financial",
    "export information",
    "export ministry",
    "export ministry-packet",
)


def test_filters_are_the_pages_fields_once_and_never_a_search():
    """NAME=VALUE pairs become form fields; a search or a repeat is refused."""
    assert admin_family_exports.filter_values(None) == {}
    assert admin_family_exports.filter_values(["amount=zero", "sort=name"]) == {
        "amount": "zero",
        "sort": "name",
    }
    # A value may be empty, as a form field can.
    assert admin_family_exports.filter_values(["start="]) == {"start": ""}
    for pairs in (
        ["search=Example"],
        ["page=2"],
        ["amount"],
        ["=zero"],
        ["amount=zero", "amount=nonzero"],
    ):
        with pytest.raises(ValueError):
            admin_family_exports.filter_values(pairs)


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "financial", "--timezone", "UTC"],
        ["export", "financial", "--format", "png", "--timezone", "UTC"],
        ["export", "information", "--format", "csv"],
        ["export", "ministry", "--format", "csv", "--timezone", "UTC", "--search", "x"],
        [
            "export",
            "ministry",
            "--format",
            "csv",
            "--timezone",
            "UTC",
            "--ministry",
            "09",
        ],
        [
            "export",
            "ministry-packet",
            "--format",
            "csv",
            "--timezone",
            "UTC",
            "--filter",
            "history=all",
        ],
    ],
)
def test_options_outside_the_forms_are_usage_errors(argv):
    """A format, zone and canonical DUIDs are required; there is no search."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_each_family_export_is_a_keyed_change_fetched_as_a_file():
    """Full scope, keyed, the export change document, never a stream."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    for name in FAMILY_EXPORTS:
        entry = entries[name]
        assert entry["scope"] == "full" and entry["changes_state"], name
        assert entry["request_key"] and not entry["streams"], name
        assert entry["pr"] == 8, name
        assert entry["audit_event"] == "admin_cmd_" + name.replace(" ", "_").replace(
            "-", "_"
        )
