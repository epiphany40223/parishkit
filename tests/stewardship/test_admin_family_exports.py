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
    "export directory",
    "export postal",
    "export family-timeline",
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


def test_a_keyring_loads_only_when_it_matches_the_running_web(tmp_path):
    """The loader reads the named file once and compares the web's receipt."""
    import os
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
    from parishkit.stewardship.accounts.key_files import serialize_keyring
    from parishkit.stewardship.accounts.metrics_credentials import (
        credential_receipt,
    )
    from parishkit.stewardship.admin_cli import CredentialMismatch, load_keyrings

    raw = serialize_keyring(CodeMacKeyring([Key("m1", "active", b"c" * 32)]))
    path = tmp_path / "family_code_mac"
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    configuration = SimpleNamespace(secrets={"family_code_mac": path})
    receipts = {"family_code_mac": credential_receipt(raw, "family_code_mac")}
    (ring,) = load_keyrings(configuration, receipts, ("family_code_mac",))
    assert isinstance(ring, CodeMacKeyring)
    # Another receipt (a rotation in progress), or no such mount: exit 2.
    with pytest.raises(CredentialMismatch):
        load_keyrings(configuration, {"family_code_mac": "other"}, ("family_code_mac",))
    with pytest.raises(CredentialMismatch):
        load_keyrings(configuration, receipts, ("general_encryption",))
