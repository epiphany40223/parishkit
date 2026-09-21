"""Database-free review decisions: ordinary policy changes the schema admits."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.chair_review import (
    ReviewRefused,
    keep_role_patch,
    remove_seed_patch,
    restore_manual_patch,
)
from parishkit.stewardship.accounts.policy_schema import (
    validate_manual_operation,
    validate_policy_change,
    validate_policy_records,
)
from parishkit.stewardship.audit.schemas import ContextKind, sanitize

from .policy_factory import address, assignment

OP = str(uuid4())
# The decision audit's two fields: a closed word and bounded text. Shared with
# the PostgreSQL case that holds the SQL context guard to the same rule.
AUDIT_DECISION_CASES = (
    ({"decision": "keep_role"}, True),
    ({"decision": "restore", "ministry_duid": 4, "review_reason": "x"}, True),
    ({"decision": "remove", "review_reason": "y" * 500}, True),
    *(
        ({"decision": value}, False)
        for value in ("approve", "", None, 1, True, ["restore"], {"a": 1})
    ),
    *(
        ({"review_reason": value}, False)
        for value in ("", "z" * 501, None, 5, False, ["why"], {"why": 1})
    ),
)


@pytest.mark.parametrize("context,valid", AUDIT_DECISION_CASES)
def test_the_decision_audit_admits_only_a_closed_word_and_bounded_text(context, valid):
    """No other decision word, type or length passes the action schema."""
    if valid:
        assert sanitize(ContextKind.ACTION, context) == context
    else:
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, context)


def test_the_decision_fields_belong_to_the_action_schema_alone():
    """Another context kind refuses the fields outright."""
    with pytest.raises(ValueError):
        sanitize(ContextKind.EMAIL, {"review_reason": "x"})
    with pytest.raises(ValueError):
        sanitize(ContextKind.TASK, {"decision": "restore"})


def applied(records, patch):
    """Apply a patch as the builder does and hold it to every ordinary rule."""
    result = deepcopy(records)
    for item in patch:
        if item["operation"] == "add":
            result.append({"id": item["id"], "values": item["values"]})
        elif item["operation"] == "remove":
            result.remove(next(row for row in result if row["id"] == item["id"]))
        else:
            existing = next(row for row in result if row["id"] == item["id"])
            existing["values"].update(item["values"])
    validate_policy_records(result)
    validate_policy_change(records, result)
    validate_manual_operation(records, result, OP)
    return result


def seeded():
    """One confirmed seed: a seeded rule and its seeded assignment."""
    return [
        address(),
        address("c@example.org", ("ministry_leader",), seeded=True),
        assignment("c@example.org", ministry=4, seeded=True),
    ]


def rule(records, email):
    """The exact rule for this address."""
    return next(
        row
        for row in records
        if row["values"]["kind"] == "address" and row["values"]["email"] == email
    )


def test_keep_role_adds_only_a_manual_origin():
    """Provenance gains the Administrator's entry; the role set is unchanged."""
    records = seeded()
    change = keep_role_patch(records, "c@example.org", operation_id=OP)
    assert change.decision == "keep_role" and change.ministry_duid is None
    after = applied(records, change.patch)
    leader = rule(after, "c@example.org")["values"]["grants"]["ministry_leader"]
    assert set(leader) == {"chair-seed", "manual"} and leader["manual"] == OP
    assert rule(after, "c@example.org")["values"]["roles"] == ["ministry_leader"]
    with pytest.raises(ReviewRefused) as refused:
        keep_role_patch(after, "c@example.org", operation_id=OP)
    assert refused.value.code == "already"
    with pytest.raises(ReviewRefused) as refused:
        keep_role_patch(records, "nobody@example.org", operation_id=OP)
    assert refused.value.code == "missing"
    with pytest.raises(ReviewRefused) as refused:
        keep_role_patch([address()], "admin@example.org", operation_id=OP)
    assert refused.value.code == "not_seeded"


def test_restore_replaces_the_seed_with_a_manual_assignment():
    """The seed goes, an Administrator's assignment to the same Ministry comes."""
    records = seeded()
    change = restore_manual_patch(records, "c@example.org", 4, operation_id=OP)
    after = applied(records, change.patch)
    assignments = [
        (row["values"]["ministry_duid"], row["values"]["source"])
        for row in after
        if row["values"]["kind"] == "assignment"
    ]
    assert assignments == [(4, "manual")]
    with pytest.raises(ReviewRefused) as refused:
        restore_manual_patch(after, "c@example.org", 4, operation_id=OP)
    assert refused.value.code == "missing"
    both = seeded() + [assignment("c@example.org", ministry=4)]
    with pytest.raises(ReviewRefused) as refused:
        restore_manual_patch(both, "c@example.org", 4, operation_id=OP)
    assert refused.value.code == "already"


def test_remove_drops_only_the_seed():
    """The rule and its roles stay; only the seeded assignment is removed."""
    records = seeded()
    change = remove_seed_patch(records, "c@example.org", 4)
    after = applied(records, change.patch)
    assert not any(row["values"]["kind"] == "assignment" for row in after)
    assert rule(after, "c@example.org")["values"]["roles"] == ["ministry_leader"]
    with pytest.raises(ReviewRefused) as refused:
        remove_seed_patch(after, "c@example.org", 4)
    assert refused.value.code == "missing"
