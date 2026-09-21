"""Database-free manual assignment edits: ordinary policy changes the schema admits."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.assignment_edits import (
    AssignmentRefused,
    add_assignment_patch,
    remove_assignment_patch,
)
from parishkit.stewardship.accounts.policy_schema import (
    validate_manual_operation,
    validate_policy_change,
    validate_policy_records,
)

from .policy_factory import address, assignment

OP = str(uuid4())


def applied(records, patch):
    """Apply a patch as the builder does and hold it to every ordinary rule."""
    result = deepcopy(records)
    for item in patch:
        if item["operation"] == "add":
            result.append({"id": item["id"], "values": item["values"]})
        else:
            result.remove(next(row for row in result if row["id"] == item["id"]))
    validate_policy_records(result)
    validate_policy_change(records, result)
    validate_manual_operation(records, result, OP)
    return result


def test_add_binds_a_manual_assignment_to_the_request():
    """The address is normalized, the assignment manual and bound to the request."""
    records = [address(), address("l@example.org", ("ministry_leader",))]
    change = add_assignment_patch(records, "L@Example.org", 4, operation_id=OP)
    assert change.email == "l@example.org" and change.operation == "add"
    after = applied(records, change.patch)
    (row,) = [item for item in after if item["values"]["kind"] == "assignment"]
    assert row["values"] == {
        "kind": "assignment",
        "email": "l@example.org",
        "ministry_duid": 4,
        "source": "manual",
        "operation_id": OP,
    }
    with pytest.raises(AssignmentRefused) as refused:
        add_assignment_patch(after, "l@example.org", 4, operation_id=OP)
    assert refused.value.code == "already"
    # A seed of the same pair does not block the Administrator's own entry.
    seeded = records + [assignment("l@example.org", ministry=4, seeded=True)]
    applied(
        seeded, add_assignment_patch(seeded, "l@example.org", 4, operation_id=OP).patch
    )
    # An address with no rule at all may still be assigned; the page says how
    # the assignment would take effect.
    applied(
        records,
        add_assignment_patch(records, "new@example.org", 4, operation_id=OP).patch,
    )
    with pytest.raises(ConfigError):
        add_assignment_patch(records, "not an address", 4, operation_id=OP)


def test_remove_drops_only_the_manual_assignment():
    """A seed beside the manual assignment stays; a seed alone is not removed here."""
    manual = assignment("l@example.org", ministry=4)
    seed = assignment("l@example.org", ministry=4, seeded=True)
    records = [address(), address("l@example.org", ("ministry_leader",)), manual, seed]
    change = remove_assignment_patch(records, "l@example.org", 4)
    after = applied(records, change.patch)
    assert [item["id"] for item in after if item["values"]["kind"] == "assignment"] == [
        seed["id"]
    ]
    with pytest.raises(AssignmentRefused) as refused:
        remove_assignment_patch(after, "l@example.org", 4)
    assert refused.value.code == "seeded"
    with pytest.raises(AssignmentRefused) as refused:
        remove_assignment_patch(after, "l@example.org", 9)
    assert refused.value.code == "missing"
