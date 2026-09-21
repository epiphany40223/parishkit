"""Database-free login-rule patches: minimal, provenance-preserving, closed."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.policy_schema import validate_policy_records
from parishkit.stewardship.accounts.user_rules import RuleRefused, rule_patch

from .policy_factory import address, domain

OPERATION = str(uuid4())


def change(records, **values):
    """Build one change with this request's operation identity."""
    return rule_patch(records, operation_id=OPERATION, **values)


def refused(records, **values):
    """The closed reason code a change is refused with, and nothing more."""
    with pytest.raises(RuleRefused) as caught:
        change(records, **values)
    # The refusal carries its code alone: never the submitted target.
    assert str(caught.value) == caught.value.code
    assert values["identity"] not in str(caught.value)
    return caught.value.code


def test_new_rules_are_added_with_manual_provenance_bound_to_the_request():
    """A new address names this request as its creation and every grant's origin."""
    records = [address()]
    added = change(
        records,
        kind="address",
        identity="New.Person@Example.ORG",
        roles=["ministry_leader", "staff"],
        operation="set",
    )
    (item,) = added.patch
    assert item["operation"] == "add" and item["section"] == "login_rules"
    assert item["values"] == {
        "kind": "address",
        "email": "new.person@example.org",
        "roles": ["ministry_leader", "staff"],
        "creation_origin": "manual",
        "creation_operation": OPERATION,
        "grants": {
            "ministry_leader": {"manual": OPERATION},
            "staff": {"manual": OPERATION},
        },
    }
    assert added.before is None and added.after == ["ministry_leader", "staff"]
    assert added.expansion is None
    # An empty role set is an explicit deny, created deliberately.
    deny = change(
        records, kind="address", identity="x@example.org", roles=[], operation="set"
    )
    assert deny.patch[0]["values"]["roles"] == [] and deny.after == []
    created = change(
        records,
        kind="domain",
        identity="Parish.Example",
        roles=["staff"],
        operation="set",
    )
    assert created.patch[0]["values"] == {
        "kind": "domain",
        "domain": "parish.example",
        "roles": ["staff"],
    }
    assert created.expansion == "domain_rule"


def test_role_changes_keep_retained_origins_and_drop_removed_ones():
    """A seeded grant is never rewritten by hand; a removed role loses its origins."""
    seeded = address("chair@example.org", roles=("ministry_leader",), seeded=True)
    records = [address(), seeded, domain("example.org", roles=("staff",))]
    grown = change(
        records,
        kind="address",
        identity="chair@example.org",
        roles=["staff", "ministry_leader"],
        operation="set",
    )
    (item,) = grown.patch
    assert item["operation"] == "update" and item["id"] == seeded["id"]
    assert item["values"]["roles"] == ["ministry_leader", "staff"]
    assert (
        item["values"]["grants"]["ministry_leader"]
        == seeded["values"]["grants"]["ministry_leader"]
    )
    assert item["values"]["grants"]["staff"] == {"manual": OPERATION}
    assert grown.before == ["ministry_leader"] and grown.expansion is None
    shrunk = change(
        records, kind="address", identity="chair@example.org", roles=[], operation="set"
    )
    assert shrunk.patch[0]["values"] == {"roles": [], "grants": {}}
    promoted = change(
        records,
        kind="address",
        identity="chair@example.org",
        roles=["administrator"],
        operation="set",
    )
    assert promoted.expansion == "administrator"
    widened = change(
        records,
        kind="domain",
        identity="example.org",
        roles=["staff", "ministry_leader"],
        operation="set",
    )
    assert widened.patch[0]["values"] == {"roles": ["ministry_leader", "staff"]}
    assert widened.expansion is None
    staffed = change(
        [address(), domain("example.org", roles=("ministry_leader",))],
        kind="domain",
        identity="example.org",
        roles=["ministry_leader", "staff"],
        operation="set",
    )
    assert staffed.expansion == "domain_staff"


def test_removal_is_one_patch_item_and_nothing_else():
    """Removing a rule leaves assignments and other rules untouched."""
    rule = domain("example.org", roles=("staff",))
    removed = change(
        [address(), rule],
        kind="domain",
        identity="EXAMPLE.org",
        roles=[],
        operation="remove",
    )
    assert removed.patch == [
        {"operation": "remove", "section": "login_rules", "id": rule["id"]}
    ]
    assert removed.before == ["staff"] and removed.after is None
    assert removed.expansion is None


def test_refusals_are_closed_reason_codes():
    """Every refusal names a reason, never the submitted target."""
    records = [address(), domain("example.org", roles=("staff",))]
    cases = {
        "consumer": dict(
            kind="domain", identity="GMail.com", roles=["staff"], operation="set"
        ),
        "target": dict(
            kind="address", identity="not an address", roles=[], operation="set"
        ),
        "invalid": dict(kind="group", identity="x", roles=[], operation="set"),
        "domain_roles": dict(
            kind="domain", identity="new.example", roles=[], operation="set"
        ),
        "unchanged": dict(
            kind="domain", identity="example.org", roles=["staff"], operation="set"
        ),
        "missing": dict(
            kind="address", identity="nobody@example.org", roles=[], operation="remove"
        ),
    }
    for code, values in cases.items():
        assert refused(records, **values) == code, code
    # The consumer list is the policy schema's own, so both refuse alike.
    assert (
        refused(
            records,
            kind="domain",
            identity="googlemail.com",
            roles=["staff"],
            operation="set",
        )
        == "consumer"
    )
    for name in ("gmail.com", "googlemail.com"):
        with pytest.raises(ConfigError):
            validate_policy_records([address(), domain(name, roles=("staff",))])
    assert (
        refused(
            records,
            kind="domain",
            identity="example.org",
            roles=["administrator", "staff"],
            operation="set",
        )
        == "domain_roles"
    )
    assert (
        refused(
            records,
            kind="address",
            identity="a@example.org",
            roles=["owner"],
            operation="set",
        )
        == "invalid"
    )
    assert (
        refused(
            records,
            kind="address",
            identity="a@example.org",
            roles=[],
            operation="toggle",
        )
        == "invalid"
    )
