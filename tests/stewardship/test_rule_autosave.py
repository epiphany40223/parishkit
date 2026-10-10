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
    assert granted.patch[0]["operation"] == "update"
    values = granted.patch[0]["values"]
    assert values["grants"]["ministry_leader"] == {"manual": operation}
    assert values["grants"]["staff"] == clerk["values"]["grants"]["staff"]
    # The same intent builds the same patch, so a resubmitted key binds.
    assert (
        autosave_patch(
            [admin, clerk],
            kind="address",
            identity="clerk@example.org",
            role="ministry_leader",
            checked=True,
            operation_id=operation,
        ).patch
        == granted.patch
    )
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
    later = applied(after, withdrawn.patch)
    # Withdrawing the last role leaves an explicit deny, which policy admits.
    denied = autosave_patch(
        later,
        kind="address",
        identity="clerk@example.org",
        role="ministry_leader",
        checked=False,
        operation_id=operation,
    )
    assert denied.after == []
    applied(later, denied.patch)


def test_missing_targets_unchanged_intents_and_domain_fences_are_refused():
    """Only a rule the page shows autosaves; a deleted one is never recreated."""
    admin = address("admin@example.org", ("administrator",))
    rule = domain("parish.example", roles=("staff",))
    operation = str(uuid4())
    for checked in (True, False):
        with pytest.raises(RuleRefused, match="missing"):
            autosave_patch(
                [admin],
                kind="domain",
                identity="parish.example",
                role="staff",
                checked=checked,
                operation_id=operation,
            )
        with pytest.raises(RuleRefused, match="missing"):
            autosave_patch(
                [admin, rule],
                kind="address",
                identity="new@parish.example",
                role="staff",
                checked=checked,
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
