"""Every Admin route has a command or an exemption (ADM-11 route parity).

The specification's rules for new Admin actions: from ADM-11 PR 3, a new
``admin:`` URL name lands with its ``pk-stewardship admin`` command or with an
exemption recorded in ``admin_parity.LEDGER``. This test fails for a route
with neither, for a ledger entry naming no route, and for a "command" entry
whose command is not in the catalog.
"""

import re

from parishkit.stewardship import admin_cli, admin_parity, urls


def admin_names():
    """Every URL name in the ``admin:`` namespace."""
    return {pattern.name for pattern in urls.admin_patterns}


def test_every_admin_route_has_a_command_or_an_exemption():
    """An unlisted route fails with its name, so the fix is obvious."""
    missing = sorted(admin_names() - set(admin_parity.LEDGER))
    assert not missing, f"Add these admin: routes to admin_parity.LEDGER: {missing}"


def test_the_ledger_names_only_routes_that_exist():
    """A removed or renamed route leaves no stale entry behind."""
    stale = sorted(set(admin_parity.LEDGER) - admin_names())
    assert not stale, f"These ledger entries name no admin: route: {stale}"


def test_covered_routes_name_commands_in_the_catalog():
    """A "command" entry is a real command; a pending one names its PR."""
    catalog = {spec.name for spec in admin_cli.COMMANDS}
    for name, entry in admin_parity.LEDGER.items():
        assert entry.kind in {"command", "pending", "permanent", "deferred"}, name
        assert entry.detail and all(entry.detail), name
        if entry.kind == "command":
            assert set(entry.detail) <= catalog, name
            assert entry.pr is None, name
        elif entry.kind == "pending":
            assert re.fullmatch(r"PR \d+[a-z]?", entry.pr), name
            # None of a pending entry's commands is in the catalog yet; once
            # one is, the entry becomes a "command" entry, with what is
            # still owed.
            assert not set(entry.detail) & catalog, name
        else:
            assert entry.pr is None and entry.owed is None, name
        if entry.owed is not None:
            assert re.fullmatch(r"PR \d+[a-z]?", entry.owed), name


def test_every_command_of_this_release_covers_a_route():
    """No catalog command outside the session commands is left unmapped."""
    covered = {
        command
        for entry in admin_parity.LEDGER.values()
        if entry.kind == "command"
        for command in entry.detail
    }
    session = {"login start", "login wait", "logout", "whoami", "sessions", "commands"}
    assert {spec.name for spec in admin_cli.COMMANDS} - session == covered


def test_every_ledger_route_is_in_the_spec_inventory():
    """The admin-automation spec's Action inventory names every ledger route
    (old redirect addresses aside, which one rule covers), so a new page's
    ledger entry cannot drift from the spec (#843 review)."""
    import re
    from pathlib import Path

    from parishkit.stewardship.admin_urls.legacy import TARGETS

    spec = (
        Path(__file__).resolve().parents[2]
        / "docs/specs/stewardship/admin-automation/spec.md"
    ).read_text(encoding="utf-8")
    inventory = spec[spec.index("## Action inventory") :]
    named = set(re.findall(r"`([a-z][a-z0-9_]*)`", inventory))
    missing = sorted(set(admin_parity.LEDGER) - set(TARGETS) - named)
    assert not missing, f"Name these routes in the spec's Action inventory: {missing}"
