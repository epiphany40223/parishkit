"""``census update-*``: Update ParishSoft's commands (#1755).

Their catalog entries, options, refusal codes and the setting the command
process reads. Their behaviour against a database, under the real roles and
the fake ParishSoft gateway, is in
``database/test_admin_census_update_cli_postgresql.py``.
"""

import ast
import inspect
from uuid import UUID

import pytest

from parishkit.stewardship import admin_census_update, admin_cli
from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.web.refusals import UserFacingUnavailable

PLAN = UUID("00000000-0000-4000-8000-000000000001")
KEY = "00000000-0000-4000-8000-000000000002"
COMMANDS = (
    "census update-check",
    "census update-plan",
    "census update-confirm",
    "census update-rerun",
)


def entries():
    """The catalog's entries for the four commands, by name."""
    return {
        entry["name"]: entry
        for entry in admin_cli.catalog()
        if entry["name"] in COMMANDS
    }


def test_the_catalog_says_what_each_command_does():
    """A passive read for any session; three keyed full-scope writes, of which
    Update is privileged and Update and Run again ask; none watches."""
    found = entries()
    assert set(found) == set(COMMANDS)
    plan = found["census update-plan"]
    assert plan["scope"] == "read_only" and not plan["changes_state"]
    assert plan["audit_event"] == "census_publication_viewed"
    assert not plan["prompts"] and not plan["request_key"]
    for name in ("census update-check", "census update-confirm", "census update-rerun"):
        entry = found[name]
        assert entry["scope"] == "full" and entry["changes_state"], name
        assert entry["request_key"] and not entry["expected_version"], name
        assert entry["audit_event"] == "admin_cmd_" + name.replace(" ", "_").replace(
            "-", "_"
        )
    assert [name for name, entry in found.items() if entry["fresh_gated"]] == [
        "census update-confirm"
    ]
    assert {name for name, entry in found.items() if entry["prompts"]} == {
        "census update-confirm",
        "census update-rerun",
    }
    # A watch is a passive read the wrapper may interrupt; a write is not.
    assert not any(entry["watch"] for entry in found.values())


def parse(*argv):
    """Parse one command line as ``pk-stewardship admin`` does."""
    return admin_cli.build_parser().parse_args(
        [*argv, "--config", "web.yaml", "--session-stdin"]
    )


def test_the_options_are_the_pages_inputs():
    """The plan is optional only for the read; keys are version 4 UUIDs, as
    the page's; nothing takes --watch."""
    assert parse("census", "update-plan").plan_id is None
    assert parse("census", "update-plan", str(PLAN)).plan_id == PLAN
    args = parse("census", "update-confirm", str(PLAN), "--request-key", KEY)
    assert (args.plan_id, str(args.request_key)) == (PLAN, KEY)
    assert parse("census", "update-check").request_key is None
    for argv in (
        ("census", "update-confirm"),
        ("census", "update-rerun"),
        ("census", "update-plan", "not-a-plan"),
        # A version 1 UUID is not a key the page would draw.
        (
            "census",
            "update-check",
            "--request-key",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        ),
        ("census", "update-check", "--watch", "5"),
        ("census", "update-confirm", str(PLAN), "--watch", "5"),
        ("census", "update-plan", "--yes"),
    ):
        with pytest.raises(admin_cli.UsageError):
            parse(*argv)


def gates():
    """Every ``Gate`` the Update ParishSoft page refuses with."""
    return [
        value
        for value in vars(publication).values()
        if isinstance(value, publication.Gate)
    ]


def test_every_page_refusal_has_one_command_code():
    """Each of the page's reasons is classed exactly once, keeps the page's
    own exception (its closed reason for the refusal log) and reaches the
    document's code and reason."""
    classed = admin_census_update.STALE_REASONS | admin_census_update.INVALID_REASONS
    reasons = {gate.reason for gate in gates()}
    assert admin_census_update.STALE_REASONS.isdisjoint(
        admin_census_update.INVALID_REASONS
    )
    assert classed <= reasons
    for gate in gates():
        error = admin_census_update._refused(gate.refusal())
        assert isinstance(error, UserFacingUnavailable) and error.reason == gate.reason
        expected = (
            "stale_version"
            if gate.reason in admin_census_update.STALE_REASONS
            else "invalid"
            if gate.reason in admin_census_update.INVALID_REASONS
            else "not_available"
        )
        assert error.command_code == expected and error.command_reason == gate.reason
        assert (
            admin_cli.classify(error, admitted_process=True, changed=True) == expected
        )
    codes = {
        gate.reason: admin_census_update._refused(gate.refusal()).command_code
        for gate in gates()
    }
    assert codes["publication_off"] == "not_available"
    assert codes["publication_plan_superseded"] == "stale_version"
    assert codes["publication_finished"] == "invalid"


def test_an_unmarked_page_refusal_is_classified_as_before():
    """Only a marked refusal takes its code: the page's exception elsewhere
    stays a stale record, and a stray attribute is never trusted."""
    error = publication.OFF.refusal()
    assert admin_cli.classify(error, admitted_process=True, changed=False) == (
        "stale_version"
    )
    stray = ValueError("x")
    stray.command_code = "outcome_unknown"
    assert admin_cli.classify(stray, admitted_process=True, changed=False) == "invalid"


def test_the_command_process_reads_the_webs_publication_switch():
    """configure_admin_process sets the page's setting from the deployment's
    parishsoft_publication, so the commands obey the web's switch."""
    tree = ast.parse(inspect.getsource(admin_cli.configure_admin_process))
    assigned = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Subscript)
        and getattr(node.targets[0].slice, "value", None)
        == "STEWARDSHIP_PARISHSOFT_PUBLICATION"
    ]
    assert len(assigned) == 1
    value = assigned[0].value
    assert isinstance(value, ast.Attribute) and value.attr == "parishsoft_publication"


def test_the_plan_document_names_ids_counts_and_outcomes_only():
    """The read model's members are the specification's, and nothing that
    could hold a value, a name or a note."""
    assert admin_census_update.UpdatePlan.field_names() == (
        "campaign_id",
        "waiting",
        "check",
        "checking",
        "plan",
        "rows",
        "others",
    )
    assert admin_census_update.UpdateConfirm.field_names() == (
        "created",
        "request_key",
        "plan_id",
        "confirmation",
        "task",
    )
    assert admin_census_update.UpdateCheck.field_names() == (
        "created",
        "request_key",
        "task",
    )
    assert admin_census_update.UpdateRerun.field_names() == (
        "created",
        "request_key",
        "plan_id",
        "task",
    )
