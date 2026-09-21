"""Autosaved role intents become the rule editor's minimal patches."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.policy_schema import validate_policy_records
from parishkit.stewardship.accounts.rule_autosave import autosave_patch
from parishkit.stewardship.accounts.user_rules import RuleRefused

from .policy_factory import address, domain


def applied(records, patch):
    """Apply one patch the way the request builder does, then validate policy."""
    result = [dict(record) for record in records]
    for item in patch:
        if item["operation"] == "add":
            result.append({"id": item["id"], "values": item["values"]})
        elif item["operation"] == "update":
            row = next(record for record in result if record["id"] == item["id"])
            row["values"] = row["values"] | item["values"]
        else:
            result = [record for record in result if record["id"] != item["id"]]
    validate_policy_records(result)
    return result


def test_a_role_is_granted_and_withdrawn_over_the_configured_roles():
    """The intent is applied over the page's rules, never as a toggle."""
    admin = address("admin@example.org", ("administrator",))
    clerk = address("clerk@example.org", ("staff",))
    operation = str(uuid4())
    granted = autosave_patch(
        [admin, clerk],
        kind="address",
        identity="Clerk@Example.org",
        role="ministry_leader",
        checked=True,
        operation_id=operation,
    )
    assert granted.before == ["staff"]
    assert granted.after == ["ministry_leader", "staff"]
    values = granted.patch[0]["values"]
    assert values["grants"]["ministry_leader"] == {"manual": operation}
    assert values["grants"]["staff"] == clerk["values"]["grants"]["staff"]
    after = applied([admin, clerk], granted.patch)
    withdrawn = autosave_patch(
        after,
        kind="address",
        identity="clerk@example.org",
        role="staff",
        checked=False,
        operation_id=operation,
    )
    assert withdrawn.after == ["ministry_leader"]
    assert "staff" not in withdrawn.patch[0]["values"]["grants"]
    applied(after, withdrawn.patch)
    # Withdrawing the last role leaves an explicit deny, which policy admits.
    denied = autosave_patch(
        applied(after, withdrawn.patch),
        kind="address",
        identity="clerk@example.org",
        role="ministry_leader",
        checked=False,
        operation_id=operation,
    )
    assert denied.after == []
    applied(applied(after, withdrawn.patch), denied.patch)


def test_new_targets_unchanged_intents_and_domain_fences_are_decided():
    """Granting creates a rule; withdrawing from none, or no change, is refused."""
    admin = address("admin@example.org", ("administrator",))
    operation = str(uuid4())
    created = autosave_patch(
        [admin],
        kind="domain",
        identity="parish.example",
        role="staff",
        checked=True,
        operation_id=operation,
    )
    assert created.before is None and created.after == ["staff"]
    assert created.patch[0]["operation"] == "add"
    with pytest.raises(RuleRefused, match="missing"):
        autosave_patch(
            [admin],
            kind="domain",
            identity="parish.example",
            role="staff",
            checked=False,
            operation_id=operation,
        )
    with pytest.raises(RuleRefused, match="unchanged"):
        autosave_patch(
            [admin],
            kind="address",
            identity="admin@example.org",
            role="administrator",
            checked=True,
            operation_id=operation,
        )
    rule = domain("parish.example", roles=("staff",))
    with pytest.raises(RuleRefused, match="domain_roles"):
        autosave_patch(
            [admin, rule],
            kind="domain",
            identity="parish.example",
            role="staff",
            checked=False,
            operation_id=operation,
        )
    with pytest.raises(RuleRefused, match="domain_roles"):
        autosave_patch(
            [admin, rule],
            kind="domain",
            identity="parish.example",
            role="administrator",
            checked=True,
            operation_id=operation,
        )
    with pytest.raises(RuleRefused, match="invalid"):
        autosave_patch(
            [admin],
            kind="address",
            identity="admin@example.org",
            role="owner",
            checked=True,
            operation_id=operation,
        )
    with pytest.raises(RuleRefused, match="target"):
        autosave_patch(
            [admin],
            kind="address",
            identity="not an address",
            role="staff",
            checked=True,
            operation_id=operation,
        )
