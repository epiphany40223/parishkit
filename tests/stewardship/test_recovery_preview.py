"""Operator previews expose the minimal additive policy diff, never a broad edit."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.operator_recovery import recovery_preview

from .policy_factory import address, domain


@pytest.mark.parametrize("roles", [None, (), ("staff",), ("administrator",)])
def test_recovery_preview_normalizes_target_and_preserves_other_roles(roles):
    """An exact deny is explicitly called out instead of looking like no change."""
    rules = [address(), domain()]
    if roles is not None:
        rules.append(address("replacement@example.org", roles))
    sections = {
        "login_rules": rules,
        "parish": [{"values": {"name": "Example Parish"}}],
    }
    version = SimpleNamespace(document=lambda: {"sections": sections}, digest="a" * 64)
    identifier = uuid4()
    value = recovery_preview(version, identifier, "Replacement@Example.org")
    assert value["deployment_id"] == str(identifier)
    assert value["parish_name"] == "Example Parish"
    assert value["target_email"] == "replacement@example.org"
    assert value["before_roles"] == list(roles or ())
    assert value["after_roles"] == sorted(set(roles or ()) | {"administrator"})
    assert value["adds_access_to_explicit_deny"] is (roles == ())
    assert value["already_granted"] is (roles == ("administrator",))
    assert "admin@example.org" in value["current_admin_rules"]
    assert value["provenance"] == "manual"
    assert rules[0]["values"]["roles"] == ["administrator"]


def test_pre_wizard_recovery_preview_does_not_invent_parish_identity():
    """A minimal bootstrap authority has deployment identity but no parish profile."""
    version = SimpleNamespace(
        document=lambda: {"sections": {"login_rules": [address()]}}, digest="b" * 64
    )
    assert (
        recovery_preview(version, uuid4(), "replacement@example.org")["parish_name"]
        is None
    )
