"""No-reminders WorkGroup commands: ``workgroup show``, ``preview``, ``confirm``.

Pure tests: the page's Now panel as closed states, the catalog entries, the
documents' member names, the ledger and usage errors. The commands against
a real database are in database/test_admin_workgroup_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli, admin_workgroup
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.source.workgroups import evidence_for

from .test_admin_reads import FORBIDDEN

VERSION = "b" * 64


@pytest.mark.parametrize(
    ("name", "evidence", "expected"),
    [
        (None, None, ("off", None)),
        ("Staff", None, ("pending", None)),
        (
            "Staff",
            {"name": "Staff", "found": False, "family_duids": []},
            ("missing", None),
        ),
        ("Staff", {"name": "Staff", "found": True, "family_duids": []}, ("found", 0)),
        (
            "Staff",
            {"name": "Staff", "found": True, "family_duids": [7, 9]},
            ("found", 2),
        ),
    ],
)
def test_the_now_state_follows_the_pages_branches(name, evidence, expected):
    """The page's Now panel: off, pending, missing, or found with a count."""
    assert admin_workgroup.now_state(name, evidence) == expected


@pytest.mark.parametrize(
    ("name", "applied", "expected"),
    [
        (None, None, (0, None)),
        ("Staff", None, (0, None)),
        ("Staff", {"name": "Staff", "found": True, "family_duids": [7, 9]}, (2, None)),
        # A rename waiting for a refresh: the previous WorkGroup's Families.
        ("Staff", {"name": "Old", "found": True, "family_duids": [7]}, (1, "Old")),
        # Missing keeps the set from before; it names no WorkGroup.
        ("Staff", {"name": "Staff", "found": False, "family_duids": [7]}, (1, None)),
        ("Staff", {"name": "Typo", "found": False, "family_duids": [7]}, (1, None)),
        # A found but empty previous WorkGroup is still named (by design).
        ("Staff", {"name": "Old", "found": True, "family_duids": []}, (0, "Old")),
    ],
)
def test_the_applied_state_counts_what_reminders_skip_now(name, applied, expected):
    """``excluding`` is the applied set's size; ``excluding_from`` names the
    WorkGroup it was read from only when that is not the configured one."""
    assert admin_workgroup.applied_state(name, applied) == expected


def test_evidence_for_is_current_evidences_rule():
    """Evidence counts as the Now panel's only when read for that name."""
    evidence = {"name": "Staff", "found": True, "family_duids": [7]}
    assert evidence_for("Staff", evidence) is evidence
    assert evidence_for("Other", evidence) is None
    assert evidence_for("Staff", None) is None


def test_the_catalog_entries():
    """The read: any session, nothing recorded. Review and Apply: full scope,
    no prompt or recent sign-in; only the Apply records its own event."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    show = entries["workgroup show"]
    preview = entries["workgroup preview"]
    confirm = entries["workgroup confirm"]
    assert show["scope"] == "read_only" and not show["changes_state"]
    assert show["audit_event"] is None
    assert {option["name"] for option in show["options"]} == {
        "--config",
        "--session-stdin",
    }
    assert show["result_fields"] == list(admin_workgroup.WorkGroupShow.field_names())
    for entry in (show, preview, confirm):
        assert entry["pr"] == 10 and entry["arguments"] == []
        assert not entry["prompts"] and not entry["fresh_gated"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full"
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(
        admin_workgroup.WorkGroupPreview.field_names()
    )
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert {"--name", "--clear"} <= set(options)
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_workgroup_confirm"
    assert "admin_cmd_workgroup_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(
        admin_workgroup.WorkGroupConfirm.field_names()
    )
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


def test_the_documents_print_only_allowed_names():
    """Names, a state and a count: never a Family's identity."""
    assert set(admin_workgroup.WorkGroupShow.field_names()) == {
        "version",
        "campaign_id",
        "name",
        "state",
        "families",
        "excluding",
        "excluding_from",
    }
    assert set(admin_workgroup.WorkGroupPreview.field_names()) == {
        "version",
        "campaign_id",
        "before",
        "after",
        "preview",
    }
    names = (
        set(admin_workgroup.WorkGroupShow.field_names())
        | set(admin_workgroup.WorkGroupPreview.field_names())
        | set(admin_workgroup.WorkGroupConfirm.field_names())
    )
    for name in names:
        assert FORBIDDEN.search(name) is None, name


def test_the_ledger_covers_the_page():
    """The page is covered by its read, Review and Apply; nothing is owed."""
    parity = LEDGER["reminder_workgroup"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == ("workgroup show", "workgroup preview", "workgroup confirm")


@pytest.mark.parametrize(
    "argv",
    [
        ["workgroup", "show", "extra"],
        ["workgroup", "show", "--campaign", "00000000-0000-4000-8000-000000000001"],
        ["workgroup", "preview", "--expected-version", VERSION],
        ["workgroup", "preview", "--name", "Staff"],
        ["workgroup", "preview", "--name", "Staff", "--clear"],
        ["workgroup", "preview", "--name", "Staff", "--expected-version", "nope"],
        ["workgroup", "confirm"],
        ["workgroup", "confirm", "--token", "-", "extra"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The read takes nothing of its own; the preview needs exactly one of
    --name and --clear and a version; the confirm needs its token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
